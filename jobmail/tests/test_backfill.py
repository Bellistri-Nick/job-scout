"""Backfill: server-side filtering, dedupe, and cost gating. No sockets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from jobmail import backfill
from jobmail.mail import MailClient


def _raw(subject: str, msg_id: str, frm: str = "no-reply@greenhouse.io") -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "me@gmail.com"
    m["Subject"] = subject
    m["Message-ID"] = msg_id
    m["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(days=30))
    m.set_content("We received your application.")
    return m.as_bytes()


class SearchConn:
    """Records the IMAP SEARCH terms and answers a fixed UID set."""

    def __init__(self, per_term: dict[str, list[int]] | None = None, selectable=True):
        self.per_term = per_term or {}
        self.terms: list[str] = []
        self.selectable = selectable
        self.body_fetches: list[str] = []

    def select(self, folder, readonly=False):
        return ("OK", [b"1"]) if self.selectable else ("NO", [b"nope"])

    def response(self, key):
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "search":
            term = args[1]
            self.terms.append(term)
            hits = self.per_term.get(term, [])
            return ("OK", [" ".join(str(u) for u in hits).encode()])
        target = args[0]
        if "HEADER.FIELDS" in args[1]:
            out = []
            for u in target.split(","):
                out.append((f"{u} (".encode(), f"Message-ID: <m{u}@acme>\r\n\r\n".encode()))
            return ("OK", out)
        self.body_fetches.append(target)
        return ("OK", [(b"1 (", _raw("Thank you for applying to Acme", f"<m{target}@acme>"))])


def _client(conn) -> MailClient:
    mc = MailClient("h", 993, "u", "p")
    mc._conn = conn
    return mc


def test_search_is_server_side_and_covers_ats_and_subjects():
    conn = SearchConn()
    since = datetime(2026, 3, 1, tzinfo=timezone.utc)
    backfill.search_candidates(_client(conn), "[Gmail]/All Mail", since)

    joined = " | ".join(conn.terms)
    assert "SINCE 01-Mar-2026" in joined
    assert 'FROM "greenhouse.io"' in joined and 'FROM "lever.co"' in joined
    assert 'SUBJECT "thank you for applying"' in joined
    # every term is date-bounded, or a backfill would scan the whole mailbox
    assert all(t.startswith("SINCE ") for t in conn.terms)


def test_search_unions_and_sorts_uids():
    conn = SearchConn()
    a = f'SINCE {backfill._imap_date(datetime(2026, 3, 1, tzinfo=timezone.utc))} FROM "lever.co"'
    b = f'SINCE {backfill._imap_date(datetime(2026, 3, 1, tzinfo=timezone.utc))} SUBJECT "your application"'
    conn.per_term = {a: [7, 3], b: [3, 11]}
    uids = backfill.search_candidates(_client(conn), "f", datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert uids == [3, 7, 11]          # deduped and ordered


def test_missing_folder_is_reported_not_crashed():
    assert backfill.search_candidates(_client(SearchConn(selectable=False)), "Nope",
                                      datetime(2026, 3, 1, tzinfo=timezone.utc)) == []


def test_header_fetch_maps_uids_to_message_ids():
    conn = SearchConn()
    ids = backfill._message_ids(_client(conn), [4, 9])
    assert ids == {4: "<m4@acme>", 9: "<m9@acme>"}


def test_list_only_never_classifies(cfg, db, monkeypatch, capsys):
    """--list must report and stop: no Claude calls, no spend."""
    conn = SearchConn()
    term = f'SINCE {backfill._imap_date(datetime.now(timezone.utc) - timedelta(days=180))} FROM "greenhouse.io"'
    conn.per_term = {term: [5]}
    monkeypatch.setattr(MailClient, "__enter__", lambda self: setattr(self, "_conn", conn) or self)
    monkeypatch.setattr(MailClient, "__exit__", lambda self, *a: None)

    def boom(*a, **k):
        raise AssertionError("classifier must not be constructed for --list")

    monkeypatch.setattr("jobmail.classifier.ClaudeClassifier", boom)
    cfg.imap_user, cfg.anthropic_api_key = "me@gmail.com", "k"
    db.close()

    assert backfill.run(cfg, 180, "[Gmail]/All Mail", True, 0, True) == 0
    out = capsys.readouterr().out
    assert "1 message(s) to classify" in out
    assert "estimated cost" in out
    assert conn.body_fetches == []      # never downloaded a body


def test_already_seen_messages_are_not_repaid_for(cfg, db, monkeypatch, capsys):
    conn = SearchConn()
    term = f'SINCE {backfill._imap_date(datetime.now(timezone.utc) - timedelta(days=180))} FROM "greenhouse.io"'
    conn.per_term = {term: [5]}
    db.insert_message(uid="5", folder="INBOX", message_id="<m5@acme>", direction="inbound")
    monkeypatch.setattr(MailClient, "__enter__", lambda self: setattr(self, "_conn", conn) or self)
    monkeypatch.setattr(MailClient, "__exit__", lambda self, *a: None)
    cfg.imap_user, cfg.anthropic_api_key = "me@gmail.com", "k"
    db.close()

    assert backfill.run(cfg, 180, "[Gmail]/All Mail", True, 0, False) == 0
    out = capsys.readouterr().out
    assert "1 already in the database" in out
    assert "Nothing to do" in out
