"""SQLite memory: what we've seen, what we've emailed. Keeps digests new-only."""
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  fingerprint TEXT PRIMARY KEY,
  source TEXT, company TEXT, title TEXT, url TEXT, location TEXT,
  comp_text TEXT, comp_min INTEGER, comp_max INTEGER, posted TEXT,
  score INTEGER, tier TEXT, why TEXT,
  first_seen TEXT, sent_at TEXT, status TEXT DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_sent ON jobs(sent_at);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at TEXT, fetched INTEGER, kept INTEGER, emailed INTEGER, note TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def is_new(self, job):
        cur = self.db.execute(
            "SELECT 1 FROM jobs WHERE fingerprint=? OR (url=? AND url<>'')",
            (job.fingerprint, job.url))
        return cur.fetchone() is None

    def record(self, job, sent=False):
        self.db.execute(
            """INSERT INTO jobs (fingerprint, source, company, title, url, location,
                 comp_text, comp_min, comp_max, posted, score, tier, why, first_seen, sent_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                 score=excluded.score, tier=excluded.tier, why=excluded.why,
                 sent_at=COALESCE(jobs.sent_at, excluded.sent_at)""",
            (job.fingerprint, job.source, job.company, job.title, job.url, job.location,
             job.comp_text, job.comp_min, job.comp_max, job.posted, job.score, job.tier,
             job.why, now(), now() if sent else None))
        self.db.commit()

    def log_run(self, fetched, kept, emailed, note=""):
        self.db.execute(
            "INSERT INTO runs (ran_at, fetched, kept, emailed, note) VALUES (?,?,?,?,?)",
            (now(), fetched, kept, emailed, note))
        self.db.commit()

    def stats(self):
        c = self.db.execute
        last = c("SELECT ran_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "tracked": c("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "emailed": c("SELECT COUNT(*) FROM jobs WHERE sent_at IS NOT NULL").fetchone()[0],
            "strong": c("SELECT COUNT(*) FROM jobs WHERE tier='strong'").fetchone()[0],
            "runs": c("SELECT COUNT(*) FROM runs").fetchone()[0],
            "last_run": last[0] if last else None,
        }

    def recent(self, limit=20):
        return self.db.execute(
            "SELECT company, title, score, tier, sent_at, url FROM jobs "
            "WHERE sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT ?", (limit,)).fetchall()
