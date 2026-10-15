"""Offline demo: run the real pipeline over the synthetic inbox in samples/.

Everything downstream of the mailbox is the production code path: the
job_inbox skill, matcher, SQLite memory, stage machine, alerts, Obsidian
mirror. Only IMAP is swapped for a JSON file of synthetic mail.

    python -m jobmail.demo --run 1 --reset --live    # week 1, calls Claude
    python -m jobmail.demo --run 2 --live            # week 2, same database
    python -m jobmail.demo --run 1 --reset --replay  # no API calls, recorded answers

Run 2 is where memory shows: replies thread onto week-1 applications, stages
advance, and your sent replies clear what needed answering.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from .classifier import Classification, ClaudeClassifier
from .config import Config, use_utf8_io
from .db import Database
from .mail import ParsedMessage, parse_message
from .obsidian import ObsidianWriter
from .pipeline import Pipeline

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "job_inbox.json"
CACHE = ROOT / "samples" / "job_inbox.recorded.json"
OUT = ROOT / "demo_out"

log = logging.getLogger("jobmail.demo")


# ------------------------------------------------------------------ inputs
def load_samples(path: Path = SAMPLES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_message(m: dict[str, Any], uid: int, owner_addr: str, now: datetime) -> ParsedMessage:
    e, folder = build_email(m, owner_addr, now)
    return parse_message(e.as_bytes(), uid, folder)


def build_email(m: dict[str, Any], owner_addr: str, now: datetime) -> tuple[EmailMessage, str]:
    when = (now + timedelta(days=m["day"])).replace(hour=m["hour"], minute=0, second=0, microsecond=0)
    e = EmailMessage()
    e["From"] = m["from"]
    e["To"] = m.get("to", owner_addr)
    e["Subject"] = m["subject"]
    e["Message-ID"] = m["message_id"]
    e["Date"] = format_datetime(when)
    if m.get("in_reply_to"):
        e["In-Reply-To"] = m["in_reply_to"]
    if m.get("references"):
        e["References"] = m["references"]
    if m.get("html"):
        e.set_content(m["body"], subtype="html")
    else:
        e.set_content(m["body"])
    folder = "INBOX" if m.get("folder", "INBOX") == "INBOX" else "[Gmail]/Sent Mail"
    return e, folder


def export_eml(out_dir: Path) -> list[Path]:
    """Write every sample as a real .eml file, for `python -m jobmail.triage <skill> <file>`."""
    now = datetime.now().astimezone()
    written = []
    for name in ("job_inbox", "ap_inbox"):
        data = load_samples(ROOT / "samples" / f"{name}.json")
        d = out_dir / name
        d.mkdir(parents=True, exist_ok=True)
        for m in data["messages"]:
            e, _ = build_email(m, data["owner_addr"], now)
            p = d / f"{m['id']}.eml"
            p.write_bytes(e.as_bytes())
            written.append(p)
    return written


def uid_for(data: dict[str, Any], sample_id: str) -> int:
    return 1 + [m["id"] for m in data["messages"]].index(sample_id)


# ------------------------------------------------------------------ classifiers
class RecordingClassifier:
    """Live Claude, plus a cache of every answer for replay and for the record."""

    def __init__(self, inner: ClaudeClassifier, by_msgid: dict[str, str]):
        self.inner, self.by_msgid = inner, by_msgid
        self.recorded: dict[str, Any] = {}
        self.input_tokens = self.output_tokens = 0
        self.last_result = None

    def classify(self, msg: ParsedMessage) -> Classification:
        cls = self.inner.classify(msg)
        r = self.last_result = self.inner.last_result
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.recorded[self.by_msgid[msg.message_id]] = {
            "record": r.record, "ok": r.ok, "error": r.error, "model": r.model,
            "recorded_on": date.today().isoformat(),
        }
        return cls


class ReplayClassifier:
    """Recorded answers, with dates moved forward so deadlines are still ahead of you."""

    def __init__(self, cache: dict[str, Any], by_msgid: dict[str, str]):
        self.cache, self.by_msgid = cache, by_msgid
        self.last_result = None

    def classify(self, msg: ParsedMessage) -> Classification:
        entry = self.cache[self.by_msgid[msg.message_id]]
        rec = json.loads(json.dumps(entry["record"]))
        shift = (date.today() - date.fromisoformat(entry["recorded_on"])).days
        for kd in rec.get("key_dates", []):
            kd["date"] = _shift_iso(kd["date"], shift)
        cls = Classification.from_dict(rec)
        cls.failed = not entry.get("ok", True)
        return cls


def _shift_iso(value: str, days: int) -> str:
    try:
        if len(value) == 10:
            return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
        return (datetime.fromisoformat(value) + timedelta(days=days)).isoformat()
    except ValueError:
        return value


class ConsoleAlerter:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, plain: str, html: str | None = None) -> None:
        self.sent.append((subject, plain))


# ------------------------------------------------------------------ run
def run(run_no: int, mode: str, reset: bool, env: str | None, model: str, effort: str,
        out: Path = OUT) -> str:
    data = load_samples()
    by_msgid = {m["message_id"]: m["id"] for m in data["messages"]}
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "jobmail.db"
    if reset:
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)

    cfg = Config(data_dir=out, db_path=db_path, obsidian_vault_path=out / "vault",
                 owner_name=data["owner_name"], claude_model=model, claude_effort=effort)
    db = Database(db_path)

    if mode == "replay":
        if not CACHE.exists():
            raise SystemExit(f"No recording at {CACHE}. Run once with --record first.")
        classifier: Any = ReplayClassifier(json.loads(CACHE.read_text(encoding="utf-8"))["answers"], by_msgid)
    else:
        from dotenv import load_dotenv
        import os
        load_dotenv(env, override=False)
        inner = ClaudeClassifier(os.environ.get("ANTHROPIC_API_KEY", ""), model, data["owner_name"], effort=effort)
        classifier = RecordingClassifier(inner, by_msgid)

    alerter = ConsoleAlerter()
    p = Pipeline(cfg, db, classifier, alerter, ObsidianWriter(cfg.obsidian_vault_path, "Applications"))
    now = datetime.now().astimezone()

    batch = [m for m in data["messages"] if m["run"] == run_no]
    inbound = sorted((m for m in batch if m["folder"] == "INBOX"), key=lambda m: (m["day"], m["hour"]))
    sent = sorted((m for m in batch if m["folder"] == "Sent"), key=lambda m: (m["day"], m["hour"]))
    stage_before = {a["id"]: a["stage"] for a in db.list_applications()}

    lines = [f"RUN {run_no}: {len(inbound)} inbound, {len(sent)} sent  "
             f"(classifier: {mode}, {model}, effort={effort})", ""]
    lines.append(f"{'id':4} {'type':26} {'company':22} {'linked by':17} {'app':>4}  reply?")
    lines.append("-" * 82)
    for m in inbound:
        msg = build_message(m, uid_for(data, m["id"]), data["owner_addr"], now)
        row_id = p.process_inbound(msg)
        row = db.get_message(row_id)
        how = p.last_match.how if p.last_match else ""
        app = row["application_id"]
        company = (db.get_application(app)["company"] if app else "") or "-"
        flag = "FALLBACK" if getattr(classifier, "last_result", None) and not classifier.last_result.ok else ""
        lines.append(f"{m['id']:4} {row['message_type'] or '':26} {company[:22]:22} {how:17} "
                     f"{app or '-':>4}  {'YES ' + (row['urgency'] or '') if row['needs_reply'] else ''} {flag}")
    for m in sent:
        msg = build_message(m, uid_for(data, m["id"]), data["owner_addr"], now)
        p.process_outbound(msg)
        lines.append(f"{m['id']:4} {'(your reply)':26} -> {m['to']}  clears needs-reply on {m['in_reply_to']}")

    p.send_alerts()
    p.mirror_to_obsidian()

    lines += ["", "STAGE CHANGES THIS RUN"]
    changed = False
    for a in db.list_applications():
        before = stage_before.get(a["id"])
        if before != a["stage"]:
            changed = True
            lines.append(f"  #{a['id']:<3} {a['company']:22} {before or '(new)':>12} -> {a['stage']}")
    if not changed:
        lines.append("  none")

    lines += ["", f"ALERTS SENT ({len(alerter.sent)})"]
    lines += [f"  {s}" for s, _ in alerter.sent]

    lines += ["", "STILL NEEDS YOU"]
    pending = db.list_needs_reply()
    lines += [f"  [{r['urgency']}] {(r['company'] or 'unlinked'):22} {r['action_needed'] or r['summary']}"
              for r in pending] or ["  nothing"]

    lines += ["", "HELD FOR VERIFICATION (never linked, never learned)"]
    held = db.list_held_for_verification()
    lines += [f"  {r['from_addr']:42} {', '.join(json.loads(r['fraud_signals'] or '[]'))}"
              for r in held] or ["  nothing"]

    lines += ["", "PIPELINE"]
    for a in db.list_applications():
        lines.append(f"  #{a['id']:<3} {a['company']:22} {(a['role'] or '-')[:40]:40} {a['stage']}")

    if isinstance(classifier, RecordingClassifier):
        from .evaluate import PRICES
        pin, pout = PRICES.get(model, (0.0, 0.0))
        cost = classifier.input_tokens * pin / 1e6 + classifier.output_tokens * pout / 1e6
        lines += ["", f"TOKENS  in={classifier.input_tokens:,}  out={classifier.output_tokens:,}  "
                      f"~${cost:.4f} at {model} list price"]
        if mode == "record":
            cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {"answers": {}}
            cache["answers"].update(classifier.recorded)
            cache["about"] = ("Claude's answers for samples/job_inbox.json, recorded by "
                              "`python -m jobmail.demo --record`. Replay reuses them with no API calls.")
            CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            lines.append(f"recorded {len(classifier.recorded)} answers to {CACHE.name}")

    report = "\n".join(lines)
    (out / f"run{run_no}.txt").write_text(report + "\n", encoding="utf-8")
    db.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail-demo", description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", type=int, choices=[1, 2])
    ap.add_argument("--export-eml", action="store_true", help="write samples as .eml files to demo_out/eml")
    ap.add_argument("--reset", action="store_true", help="start from an empty database")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--live", action="store_const", dest="mode", const="live", help="call Claude")
    g.add_argument("--record", action="store_const", dest="mode", const="record",
                   help="call Claude and save the answers for --replay")
    g.add_argument("--replay", action="store_const", dest="mode", const="replay", help="no API calls")
    ap.add_argument("--env", help=".env file holding ANTHROPIC_API_KEY")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="low")
    args = ap.parse_args(argv)
    use_utf8_io()
    if args.export_eml:
        files = export_eml(OUT / "eml")
        print(f"wrote {len(files)} .eml files under {OUT / 'eml'}")
        return 0
    if not (args.run and args.mode):
        ap.error("--run and one of --live/--record/--replay are required")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    print(run(args.run, args.mode, args.reset, args.env, args.model, args.effort))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
