"""Alert delivery: Telegram bot messages and plain-text email."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import Config

log = logging.getLogger(__name__)


class Alerter:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ---------------------------------------------------------------- telegram
    def telegram(self, text: str) -> bool:
        if not (self.cfg.telegram_bot_token and self.cfg.telegram_chat_id):
            return False
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
        # Telegram caps messages at 4096 chars.
        for chunk in _chunks(text, 4000):
            try:
                r = httpx.post(
                    url,
                    json={
                        "chat_id": self.cfg.telegram_chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
                r.raise_for_status()
            except Exception as exc:
                log.error("Telegram send failed: %s", exc)
                return False
        return True

    # ------------------------------------------------------------------- email
    def email(self, subject: str, body: str) -> bool:
        if not (self.cfg.alert_email_to and self.cfg.smtp_host and self.cfg.smtp_user):
            return False
        msg = EmailMessage()
        msg["From"] = self.cfg.alert_email_from or self.cfg.smtp_user
        msg["To"] = self.cfg.alert_email_to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            if self.cfg.smtp_port == 465:
                with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as s:
                    s.login(self.cfg.smtp_user, self.cfg.smtp_password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as s:
                    s.starttls()
                    s.login(self.cfg.smtp_user, self.cfg.smtp_password)
                    s.send_message(msg)
            return True
        except Exception as exc:
            log.error("Email send failed: %s", exc)
            return False

    # ---------------------------------------------------------------- combined
    def send(self, subject: str, text_plain: str, text_html: str | None = None) -> None:
        """Send to every configured channel. Never raises."""
        if not self.cfg.alerts_enabled or self.cfg.dry_run:
            log.info("[dry-run/disabled] alert: %s\n%s", subject, text_plain)
            return
        sent_tg = self.telegram(text_html or _escape(text_plain))
        sent_mail = self.email(subject, text_plain)
        if not (sent_tg or sent_mail):
            log.warning("Alert not delivered on any channel: %s", subject)


def _chunks(text: str, size: int):
    while text:
        yield text[:size]
        text = text[size:]


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------- formatting

URGENCY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟢"}


# What each message type is called when it lands on your phone. "Reply needed"
# is wrong for an offer that asks nothing of you.
TYPE_HEADLINE = {
    "offer": ("🎉", "Offer"),
    "interview_request": ("📅", "Interview request"),
    "scheduling": ("📅", "Scheduling"),
    "assessment": ("📝", "Assessment"),
    "recruiter_outreach": ("💬", "Recruiter"),
    "reference_or_background": ("🔎", "Background check"),
    "follow_up": ("📬", "Update"),
    "rejection": ("🚫", "Rejection"),
}


def format_alert(m, dashboard_url: str = "") -> tuple[str, str, str]:
    """Build an alert for any noteworthy message, reply required or not.

    The headline follows the event. "Reply needed" is simply wrong for an offer
    that asks nothing of you, and a silent phone is wrong for it too.
    """
    needs_reply = bool(m["needs_reply"]) and not m["replied_at"] and not m["superseded_by"]
    icon, label = TYPE_HEADLINE.get(m["message_type"] or "", ("", ""))
    if needs_reply:
        icon = URGENCY_ICON.get(m["urgency"] or "low", "🟢")
        label = "Reply needed"
    elif not label:
        icon, label = "📨", "Job mail"

    role = m["role"] or ""
    title = (m["company"] or "Unknown company") + (f" — {role}" if role else "")
    who = f"{m['from_name']} <{m['from_addr']}>" if m["from_name"] else (m["from_addr"] or "")
    urg = f" ({m['urgency']})" if needs_reply and m["urgency"] else ""

    plain = (
        f"{icon} {label}{urg}: {title}\n"
        f"From: {who}\n"
        f"Subject: {m['subject'] or ''}\n\n"
        f"{m['summary'] or ''}\n"
    )
    html = (
        f"{icon} <b>{_escape(label)}</b>{_escape(urg)}\n"
        f"<b>{_escape(title)}</b>\n"
        f"From: {_escape(who)}\n"
        f"Subject: {_escape(m['subject'] or '')}\n\n"
        f"{_escape(m['summary'] or '')}"
    )
    if m["action_needed"]:
        plain += f"\nAction: {m['action_needed']}\n"
        html += f"\n\n<i>Action:</i> {_escape(m['action_needed'])}"
    if dashboard_url:
        plain += f"\n{dashboard_url}"
        html += f'\n\n<a href="{dashboard_url}">Open dashboard</a>'
    return f"[jobmail] {label}: {title}", plain, html


def format_needs_reply(company: str, role: str, urgency: str, summary: str, action: str,
                       from_name: str, from_addr: str, subject: str, dashboard_url: str = "") -> tuple[str, str, str]:
    """Return (email_subject, plain_text, telegram_html)."""
    icon = URGENCY_ICON.get(urgency, "🟢")
    who = f"{from_name} <{from_addr}>" if from_name else from_addr
    title = f"{company or 'Unknown company'}" + (f" — {role}" if role else "")
    plain = (
        f"{icon} Reply needed ({urgency}): {title}\n"
        f"From: {who}\n"
        f"Subject: {subject}\n\n"
        f"{summary}\n\n"
        f"Action: {action or 'Respond.'}\n"
    )
    if dashboard_url:
        plain += f"\n{dashboard_url}"
    html = (
        f"{icon} <b>Reply needed</b> ({urgency})\n"
        f"<b>{_escape(title)}</b>\n"
        f"From: {_escape(who)}\n"
        f"Subject: {_escape(subject)}\n\n"
        f"{_escape(summary)}\n\n"
        f"<i>Action:</i> {_escape(action or 'Respond.')}"
    )
    if dashboard_url:
        html += f"\n\n<a href=\"{dashboard_url}\">Open dashboard</a>"
    return f"[jobmail] Reply needed: {title}", plain, html


def format_upcoming(company: str, role: str, when: str, description: str) -> tuple[str, str, str]:
    title = f"{company or 'Unknown company'}" + (f" — {role}" if role else "")
    plain = f"📅 Upcoming: {title}\n{description}\nWhen: {when}\n"
    html = f"📅 <b>Upcoming</b>\n<b>{_escape(title)}</b>\n{_escape(description)}\nWhen: {_escape(when)}"
    return f"[jobmail] Upcoming: {title}", plain, html


def format_stale(apps: list[dict]) -> tuple[str, str, str]:
    lines = [f"• {a['company']} — {a.get('role') or '?'} ({a['stage']}, last activity {a['last']})" for a in apps]
    plain = "⏳ No activity in a while:\n" + "\n".join(lines)
    html = "⏳ <b>No activity in a while</b>\n" + "\n".join(_escape(l) for l in lines)
    return f"[jobmail] {len(apps)} stale application(s)", plain, html
