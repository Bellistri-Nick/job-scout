"""One polling run: fetch → classify → match → store → alert → mirror.

Run with `python -m jobmail.pipeline` (or via the systemd timer).
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from .alerts import Alerter, format_alert, format_stale, format_upcoming
from .classifier import STAGE_FOR_TYPE, Classification, Classifier, ClaudeClassifier
from .config import Config, use_utf8_io
from .db import Database, now_iso
from .mail import SENT_FOLDER_CANDIDATES, MailClient, ParsedMessage
from .matcher import Matcher, normalise_company
from .obsidian import ObsidianWriter
from .triage import SkillSpec

log = logging.getLogger("jobmail")



class Pipeline:
    def __init__(self, cfg: Config, db: Database, classifier: Classifier, alerter: Alerter | None = None,
                 obsidian: ObsidianWriter | None = None):
        self.cfg = cfg
        self.db = db
        self.classifier = classifier
        self.alerter = alerter or Alerter(cfg)
        self.matcher = Matcher(db)
        self.obsidian = obsidian
        self.touched_apps: set[int] = set()

    # ------------------------------------------------------------ inbound
    def process_inbound(self, msg: ParsedMessage) -> int:
        """Store, classify and link one inbound message. Returns message row id."""
        if self.db.message_exists(msg.folder, msg.uid):
            return -1
        if msg.message_id and self.db.find_message_by_message_id(msg.message_id):
            # same mail under a new UID: the server reissued them (UIDVALIDITY
            # change). Already accounted for, and re-classifying would cost a
            # Claude call and a duplicate alert.
            return -1

        raw_path = self._save_raw(msg)
        msg_id = self.db.insert_message(
            uid=msg.uid, folder=msg.folder, message_id=msg.message_id,
            in_reply_to=msg.in_reply_to, references=" ".join(msg.references),
            direction="inbound", from_addr=msg.from_addr, from_name=msg.from_name,
            to_addr=msg.to_addr, subject=msg.subject, sent_at=msg.sent_at,
            body_text=msg.body_text, raw_path=raw_path,
        )

        try:
            cls = self.classifier.classify(msg)
        except Exception as exc:
            log.exception("Classification failed for UID %s: %s", msg.uid, exc)
            # Fail loud: the skill's fallback asks for a human, so a broken
            # classifier pages you instead of quietly marking mail as handled.
            cls = Classification.from_dict(SkillSpec.load("job_inbox").fallback)
            cls.failed = True
        if cls.failed:
            log.warning("UID %s fell back to manual review: %s", msg.uid,
                        getattr(getattr(self.classifier, "last_result", None), "error", "") or "exception")

        match = self.matcher.match(msg, cls)
        app_id = match.application_id

        self.db.update_message(
            msg_id,
            application_id=app_id,
            classification=cls.to_json(),
            is_job_related=int(cls.is_job_related),
            message_type=cls.message_type,
            needs_reply=int(cls.needs_reply),
            urgency=cls.urgency,
            summary=cls.summary,
            action_needed=cls.action_needed,
        )
        log.info("UID %s [%s] %s | %s | link=%s%s", msg.uid, cls.message_type, cls.company or "-",
                 cls.summary[:80], match.how, " NEEDS REPLY" if cls.needs_reply else "")

        if app_id is None:
            return msg_id

        self.touched_apps.add(app_id)
        app = self.db.get_application(app_id)
        updates: dict = {"last_activity_at": max(msg.sent_at, app["last_activity_at"] or "")}
        if not app["role"] and cls.role:
            updates["role"] = cls.role
        if cls.needs_reply:
            updates["next_action"] = cls.action_needed or "Reply"
            updates["next_action_due"] = cls.key_dates[0]["date"] if cls.key_dates else None
        self.db.update_application(app_id, **updates)
        if msg.from_domain:
            from .matcher import SHARED_DOMAINS
            if msg.from_domain not in SHARED_DOMAINS:
                self.db.add_sender_domain(app_id, msg.from_domain)

        new_stage = STAGE_FOR_TYPE.get(cls.message_type)
        if new_stage and not match.created:
            self._advance_stage(app_id, new_stage, cls.message_type)
        elif new_stage and match.created and new_stage != "applied":
            self.db.set_stage(app_id, new_stage, cls.message_type)

        for kd in cls.key_dates:
            self.db.add_key_date(app_id, msg_id, kd["date"], kd["description"])

        return msg_id

    def _advance_stage(self, app_id: int, new_stage: str, reason: str) -> None:
        """Only move forward (or to a terminal stage); never regress interviewing → applied."""
        from .db import STAGES
        app = self.db.get_application(app_id)
        cur = app["stage"]
        if cur in {"rejected", "withdrawn", "closed"} and new_stage not in {"offer"}:
            return  # a closed app gets no auto-revival except an actual offer
        if new_stage in {"rejected", "offer"} or STAGES.index(new_stage) > STAGES.index(cur):
            self.db.set_stage(app_id, new_stage, reason)

    # ------------------------------------------------------------ outbound
    def process_outbound(self, msg: ParsedMessage) -> None:
        """Record a message you sent; resolve 'needs reply' on what it answers."""
        if self.db.message_exists(msg.folder, msg.uid):
            return
        if msg.message_id and self.db.find_message_by_message_id(msg.message_id):
            return
        app_id = None
        for ref in [msg.in_reply_to, *reversed(msg.references)]:
            prior = self.db.find_message_by_message_id(ref) if ref else None
            if prior:
                app_id = prior["application_id"]
                if self.db.mark_replied_to(ref, msg.sent_at):
                    log.info("Marked %s as replied", ref)
                break
        if app_id is None:
            # fall back: recipient domain seen on an application
            domain = msg.to_addr.rsplit("@", 1)[-1] if "@" in msg.to_addr else ""
            apps = self.db.find_applications_by_domain(domain) if domain else []
            app_id = int(apps[0]["id"]) if apps else None
        self.db.insert_message(
            uid=msg.uid, folder=msg.folder, message_id=msg.message_id, in_reply_to=msg.in_reply_to,
            references=" ".join(msg.references), application_id=app_id, direction="outbound",
            from_addr=msg.from_addr, from_name=msg.from_name, to_addr=msg.to_addr,
            subject=msg.subject, sent_at=msg.sent_at, body_text=msg.body_text,
            raw_path=self._save_raw(msg), is_job_related=1 if app_id else None,
            message_type="outbound", needs_reply=0,
        )
        if app_id:
            self.touched_apps.add(int(app_id))
            self.db.update_application(int(app_id), last_activity_at=msg.sent_at, next_action=None, next_action_due=None)

    # ------------------------------------------------------------ alerts
    def send_alerts(self) -> None:
        for m in self.db.list_unalerted_alertable(self.cfg.alert_on_types):
            subj, plain, html = format_alert(m)
            self.alerter.send(subj, plain, html)
            self.db.update_message(int(m["id"]), alerted_at=now_iso())
            if m["application_id"]:
                self.db.add_event(int(m["application_id"]), "alert", f"reply-needed alert sent: {m['subject']}")

        for kd in self.db.upcoming_key_dates(self.cfg.deadline_alert_hours):
            subj, plain, html = format_upcoming(kd["company"] or "", kd["role"] or "", kd["date"], kd["description"] or "")
            self.alerter.send(subj, plain, html)
            self.db.mark_key_date_alerted(int(kd["id"]))

        # stale check: at most once per day
        today = datetime.now(timezone.utc).date().isoformat()
        if self.db.get_state("last_stale_check") != today:
            stale = self.db.stale_applications(self.cfg.stale_after_days)
            if stale:
                subj, plain, html = format_stale([
                    {"company": a["company"], "role": a["role"], "stage": a["stage"],
                     "last": (a["last_activity_at"] or "")[:10]} for a in stale
                ])
                self.alerter.send(subj, plain, html)
            self.db.set_state("last_stale_check", today)

    # ------------------------------------------------------------ obsidian
    def mirror_to_obsidian(self, all_apps: bool = False) -> None:
        if not self.obsidian or self.cfg.dry_run:
            return
        ids = {int(a["id"]) for a in self.db.list_applications()} if all_apps else self.touched_apps
        failed = 0
        for app_id in ids:
            try:
                self.obsidian.write_application(self.db, app_id)
            except Exception as exc:
                failed += 1
                log.error("Obsidian write failed for app %s: %s", app_id, exc)
        if failed:
            log.error("Obsidian mirror is failing (%d of %d). Check OBSIDIAN_VAULT_PATH; "
                      "mail processing is unaffected.", failed, len(ids))
        if ids and failed < len(ids):
            try:
                self.obsidian.write_index(self.db)
            except Exception as exc:
                log.error("Obsidian index write failed: %s", exc)

    # ------------------------------------------------------------ run
    def _resume_uid(self, mc: MailClient, folder: str) -> int:
        """UID to resume from, honouring UIDVALIDITY.

        IMAP UIDs are only meaningful within a UIDVALIDITY generation. If the
        server reissues them, a stored `last_uid` is meaningless: it can skip
        new mail, and worse, a new message can land on a UID we already have on
        file and be silently swallowed by the dedupe. On a change we drop the
        stored UIDs for that folder and rescan from the start; the Message-ID
        check in process_* keeps the rescan from re-paying for anything.
        """
        validity = mc.select(folder)
        last_uid = int(self.db.get_state(f"last_uid:{folder}", "0") or 0)
        if validity is None:
            return last_uid
        key = f"uidvalidity:{folder}"
        stored = self.db.get_state(key)
        self.db.set_state(key, str(validity))
        if stored is not None and stored != str(validity):
            log.warning(
                "UIDVALIDITY for %s changed (%s → %s): the server reissued UIDs. "
                "Rescanning the folder; already-seen mail is skipped by Message-ID.",
                folder, stored, validity,
            )
            self.db.clear_uids(folder)
            self.db.set_state(f"last_uid:{folder}", "0")
            return 0
        return last_uid

    def _drain(self, mc: MailClient, folder: str, inbound: bool) -> int:
        last_uid = self._resume_uid(mc, folder)
        handled = 0
        for msg in mc.fetch_since_uid(folder, last_uid):
            if inbound:
                self.process_inbound(msg)
            else:
                self.process_outbound(msg)
            handled += 1
            self.db.set_state(f"last_uid:{folder}", str(msg.uid))
        return handled

    def run_once(self) -> dict:
        stats = {"inbound": 0, "outbound": 0}
        with MailClient(self.cfg.imap_host, self.cfg.imap_port, self.cfg.imap_user, self.cfg.imap_password) as mc:
            stats["inbound"] = self._drain(mc, self.cfg.imap_folder, inbound=True)
            sent = self._resolve_sent_folder(mc)
            if sent:
                stats["outbound"] = self._drain(mc, sent, inbound=False)

        self.send_alerts()
        self.mirror_to_obsidian()
        self.db.set_state("last_run_at", now_iso())
        return stats

    def _resolve_sent_folder(self, mc: MailClient) -> str | None:
        cached = self.db.get_state("sent_folder")
        if cached:
            return cached
        names = mc.list_folders()
        for cand in SENT_FOLDER_CANDIDATES:
            if cand in names:
                self.db.set_state("sent_folder", cand)
                return cand
        for n in names:
            if "sent" in n.lower():
                self.db.set_state("sent_folder", n)
                return n
        log.warning("No Sent folder found; outbound replies won't be tracked. Folders: %s", names)
        return None

    def _save_raw(self, msg: ParsedMessage) -> str | None:
        if not msg.raw:
            return None
        d = self.cfg.data_dir / "raw" / msg.folder.replace("/", "_")
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{msg.uid}.eml"
        p.write_bytes(msg.raw)
        return str(p)


def build(cfg: Config | None = None) -> Pipeline:
    cfg = cfg or Config.load()
    db = Database(cfg.db_path or cfg.data_dir / "jobmail.db")
    classifier = ClaudeClassifier(cfg.anthropic_api_key, cfg.claude_model, cfg.owner_name,
                                  effort=cfg.claude_effort)
    obsidian = ObsidianWriter(cfg.obsidian_vault_path, cfg.obsidian_subfolder) if cfg.obsidian_vault_path else None
    return Pipeline(cfg, db, classifier, Alerter(cfg), obsidian)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail", description="Poll the job-search inbox once.")
    ap.add_argument("--env", help="path to .env file")
    ap.add_argument("--dry-run", action="store_true", help="classify but send no alerts / write no notes")
    ap.add_argument("--resync-obsidian", action="store_true", help="rewrite every application note")
    ap.add_argument("-v", "--verbose", action="store_true", help="jobmail's own debug output")
    ap.add_argument("--debug-http", action="store_true",
                    help="also log the Anthropic/HTTP wire traffic (very noisy)")
    args = ap.parse_args(argv)
    use_utf8_io()

    # -v raises *our* logger only. Left to basicConfig, DEBUG on the root logger
    # makes the Anthropic SDK dump every request body — the full tool schema per
    # email — burying the four lines you actually wanted and flooding the journal.
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    if not args.debug_http:
        for noisy in ("anthropic", "httpx", "httpx2", "httpcore", "httpcore2", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config.load(args.env)
    if args.dry_run:
        cfg.dry_run = True
    for k in ("imap_user", "imap_password", "anthropic_api_key"):
        if not getattr(cfg, k):
            log.error("Missing config: %s", k.upper())
            return 2

    p = build(cfg)
    if args.resync_obsidian:
        p.mirror_to_obsidian(all_apps=True)
        return 0
    stats = p.run_once()
    log.info("Done: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
