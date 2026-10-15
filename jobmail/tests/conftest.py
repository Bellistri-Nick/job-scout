from __future__ import annotations

from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from jobmail.classifier import Classification
from jobmail.config import Config
from jobmail.db import Database
from jobmail.mail import ParsedMessage, parse_message


class StubClassifier:
    """Returns canned classifications keyed by subject substring."""

    def __init__(self, table: dict[str, dict]):
        self.table = table
        self.calls = 0

    def classify(self, msg: ParsedMessage) -> Classification:
        self.calls += 1
        for key, d in self.table.items():
            if key.lower() in msg.subject.lower():
                base = {"is_job_related": True, "sender_is_human": True, "needs_reply": False,
                        "urgency": "low", "summary": f"stub: {key}", "action_needed": "", "key_dates": [],
                        "company": "", "role": "", "message_type": "other_job_related"}
                base.update(d)
                return Classification.from_dict(base)
        return Classification.from_dict({"is_job_related": False, "message_type": "not_job_related",
                                         "company": "", "role": "", "summary": "noise"})


class CapturingAlerter:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, subject, plain, html=None):
        self.sent.append((subject, plain))


def make_email(uid: int, *, frm: str, to: str = "me@gmail.com", subject: str, body: str,
               msg_id: str, in_reply_to: str | None = None, references: list[str] | None = None,
               when: datetime | None = None, folder: str = "INBOX", html: bool = False) -> ParsedMessage:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = msg_id
    m["Date"] = format_datetime(when or datetime.now(timezone.utc))
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = " ".join(references)
    if html:
        m.set_content("plain fallback")
        m.add_alternative(f"<html><body><p>{body}</p><p>Second&nbsp;para</p></body></html>", subtype="html")
    else:
        m.set_content(body)
    return parse_message(m.as_bytes(), uid, folder)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "t.db",
               obsidian_vault_path=tmp_path / "vault", alerts_enabled=True)
    c.data_dir.mkdir(parents=True)
    return c


@pytest.fixture
def db(cfg: Config) -> Database:
    d = Database(cfg.db_path)
    yield d
    d.close()


@pytest.fixture
def days_ago():
    return lambda n: datetime.now(timezone.utc) - timedelta(days=n)
