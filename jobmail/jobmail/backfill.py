"""Recover applications you already made: `python -m jobmail.backfill`.

The poller only sees mail that arrives after it starts. This walks backwards
through Gmail's All Mail (so archived threads count) and finds the application
confirmations you already received, so the metrics start with your real history
instead of from the day you installed this.

Cost control is the whole design. Every classified message is one Claude call,
and All Mail can hold tens of thousands of messages, so:

  1. IMAP SEARCH filters server-side first, on ATS sender domains and on the
     subject lines those confirmations actually use. Free, and it turns
     "everything" into a few dozen candidates.
  2. Only Message-ID headers are fetched for the survivors, which is cheap, and
     anything already in the database is dropped before it costs anything.
  3. You are shown the count and the estimated spend, and nothing is classified
     until you say yes.

Alerts stay off throughout. Backfilling months of old mail should not fire a
burst of Telegram notifications about interviews that already happened.
"""

from __future__ import annotations

import argparse
import email
import logging
import re
import sys
from datetime import datetime, timedelta, timezone

from .config import Config, use_utf8_io
from .db import Database
from .mail import MailClient, parse_message
from .matcher import ATS_LABELS

log = logging.getLogger("jobmail.backfill")

DEFAULT_FOLDER = "[Gmail]/All Mail"

# Subjects an application acknowledgement actually uses. Deliberately broad:
# IMAP filtering is free, and a false positive costs one Claude call at most.
SUBJECT_HINTS = [
    "thank you for applying",
    "thanks for applying",
    "application received",
    "we received your application",
    "your application",
    "application submitted",
    "application confirmation",
    "received your application",
]

# Roughly what one classification costs on claude-sonnet-5 at effort low:
# ~1.5k input + ~250 output tokens, at $2/MTok in and $10/MTok out.
COST_PER_MESSAGE = 0.0055


def _imap_date(d: datetime) -> str:
    return d.strftime("%d-%b-%Y")


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_candidates(mc: MailClient, folder: str, since: datetime) -> list[int]:
    """UIDs worth looking at, found entirely server-side. Costs nothing."""
    if mc.select(folder) is None:
        log.error("Folder %r not found. Try --folder with a name from preflight.", folder)
        return []

    since_term = f"SINCE {_imap_date(since)}"
    terms = [f"{since_term} FROM {_quote(d)}" for d in sorted(ATS_LABELS)]
    terms += [f"{since_term} SUBJECT {_quote(h)}" for h in SUBJECT_HINTS]

    found: set[int] = set()
    for term in terms:
        try:
            status, data = mc.conn.uid("search", None, term)
        except Exception as exc:
            log.debug("search %r failed: %s", term, exc)
            continue
        if status == "OK" and data and data[0]:
            found.update(int(u) for u in data[0].split())
    return sorted(found)


def _message_ids(mc: MailClient, uids: list[int]) -> dict[int, str]:
    """Message-ID per UID, fetched headers-only so this stays cheap."""
    out: dict[int, str] = {}
    for i in range(0, len(uids), 50):
        chunk = ",".join(str(u) for u in uids[i:i + 50])
        try:
            status, parts = mc.conn.uid("fetch", chunk, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        except Exception as exc:
            log.debug("header fetch failed: %s", exc)
            continue
        if status != "OK":
            continue
        uid = None
        for part in parts:
            if isinstance(part, tuple):
                m = re.search(rb"(\d+) \(", part[0] or b"")
                uid = int(m.group(1)) if m else uid
                hdr = email.message_from_bytes(part[1]).get("Message-ID") or ""
                if uid is not None and hdr.strip():
                    out[uid] = hdr.strip()
    return out


def run(cfg: Config, since_days: int, folder: str, assume_yes: bool,
        limit: int, list_only: bool) -> int:
    from .classifier import ClaudeClassifier
    from .pipeline import Pipeline

    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    db = Database(cfg.db_path or cfg.data_dir / "jobmail.db")

    # Old mail must not trigger notifications for things that already happened.
    cfg.alerts_enabled = False

    try:
        with MailClient(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password) as mc:
            print(f"\nSearching {folder} since {since:%Y-%m-%d} ...")
            uids = search_candidates(mc, folder, since)
            if not uids:
                print("No candidate messages matched. Nothing to do.\n")
                return 0
            print(f"  {len(uids)} candidate(s) matched an ATS sender or an application subject")

            ids = _message_ids(mc, uids)
            fresh = [u for u in uids if not (ids.get(u) and db.find_message_by_message_id(ids[u]))]
            skipped = len(uids) - len(fresh)
            if skipped:
                print(f"  {skipped} already in the database, skipping")
            if limit and len(fresh) > limit:
                print(f"  limiting to the {limit} most recent of {len(fresh)}")
                fresh = fresh[-limit:]
            if not fresh:
                print("\nEverything found is already recorded. Nothing to do.\n")
                return 0

            print(f"\n  {len(fresh)} message(s) to classify")
            print(f"  estimated cost: about ${len(fresh) * COST_PER_MESSAGE:.2f}")

            if list_only:
                print("\n--list given, stopping before any spend.\n")
                return 0
            if not assume_yes:
                if input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Cancelled. Nothing was classified.\n")
                    return 1

            classifier = ClaudeClassifier(cfg.anthropic_api_key, cfg.claude_model,
                                          cfg.owner_name, effort=cfg.claude_effort)
            pipe = Pipeline(cfg, db, classifier, obsidian=None)

            done = 0
            for uid in fresh:
                status, parts = mc.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    log.warning("could not fetch UID %s, skipping", uid)
                    continue
                try:
                    msg = parse_message(parts[0][1], uid, folder)
                except Exception as exc:
                    log.warning("could not parse UID %s: %s", uid, exc)
                    continue
                pipe.process_inbound(msg)
                done += 1
                if done % 10 == 0:
                    print(f"  ... {done}/{len(fresh)}")

        apps = db.list_applications()
        print(f"\nClassified {done} message(s). {len(apps)} application(s) now tracked.")
        f = db.funnel()
        print("  " + "  ".join(f"{k} {v}" for k, v in f.items() if v))
        print("\nSee the numbers at the dashboard's /metrics page.\n")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="jobmail-backfill",
        description="Find application confirmations already in your mailbox.")
    ap.add_argument("--env")
    ap.add_argument("--days", type=int, default=180, help="how far back to look (default 180)")
    ap.add_argument("--folder", default=DEFAULT_FOLDER,
                    help=f"folder to search (default {DEFAULT_FOLDER!r}, so archived mail counts)")
    ap.add_argument("--limit", type=int, default=0, help="classify at most N messages")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="report what was found and stop, spending nothing")
    ap.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    use_utf8_io()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("jobmail").setLevel(logging.DEBUG if args.verbose else logging.INFO)
    for noisy in ("anthropic", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = Config.load(args.env)
    for key, name in (("imap_user", "IMAP_USER"), ("anthropic_api_key", "ANTHROPIC_API_KEY")):
        if not getattr(cfg, key):
            print(f"Missing config: {name}", file=sys.stderr)
            return 2
    try:
        return run(cfg, args.days, args.folder, args.yes, args.limit, args.list_only)
    except KeyboardInterrupt:
        print("\nInterrupted.\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
