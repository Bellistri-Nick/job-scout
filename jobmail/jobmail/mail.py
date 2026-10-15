"""IMAP fetching and message parsing (stdlib only)."""

from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

log = logging.getLogger(__name__)

# Checked in order; anything containing "sent" is used as a fallback.
SENT_FOLDER_CANDIDATES = ["[Gmail]/Sent Mail", "Sent", "Sent Items", "Sent Messages", "INBOX.Sent"]


@dataclass
class ParsedMessage:
    uid: int
    folder: str
    message_id: str
    in_reply_to: str
    references: list[str]
    from_addr: str
    from_name: str
    to_addr: str
    subject: str
    sent_at: str  # ISO 8601, UTC
    body_text: str
    raw: bytes = field(repr=False, default=b"")

    @property
    def from_domain(self) -> str:
        return self.from_addr.rsplit("@", 1)[-1].lower() if "@" in self.from_addr else ""


# ----------------------------------------------------------------- parsing


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_body(msg: Message) -> str:
    """Prefer text/plain; fall back to a de-tagged text/html part."""
    plain, html = None, None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain and plain.strip():
        return plain.strip()
    if html:
        return _html_to_text(html)
    return ""


def _parse_date(raw: str | None) -> str:
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _split_ids(raw: str | None) -> list[str]:
    return re.findall(r"<[^<>]+>", raw or "")


def parse_message(raw: bytes, uid: int, folder: str) -> ParsedMessage:
    msg = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(_decode(msg.get("From")))
    _, to_addr = parseaddr(_decode(msg.get("To")))
    return ParsedMessage(
        uid=uid,
        folder=folder,
        message_id=(msg.get("Message-ID") or "").strip(),
        in_reply_to=(msg.get("In-Reply-To") or "").strip(),
        references=_split_ids(msg.get("References")),
        from_addr=from_addr.lower(),
        from_name=from_name,
        to_addr=to_addr.lower(),
        subject=_decode(msg.get("Subject")),
        sent_at=_parse_date(msg.get("Date")),
        body_text=extract_body(msg),
        raw=raw,
    )


# ------------------------------------------------------------------ fetching


class MailClient:
    """Thin IMAP wrapper that fetches messages newer than a stored UID."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self.host, self.port, self.user, self.password = host, port, user, password
        self._conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "MailClient":
        self._conn = imaplib.IMAP4_SSL(self.host, self.port)
        self._conn.login(self.user, self.password)
        return self

    def __exit__(self, *exc) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass

    @property
    def conn(self) -> imaplib.IMAP4_SSL:
        assert self._conn is not None, "MailClient must be used as a context manager"
        return self._conn

    def list_folders(self) -> list[str]:
        status, data = self.conn.list()
        names: list[str] = []
        if status != "OK":
            return names
        for line in data:
            if not line:
                continue
            m = re.search(rb'"?([^"]*)"?$', line.strip())
            if m:
                names.append(m.group(1).decode(errors="replace"))
        return names

    def select(self, folder: str) -> int | None:
        """Select folder read-only; return UIDVALIDITY or None if the folder is missing."""
        status, _ = self.conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            log.warning("Folder %r not found on server", folder)
            return None
        status, data = self.conn.response("UIDVALIDITY")
        try:
            return int(data[0])
        except Exception:
            return None

    def fetch_since_uid(self, folder: str, last_uid: int, limit: int = 200) -> list[ParsedMessage]:
        """Return messages with UID > last_uid, oldest first.

        Stops at the first message that won't fetch or parse rather than
        skipping it. The caller advances its watermark per message, so skipping
        would let the next success step over the bad one and drop it silently.
        A message that fails forever stalls the folder, which is loud in the log
        rather than invisible in the mailbox.
        """
        if self.select(folder) is None:
            return []
        status, data = self.conn.uid("search", None, f"UID {last_uid + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []
        uids = [int(u) for u in data[0].split() if int(u) > last_uid]
        uids.sort()
        out: list[ParsedMessage] = []
        for uid in uids[:limit]:
            status, parts = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                log.error("Could not fetch UID %s in %s; stopping here to retry next run", uid, folder)
                break
            try:
                out.append(parse_message(parts[0][1], uid, folder))
            except Exception as exc:
                log.exception("Failed to parse UID %s in %s; stopping here: %s", uid, folder, exc)
                break
        return out
