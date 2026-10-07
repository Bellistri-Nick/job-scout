"""SQLite storage: applications, messages, events, and pipeline state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STAGES = [
    "applied",
    "screening",
    "interviewing",
    "assessment",
    "offer",
    "rejected",
    "withdrawn",
    "closed",
]
OPEN_STAGES = {"applied", "screening", "interviewing", "assessment", "offer"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT,
    stage TEXT NOT NULL DEFAULT 'applied',
    source TEXT,
    sender_domains TEXT NOT NULL DEFAULT '[]',
    applied_at TEXT,
    last_activity_at TEXT,
    next_action TEXT,
    next_action_due TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company_key);
CREATE INDEX IF NOT EXISTS idx_applications_stage ON applications(stage);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER,
    folder TEXT NOT NULL,
    message_id TEXT,
    in_reply_to TEXT,
    "references" TEXT,
    application_id INTEGER REFERENCES applications(id),
    direction TEXT NOT NULL,            -- inbound | outbound
    from_addr TEXT,
    from_name TEXT,
    to_addr TEXT,
    subject TEXT,
    sent_at TEXT,
    body_text TEXT,
    raw_path TEXT,
    classification TEXT,                -- full JSON from Claude
    is_job_related INTEGER,
    message_type TEXT,
    needs_reply INTEGER NOT NULL DEFAULT 0,
    urgency TEXT,
    summary TEXT,
    action_needed TEXT,
    replied_at TEXT,
    alerted_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(folder, uid)
);
CREATE INDEX IF NOT EXISTS idx_messages_message_id ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_messages_app ON messages(application_id);
CREATE INDEX IF NOT EXISTS idx_messages_needs_reply ON messages(needs_reply);

CREATE TABLE IF NOT EXISTS key_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES applications(id),
    message_id INTEGER REFERENCES messages(id),
    date TEXT NOT NULL,                 -- ISO date or datetime
    description TEXT,
    alerted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_key_dates_date ON key_dates(date);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES applications(id),
    kind TEXT NOT NULL,                 -- stage_change | alert | created | note
    detail TEXT,
    to_stage TEXT,                      -- destination stage, for stage_change rows
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive migrations for databases created by an earlier version.

        Runs after the schema script, and the index on events.to_stage is
        created here rather than in SCHEMA: on an existing database the
        CREATE TABLE is a no-op, so the column does not exist yet when the
        schema script runs and the index statement would fail.
        """
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)")}
        if "to_stage" not in cols:
            # Without this, a rejected application forgets how far it ever got,
            # which is exactly what the funnel needs to know.
            self.conn.execute("ALTER TABLE events ADD COLUMN to_stage TEXT")
            self._backfill_to_stage()
            self.conn.commit()
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_to_stage ON events(to_stage)")
        self.conn.commit()

    def _backfill_to_stage(self) -> None:
        """Recover the destination stage from old free-text event details.

        Historic rows read like "applied → interviewing (interview_request)".
        Parsed in Python rather than SQL: the string surgery is legible here and
        was wrong twice in SQL.
        """
        rows = self.conn.execute(
            "SELECT id, kind, detail FROM events WHERE to_stage IS NULL"
        ).fetchall()
        for r in rows:
            detail = r["detail"] or ""
            stage = None
            if r["kind"] == "stage_change":
                for arrow in ("→", "->"):
                    if arrow in detail:
                        stage = detail.split(arrow, 1)[1].split("(", 1)[0].strip()
                        break
            elif r["kind"] == "created":
                # every application is created at 'applied' and advances from there
                stage = "applied"
            if stage in STAGES:
                self.conn.execute("UPDATE events SET to_stage=? WHERE id=?", (stage, r["id"]))

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ state
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ----------------------------------------------------------- applications
    def create_application(
        self,
        company: str,
        company_key: str,
        role: str | None,
        stage: str = "applied",
        source: str | None = None,
        sender_domains: list[str] | None = None,
        applied_at: str | None = None,
    ) -> int:
        ts = now_iso()
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO applications
                   (company, company_key, role, stage, source, sender_domains,
                    applied_at, last_activity_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    company,
                    company_key,
                    role,
                    stage,
                    source,
                    json.dumps(sorted(set(sender_domains or []))),
                    applied_at or ts,
                    applied_at or ts,
                    ts,
                    ts,
                ),
            )
            app_id = int(cur.lastrowid)
            c.execute(
                "INSERT INTO events(application_id, kind, detail, to_stage, at) VALUES (?,?,?,?,?)",
                (app_id, "created", f"{company} — {role or 'unknown role'}", stage, ts),
            )
        return app_id

    def get_application(self, app_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()

    def list_applications(self, open_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM applications"
        if open_only:
            sql += " WHERE stage IN (%s)" % ",".join("?" * len(OPEN_STAGES))
            rows = self.conn.execute(sql + " ORDER BY last_activity_at DESC", tuple(OPEN_STAGES))
        else:
            rows = self.conn.execute(sql + " ORDER BY last_activity_at DESC")
        return rows.fetchall()

    def find_applications_by_company(self, company_key: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM applications WHERE company_key=? ORDER BY last_activity_at DESC",
            (company_key,),
        ).fetchall()

    def find_applications_by_domain(self, domain: str) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM applications WHERE sender_domains LIKE ? ORDER BY last_activity_at DESC",
            (f'%"{domain}"%',),
        ).fetchall()
        return rows

    def update_application(self, app_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE applications SET {cols} WHERE id=?", (*fields.values(), app_id))

    def add_sender_domain(self, app_id: int, domain: str) -> None:
        app = self.get_application(app_id)
        if not app:
            return
        domains = set(json.loads(app["sender_domains"] or "[]"))
        if domain and domain not in domains:
            domains.add(domain)
            self.update_application(app_id, sender_domains=json.dumps(sorted(domains)))

    def set_stage(self, app_id: int, stage: str, reason: str | None = None) -> bool:
        app = self.get_application(app_id)
        if not app or app["stage"] == stage:
            return False
        fields: dict[str, Any] = {"stage": stage}
        if stage not in OPEN_STAGES:
            fields["closed_at"] = now_iso()
        self.update_application(app_id, **fields)
        self.add_event(app_id, "stage_change",
                       f"{app['stage']} → {stage}" + (f" ({reason})" if reason else ""),
                       to_stage=stage)
        return True

    def add_event(self, app_id: int | None, kind: str, detail: str,
                  to_stage: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO events(application_id, kind, detail, to_stage, at) VALUES (?,?,?,?,?)",
                (app_id, kind, detail, to_stage, now_iso()),
            )

    def list_events(self, app_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE application_id=? ORDER BY at", (app_id,)
        ).fetchall()

    # --------------------------------------------------------------- messages
    def message_exists(self, folder: str, uid: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE folder=? AND uid=?", (folder, uid)
        ).fetchone()
        return row is not None

    def clear_uids(self, folder: str) -> int:
        """Forget the UIDs recorded for a folder (after a UIDVALIDITY change).

        NULLs are distinct under SQLite's UNIQUE, so this can't collide, and it
        stops a reissued UID from matching an unrelated old message.
        """
        with self.tx() as c:
            return c.execute("UPDATE messages SET uid=NULL WHERE folder=?", (folder,)).rowcount

    def insert_message(self, **fields: Any) -> int:
        fields.setdefault("created_at", now_iso())
        cols = ", ".join(f'"{k}"' for k in fields)
        marks = ", ".join("?" * len(fields))
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO messages ({cols}) VALUES ({marks})", tuple(fields.values()))
            return int(cur.lastrowid)

    def update_message(self, msg_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f'"{k}"=?' for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE messages SET {cols} WHERE id=?", (*fields.values(), msg_id))

    def get_message(self, msg_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()

    def find_message_by_message_id(self, message_id: str) -> sqlite3.Row | None:
        if not message_id:
            return None
        return self.conn.execute(
            "SELECT * FROM messages WHERE message_id=? LIMIT 1", (message_id,)
        ).fetchone()

    def list_messages_for_application(self, app_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM messages WHERE application_id=? ORDER BY sent_at", (app_id,)
        ).fetchall()

    def list_needs_reply(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT m.*, a.company, a.role, a.stage
               FROM messages m LEFT JOIN applications a ON a.id = m.application_id
               WHERE m.needs_reply=1 AND m.replied_at IS NULL
               ORDER BY CASE m.urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        m.sent_at DESC"""
        ).fetchall()

    def list_unalerted_needs_reply(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT m.*, a.company, a.role
               FROM messages m LEFT JOIN applications a ON a.id = m.application_id
               WHERE m.needs_reply=1 AND m.replied_at IS NULL AND m.alerted_at IS NULL
               ORDER BY m.sent_at"""
        ).fetchall()

    def list_unalerted_alertable(self, alert_types: "frozenset[str] | set[str]") -> list[sqlite3.Row]:
        """Inbound job mail worth interrupting you for, not yet alerted.

        Fires on `needs_reply` OR on a message type that matters regardless of
        whether a reply is required. An offer or an interview invite with a
        booking link needs no reply and is exactly what you want to hear about.

        Also guards two things the old query missed: only inbound mail, and only
        mail the classifier judged job related, so a stray needs_reply on
        personal mail cannot page you.
        """
        types = sorted(alert_types or ())
        placeholders = ",".join("?" * len(types)) or "NULL"
        return self.conn.execute(
            f"""SELECT m.*, a.company, a.role
                FROM messages m LEFT JOIN applications a ON a.id = m.application_id
                WHERE m.alerted_at IS NULL
                  AND m.replied_at IS NULL
                  AND m.direction = 'inbound'
                  AND COALESCE(m.is_job_related, 0) = 1
                  AND (m.needs_reply = 1 OR m.message_type IN ({placeholders}))
                ORDER BY m.sent_at""",
            tuple(types),
        ).fetchall()

    def mark_replied_to(self, message_id: str, replied_at: str) -> int:
        """Mark any inbound message with this Message-ID as replied. Returns rows changed."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE messages SET replied_at=? WHERE message_id=? AND direction='inbound' AND replied_at IS NULL",
                (replied_at, message_id),
            )
            return cur.rowcount

    def recent_messages(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT m.*, a.company, a.role
               FROM messages m LEFT JOIN applications a ON a.id = m.application_id
               ORDER BY m.sent_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    # -------------------------------------------------------------- key dates
    def add_key_date(self, app_id: int | None, msg_id: int | None, date: str, description: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO key_dates(application_id, message_id, date, description, created_at) VALUES (?,?,?,?,?)",
                (app_id, msg_id, date, description, now_iso()),
            )

    def upcoming_key_dates(self, within_hours: int, unalerted_only: bool = True) -> list[sqlite3.Row]:
        sql = """SELECT k.*, a.company, a.role
                 FROM key_dates k LEFT JOIN applications a ON a.id = k.application_id
                 WHERE k.date >= ? AND k.date <= ?"""
        if unalerted_only:
            sql += " AND k.alerted_at IS NULL"
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        return self.conn.execute(
            sql + " ORDER BY k.date",
            (now.date().isoformat(), (now + timedelta(hours=within_hours)).isoformat()),
        ).fetchall()

    def list_key_dates_for_application(self, app_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM key_dates WHERE application_id=? ORDER BY date", (app_id,)
        ).fetchall()

    def mark_key_date_alerted(self, key_date_id: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE key_dates SET alerted_at=? WHERE id=?", (now_iso(), key_date_id))

    # -------------------------------------------------------------- stale
    def stale_applications(self, days: int) -> list[sqlite3.Row]:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        placeholders = ",".join("?" * len(OPEN_STAGES))
        return self.conn.execute(
            f"""SELECT * FROM applications
                WHERE stage IN ({placeholders}) AND last_activity_at < ?
                ORDER BY last_activity_at""",
            (*OPEN_STAGES, cutoff),
        ).fetchall()

    # -------------------------------------------------------------- metrics
    def applications_by_period(self, period: str = "month") -> list[tuple[str, int]]:
        """Applications created per calendar month (or week), oldest first."""
        n = 7 if period == "month" else 10   # 2026-09 vs 2026-09-06
        rows = self.conn.execute(
            f"""SELECT SUBSTR(applied_at, 1, {n}) AS p, COUNT(*) AS n
                FROM applications WHERE applied_at IS NOT NULL
                GROUP BY p ORDER BY p"""
        ).fetchall()
        return [(r["p"], r["n"]) for r in rows]

    def funnel(self) -> dict[str, int]:
        """How many applications got *at least as far as* each stage.

        Two things this has to get right. It reads from events rather than the
        current stage, because an application rejected after two interviews has
        still interviewed and the `stage` column has forgotten that. And it is
        monotonic by construction: an application that jumped straight from
        applied to interviewing counts toward screening too, so the funnel can
        never widen as it descends, which would be nonsense on a chart.
        """
        order = ["applied", "screening", "interviewing", "assessment", "offer"]
        rank = {s: i for i, s in enumerate(order)}

        best: dict[int, int] = {}
        for r in self.conn.execute(
            "SELECT application_id, to_stage FROM events WHERE to_stage IS NOT NULL"
        ):
            i = rank.get(r["to_stage"])
            if i is not None and r["application_id"] is not None:
                aid = int(r["application_id"])
                best[aid] = max(best.get(aid, -1), i)

        out = {s: sum(1 for v in best.values() if v >= i) for s, i in rank.items()}
        for terminal in ("rejected", "withdrawn", "closed"):
            out[terminal] = self.conn.execute(
                "SELECT COUNT(DISTINCT application_id) AS n FROM events WHERE to_stage=?",
                (terminal,),
            ).fetchone()["n"]
        return out

    def outcome_stats(self, ghost_days: int = 21) -> dict[str, Any]:
        """Headline funnel numbers, plus how long a reply actually takes."""
        from datetime import timedelta

        total = self.conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        advanced = {"screening", "interviewing", "assessment", "offer"}
        placeholders = ",".join("?" * len(advanced))

        responded = self.conn.execute(
            f"""SELECT COUNT(DISTINCT application_id) AS n FROM events
                WHERE to_stage IN ({placeholders})""", tuple(advanced)
        ).fetchone()["n"]

        rejected = self.conn.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE stage='rejected'"
        ).fetchone()["n"]

        cutoff = (datetime.now(timezone.utc) - timedelta(days=ghost_days)).isoformat()
        ghosted = self.conn.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE stage='applied' AND last_activity_at < ?",
            (cutoff,),
        ).fetchone()["n"]

        # days from applying to the first sign of life, per application
        gaps: list[float] = []
        for r in self.conn.execute(
            f"""SELECT a.applied_at AS applied, MIN(e.at) AS first_move
                FROM applications a JOIN events e ON e.application_id = a.id
                WHERE e.to_stage IN ({placeholders}) AND a.applied_at IS NOT NULL
                GROUP BY a.id""", tuple(advanced)
        ):
            try:
                d0 = datetime.fromisoformat(r["applied"])
                d1 = datetime.fromisoformat(r["first_move"])
            except (TypeError, ValueError):
                continue
            days = (d1 - d0).total_seconds() / 86400
            if days >= 0:
                gaps.append(days)
        gaps.sort()
        median = round(gaps[len(gaps) // 2], 1) if gaps else None

        return {
            "total": total,
            "responded": responded,
            "rejected": rejected,
            "ghosted": ghosted,
            "open": self.conn.execute(
                "SELECT COUNT(*) AS n FROM applications WHERE stage IN (%s)"
                % ",".join("?" * len(OPEN_STAGES)), tuple(OPEN_STAGES)
            ).fetchone()["n"],
            "response_rate": round(100 * responded / total) if total else 0,
            "median_days_to_response": median,
            "ghost_days": ghost_days,
        }

    def top_sources(self, limit: int = 8) -> list[tuple[str, int]]:
        """Which ATS or job board each application arrived through."""
        rows = self.conn.execute(
            """SELECT COALESCE(NULLIF(a.source,''), 'unknown') AS src, COUNT(*) AS n
               FROM applications a GROUP BY src ORDER BY n DESC, src LIMIT ?""",
            (limit,),
        ).fetchall()
        return [(r["src"], r["n"]) for r in rows]

    # -------------------------------------------------------------- stats
    def stage_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT stage, COUNT(*) AS n FROM applications GROUP BY stage"
        ).fetchall()
        counts = {s: 0 for s in STAGES}
        for r in rows:
            counts[r["stage"]] = r["n"]
        return counts
