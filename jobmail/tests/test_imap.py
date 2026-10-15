"""IMAP fetch behaviour and UID bookkeeping. No sockets: a fake conn stands in
for imaplib, and a fake MailClient for the pipeline-level tests."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pathlib

import pytest

from jobmail.mail import MailClient
from jobmail.obsidian import ObsidianWriter
from jobmail.pipeline import Pipeline

from conftest import CapturingAlerter, StubClassifier, make_email


def _raw(subject: str, msg_id: str) -> bytes:
    m = EmailMessage()
    m["From"] = "hr@acme.com"
    m["To"] = "me@gmail.com"
    m["Subject"] = subject
    m["Message-ID"] = msg_id
    m["Date"] = format_datetime(datetime.now(timezone.utc))
    m.set_content("body")
    return m.as_bytes()


class FakeConn:
    """Enough of imaplib.IMAP4_SSL for MailClient."""

    def __init__(self, uids: list[int], uid_validity: int = 111, unfetchable: set[int] | None = None):
        self.uids = uids
        self.uid_validity = uid_validity
        self.unfetchable = unfetchable or set()
        self.fetched: list[int] = []
        self.selected: list[str] = []

    def select(self, folder, readonly=False):
        self.selected.append(folder)
        return ("OK", [b"1"])

    def response(self, key):
        return ("OK", [str(self.uid_validity).encode()])

    def uid(self, cmd, *args):
        if cmd == "search":
            return ("OK", [" ".join(str(u) for u in self.uids).encode()])
        n = int(args[0])
        self.fetched.append(n)
        if n in self.unfetchable:
            return ("NO", [None])
        return ("OK", [(b"header", _raw(f"subject {n}", f"<m{n}@acme>")), b")"])


def _client(conn: FakeConn) -> MailClient:
    mc = MailClient("host", 993, "u", "p")
    mc._conn = conn
    return mc


class FakeMailClient:
    """Pipeline-level double: controllable UIDVALIDITY and message list."""

    def __init__(self, messages, uid_validity=111):
        self.messages = messages  # list[ParsedMessage], ascending uid
        self.uid_validity = uid_validity

    def select(self, folder):
        return self.uid_validity

    def fetch_since_uid(self, folder, last_uid, limit=200):
        return [m for m in self.messages if m.uid > last_uid][:limit]


@pytest.fixture
def pipe(cfg, db):
    clf = StubClassifier({"Interview": {"company": "Acme", "role": "PM",
                                        "message_type": "interview_request", "needs_reply": True},
                          "Applied": {"company": "Acme", "role": "PM",
                                      "message_type": "application_confirmation"}})
    p = Pipeline(cfg, db, clf, CapturingAlerter(), ObsidianWriter(cfg.obsidian_vault_path, "Applications"))
    return p, clf


# ------------------------------------------------------------------ fetching


def test_fetch_returns_new_uids_in_order():
    conn = FakeConn([3, 1, 2])
    msgs = _client(conn).fetch_since_uid("INBOX", 0)
    assert [m.uid for m in msgs] == [1, 2, 3]


def test_fetch_skips_uids_at_or_below_the_watermark():
    conn = FakeConn([1, 2, 3])
    msgs = _client(conn).fetch_since_uid("INBOX", 2)
    assert [m.uid for m in msgs] == [3]
    assert conn.fetched == [3]  # the others were never downloaded


def test_fetch_stops_at_an_unfetchable_message():
    """A failed fetch must not be stepped over: the watermark has to stall on it."""
    conn = FakeConn([1, 2, 3], unfetchable={2})
    msgs = _client(conn).fetch_since_uid("INBOX", 0)
    assert [m.uid for m in msgs] == [1]      # 3 is deliberately withheld
    assert conn.fetched == [1, 2]            # and never even requested


def test_missing_folder_yields_nothing():
    conn = FakeConn([1])
    conn.select = lambda folder, readonly=False: ("NO", [b"nope"])
    assert _client(conn).fetch_since_uid("Nope", 0) == []


# ------------------------------------------------------------- uid validity


def test_watermark_advances_and_dedupes(pipe, db, days_ago):
    p, clf = pipe
    msgs = [make_email(1, frm="hr@acme.com", subject="Applied", body="x", msg_id="<a@1>", when=days_ago(3)),
            make_email(2, frm="hr@acme.com", subject="Interview", body="y", msg_id="<b@2>", when=days_ago(1))]
    mc = FakeMailClient(msgs)

    assert p._drain(mc, "INBOX", inbound=True) == 2
    assert db.get_state("last_uid:INBOX") == "2"
    assert db.get_state("uidvalidity:INBOX") == "111"
    assert clf.calls == 2

    assert p._drain(mc, "INBOX", inbound=True) == 0  # nothing new
    assert clf.calls == 2


def test_uidvalidity_change_rescans_without_reclassifying(pipe, db, days_ago):
    """The server reissues UIDs; we must re-read the folder but not re-pay for it."""
    p, clf = pipe
    msgs = [make_email(1, frm="hr@acme.com", subject="Applied", body="x", msg_id="<a@1>", when=days_ago(3)),
            make_email(2, frm="hr@acme.com", subject="Interview", body="y", msg_id="<b@2>", when=days_ago(1))]
    p._drain(FakeMailClient(msgs), "INBOX", inbound=True)
    assert clf.calls == 2
    before = len(db.list_messages_for_application(1))

    # same two messages, renumbered under a new generation, plus one genuinely new
    renumbered = [make_email(10, frm="hr@acme.com", subject="Applied", body="x", msg_id="<a@1>", when=days_ago(3)),
                  make_email(11, frm="hr@acme.com", subject="Interview", body="y", msg_id="<b@2>", when=days_ago(1)),
                  make_email(12, frm="hr@acme.com", subject="Interview", body="z", msg_id="<c@3>", when=days_ago(0))]
    p._drain(FakeMailClient(renumbered, uid_validity=222), "INBOX", inbound=True)

    assert db.get_state("uidvalidity:INBOX") == "222"
    assert clf.calls == 3  # only the genuinely new message cost a Claude call
    assert len(db.list_messages_for_application(1)) == before + 1
    assert db.get_state("last_uid:INBOX") == "12"


def test_uidvalidity_change_cannot_swallow_a_reused_uid(pipe, db, days_ago):
    """A new message landing on an old UID must still be processed."""
    p, clf = pipe
    p._drain(FakeMailClient([make_email(5, frm="hr@acme.com", subject="Applied", body="x",
                                        msg_id="<old@5>", when=days_ago(3))]), "INBOX", inbound=True)
    assert clf.calls == 1

    # different mail, same UID, new generation
    reused = make_email(5, frm="hr@acme.com", subject="Interview", body="new", msg_id="<new@5>",
                        when=days_ago(1))
    p._drain(FakeMailClient([reused], uid_validity=222), "INBOX", inbound=True)
    assert clf.calls == 2
    assert db.find_message_by_message_id("<new@5>") is not None


def test_first_run_records_uidvalidity_without_rescanning(pipe, db, days_ago):
    p, clf = pipe
    msgs = [make_email(1, frm="hr@acme.com", subject="Applied", body="x", msg_id="<a@1>", when=days_ago(3))]
    db.set_state("last_uid:INBOX", "1")  # pre-seeded to skip history, as the README suggests
    assert p._drain(FakeMailClient(msgs), "INBOX", inbound=True) == 0
    assert clf.calls == 0
    assert db.get_state("uidvalidity:INBOX") == "111"


# ------------------------------------------------------------------ preflight


def test_preflight_reports_folders_and_seed_uid(cfg, capsys, monkeypatch):
    from jobmail import preflight

    conn = FakeConn([4, 5, 9])
    conn.list = lambda: ("OK", [rb'(\HasNoChildren) "/" "INBOX"',
                                rb'(\HasNoChildren) "/" "Sent Messages"'])
    monkeypatch.setattr(MailClient, "__enter__", lambda self: setattr(self, "_conn", conn) or self)
    monkeypatch.setattr(MailClient, "__exit__", lambda self, *a: None)

    cfg.imap_user, cfg.imap_password = "me@gmail.com", "pw"
    assert preflight.check_imap(cfg) == 0

    out = capsys.readouterr().out
    assert "logged in" in out
    assert "Sent Messages" in out and "outbound replies will be tracked" in out
    assert "holds 3 message(s)" in out
    assert "seed nothing" in out           # a tiny mailbox is cheaper to just classify


def test_preflight_fails_without_credentials(cfg, capsys):
    from jobmail import preflight

    cfg.imap_user, cfg.imap_password = "", ""
    assert preflight.check_imap(cfg) == 2
    assert "must both be set" in capsys.readouterr().out


def test_preflight_explains_a_rejected_login(cfg, capsys, monkeypatch):
    import imaplib as _imaplib
    from jobmail import preflight

    def boom(self):
        raise _imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(MailClient, "__enter__", boom)
    cfg.imap_user, cfg.imap_password = "me@gmail.com", "wrong"
    assert preflight.check_imap(cfg) == 1
    out = capsys.readouterr().out
    assert "login rejected" in out and "App Password" in out  # points at the likely cause


def test_preflight_resolves_gmails_sent_folder(cfg, capsys, monkeypatch):
    """Gmail nests Sent under [Gmail]/, which matches none of the classic names."""
    from jobmail import preflight

    conn = FakeConn([1])
    conn.list = lambda: ("OK", [rb'(\HasNoChildren) "/" "INBOX"',
                                rb'(\HasNoChildren) "/" "Jobs"',
                                rb'(\HasNoChildren) "/" "[Gmail]/All Mail"',
                                rb'(\HasNoChildren) "/" "[Gmail]/Sent Mail"'])
    monkeypatch.setattr(MailClient, "__enter__", lambda self: setattr(self, "_conn", conn) or self)
    monkeypatch.setattr(MailClient, "__exit__", lambda self, *a: None)

    cfg.imap_user, cfg.imap_password = "me@gmail.com", "apppw"
    assert preflight.check_imap(cfg) == 0
    out = capsys.readouterr().out
    assert "'[Gmail]/Sent Mail'" in out
    assert "[Gmail]/All Mail" not in out.split("Sent folder resolves")[1]  # picked the right one


def test_classifier_check_skipped_without_a_key(cfg, capsys):
    from jobmail import preflight
    cfg.anthropic_api_key = ""
    assert preflight.check_classifier(cfg) == 0
    assert "skipped" in capsys.readouterr().out


def test_classifier_check_reports_a_good_answer(cfg, capsys, monkeypatch):
    from jobmail import preflight
    from jobmail.classifier import Classification

    class Fake:
        def __init__(self, *a, **k): pass
        def classify(self, msg):
            assert "Acme" in msg.subject and "Thursday" in msg.subject  # the sample reached it
            return Classification.from_dict({
                "is_job_related": True, "company": "Acme", "role": "Senior Product Manager",
                "message_type": "interview_request", "sender_is_human": True,
                "needs_reply": True, "urgency": "high", "summary": "Jane wants a screen.",
                "action_needed": "Send times before Friday",
                "key_dates": [{"date": "2026-09-05", "description": "Reply deadline"}]})

    monkeypatch.setattr("jobmail.classifier.ClaudeClassifier", Fake)
    cfg.anthropic_api_key = "sk-ant-test"
    assert preflight.check_classifier(cfg) == 0
    out = capsys.readouterr().out
    assert "schema conformed" in out and "Acme" in out and "2026-09-05" in out
    assert "prompt may need a look" not in out


def test_classifier_check_surfaces_an_api_error(cfg, capsys, monkeypatch):
    from jobmail import preflight

    class Boom:
        def __init__(self, *a, **k): pass
        def classify(self, msg): raise RuntimeError("invalid x-api-key")

    monkeypatch.setattr("jobmail.classifier.ClaudeClassifier", Boom)
    cfg.anthropic_api_key = "sk-ant-bad"
    assert preflight.check_classifier(cfg) == 1
    assert "invalid x-api-key" in capsys.readouterr().out


def test_preflight_offers_a_seed_for_a_big_mailbox(cfg, capsys, monkeypatch):
    """Past ~25 messages the Claude bill starts to matter, so offer the watermark."""
    from jobmail import preflight

    conn = FakeConn(list(range(1, 60)))
    conn.list = lambda: ("OK", [rb'(\HasNoChildren) "/" "INBOX"',
                                rb'(\HasNoChildren) "/" "[Gmail]/Sent Mail"'])
    monkeypatch.setattr(MailClient, "__enter__", lambda self: setattr(self, "_conn", conn) or self)
    monkeypatch.setattr(MailClient, "__exit__", lambda self, *a: None)
    cfg.imap_user, cfg.imap_password = "me@gmail.com", "apppw"
    cfg.db_path = pathlib.PureWindowsPath(r"\home\pi\.jobmail\jobmail.db")

    assert preflight.check_imap(cfg) == 0
    out = capsys.readouterr().out
    assert "last_uid:INBOX', '59'" in out
    assert "/home/pi/.jobmail/jobmail.db" in out   # never backslashes: it is a Pi path
