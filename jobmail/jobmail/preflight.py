"""Connection check: `python -m jobmail.preflight`.

Proves the three things that can only be learned from the live server, before
any of the rest of the stack is wired up: that the IMAP host and credentials
work, what the folders are actually called, and where Sent lives. Needs nothing
but the four IMAP settings. It imports only config and mail, so `pip install
python-dotenv` is enough to run it: no Anthropic key, no Telegram, no vault, and
none of the heavier dependencies.

Read-only. Selects folders with readonly=True and never fetches a body.
"""

from __future__ import annotations

import argparse
import imaplib
import ssl

from .config import Config, use_utf8_io
from .mail import SENT_FOLDER_CANDIDATES, MailClient


def _ok(msg: str) -> None:
    print(f"  ok    {msg}")


def _warn(msg: str) -> None:
    print(f"  warn  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def check_imap(cfg: Config) -> int:
    print(f"\nConnecting to {cfg.imap_host}:{cfg.imap_port} as {cfg.imap_user!r}\n")

    if not cfg.imap_user or not cfg.imap_password:
        _fail("IMAP_USER and IMAP_PASSWORD must both be set in .env")
        return 2

    try:
        mc = MailClient(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password)
        mc.__enter__()
    except (imaplib.IMAP4.error, imaplib.IMAP4.abort) as exc:
        _fail(f"login rejected: {exc}")
        print("\n  On Gmail, in order:")
        print("    1. IMAP_PASSWORD must be an App Password, not your Google password.")
        print("       Turn on 2-Step Verification, then: myaccount.google.com/apppasswords")
        print("    2. Strip the spaces. Google shows it as 4 groups of 4; the value needs")
        print("       all 16 characters run together.")
        print("    3. IMAP_USER is the full address, e.g. you@gmail.com.")
        return 1
    except (OSError, ssl.SSLError) as exc:
        _fail(f"could not reach the server: {exc}")
        print(f"\n  Check IMAP_HOST/IMAP_PORT. Expected {cfg.imap_host}:{cfg.imap_port}.")
        return 1

    try:
        _ok("logged in")

        folders = mc.list_folders()
        if not folders:
            _fail("logged in but the server listed no folders")
            return 1
        print(f"\n  Folders ({len(folders)}):")
        for name in folders:
            print(f"    {name}")

        print()
        if cfg.imap_folder in folders:
            _ok(f"IMAP_FOLDER={cfg.imap_folder!r} exists")
        else:
            _fail(f"IMAP_FOLDER={cfg.imap_folder!r} is not in that list — fix .env")

        sent = next((c for c in SENT_FOLDER_CANDIDATES if c in folders), None) \
            or next((n for n in folders if "sent" in n.lower()), None)
        if sent:
            _ok(f"Sent folder resolves to {sent!r} (outbound replies will be tracked)")
        else:
            _warn("no Sent folder matched; replies won't auto-clear a 'needs reply'. "
                  "Add its real name to SENT_FOLDER_CANDIDATES in mail.py.")

        validity = mc.select(cfg.imap_folder)
        if validity is None:
            _fail(f"could not select {cfg.imap_folder!r}")
            return 1
        _ok(f"UIDVALIDITY for {cfg.imap_folder} is {validity}")

        status, data = mc.conn.uid("search", None, "ALL")
        uids = [int(u) for u in data[0].split()] if status == "OK" and data and data[0] else []
        print(f"\n  {cfg.imap_folder} holds {len(uids)} message(s).")
        if not uids:
            print("  Empty, so nothing to seed. The first poll starts clean.")
        elif len(uids) <= 25:
            print(f"  Highest UID: {max(uids)}")
            print("\n  Few enough that classifying them all is cheap, and it exercises the")
            print("  pipeline on real mail. Recommended: seed nothing, just run --dry-run -v.")
        else:
            print(f"  Highest UID: {max(uids)}")
            print("\n  Each message is one Claude call. To start from now instead, seed the")
            print("  watermark on the Pi before the first run:\n")
            # posix: the data dir is a Pi path even when this runs from Windows
            db = str(cfg.db_path).replace("\\", "/")
            print(f"    sqlite3 {db} \\")
            print(f"      \"INSERT INTO state VALUES ('last_uid:{cfg.imap_folder}', '{max(uids)}')\"")

        return 0
    finally:
        mc.__exit__()


def check_classifier(cfg: Config) -> int:
    """Prove the Anthropic key and the forced-tool-use schema on one synthetic email.

    Costs a fraction of a cent, and it is the only way to learn before the Pi
    ever runs that the tool schema is accepted and the response parses.
    """
    print("\nClassifier")
    if not cfg.anthropic_api_key:
        _warn("ANTHROPIC_API_KEY not set — skipped. Needed before the first real poll.")
        return 0
    try:
        from .classifier import ClaudeClassifier
    except ImportError:
        _warn("the `anthropic` package isn't installed here — skipped. "
              "This check runs on the Pi after `pip install -e .`")
        return 0

    from .mail import parse_message
    sample = parse_message(
        b"From: Jane Recruiter <jane@acme.com>\r\n"
        b"To: you@gmail.com\r\n"
        b"Subject: Senior PM role at Acme - are you free Thursday?\r\n"
        b"Message-ID: <preflight@local>\r\n"
        b"Date: Mon, 1 Sep 2026 09:00:00 +0000\r\n\r\n"
        b"Hi, we'd love to set up a 30-minute screen for the Senior Product Manager\r\n"
        b"role. Could you send a few times that work before Friday 5 Sep?\r\n",
        "preflight", "preflight")
    try:
        cls = ClaudeClassifier(cfg.anthropic_api_key, cfg.claude_model, cfg.owner_name,
                               effort=cfg.claude_effort).classify(sample)
    except Exception as exc:
        _fail(f"classification failed: {type(exc).__name__}: {exc}")
        print("\n  If this is an auth error, check ANTHROPIC_API_KEY at console.anthropic.com.")
        print(f"  If it names the model, check CLAUDE_MODEL={cfg.claude_model!r}.")
        return 1

    _ok(f"{cfg.claude_model} answered, schema conformed")
    print(f"    company      {cls.company!r}")
    print(f"    role         {cls.role!r}")
    print(f"    type         {cls.message_type}")
    print(f"    needs_reply  {cls.needs_reply}   urgency: {cls.urgency}")
    print(f"    summary      {cls.summary}")
    if cls.action_needed:
        print(f"    action       {cls.action_needed}")
    for kd in cls.key_dates:
        print(f"    date         {kd['date']}  {kd['description']}")
    if not (cls.is_job_related and cls.needs_reply and cls.company):
        _warn("that sample should read as job-related, needing a reply, at 'Acme'. "
              "The call works, but the prompt may need a look.")
    return 0


def check(cfg: Config) -> int:
    rc = check_imap(cfg)
    if rc == 2:
        return rc
    rc = max(rc, check_classifier(cfg))
    print("\nAll good.\n" if rc == 0 else "\nSomething above needs fixing.\n")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail-preflight",
                                 description="Check IMAP connectivity and discover folder names.")
    ap.add_argument("--env", help="path to .env file")
    args = ap.parse_args(argv)
    use_utf8_io()
    try:
        return check(Config.load(args.env))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
