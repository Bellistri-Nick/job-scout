"""Optional Telegram push, fired after the digest is actually delivered.

The email is the deliverable; this is the tap on the shoulder that says it landed,
with enough detail to decide whether to open it now or at lunch. Silent no-op when
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are unset, and it never breaks a scan: a
push failure is logged and swallowed, because the mail already went out.

Setup is two minutes:
  1. Message @BotFather on Telegram, send /newbot, copy the token.
  2. Send your new bot any message at all.
  3. python run.py telegram-setup    (finds your chat id and prints it)
"""
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, params, token=None, timeout=15):
    token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"    ! telegram {method} failed ({e.code}): {body}")
    except Exception as e:
        print(f"    ! telegram {method} failed ({type(e).__name__}: {e})")
    return None


def configured():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send(text, chat_id=None, token=None):
    """Send one HTML-formatted message. Returns True if Telegram accepted it."""
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        return False
    resp = _call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }, token=token)
    return bool(resp and resp.get("ok"))


def find_chat_id(token=None):
    """Read recent updates and return the chat id of whoever messaged the bot."""
    resp = _call("getUpdates", {"limit": 10}, token=token)
    if not resp or not resp.get("ok"):
        return None, None
    for update in reversed(resp.get("result", [])):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = chat.get("username") or chat.get("first_name") or chat.get("title") or ""
            return str(chat["id"]), name
    return None, None


def _esc(s):
    return html.escape(str(s or ""))


def digest_delivered(strong, look, rest, recipients, stats=None):
    """The message that goes out once the digest email is on its way."""
    total = len(strong) + len(look) + len(rest)
    if not total:
        return "<b>Job scan</b>\nNothing new today."

    lines = ["<b>Job digest sent</b>"]
    counts = f"{len(strong)} strong · {len(look)} worth a look · {len(rest)} ranked below"
    lines.append(counts)

    top = (strong or look or rest)[0]
    lines.append("")
    url = (top.url or "").strip()
    title = _esc(top.title)
    headline = f'<a href="{_esc(url)}">{title}</a>' if url.startswith("https://") else title
    lines.append(f"Top: {headline}")
    detail = " · ".join(x for x in (_esc(top.company), _esc(top.location), _esc(top.comp_text)) if x)
    if detail:
        lines.append(detail)
    if top.why:
        lines.append(f"<i>{_esc(top.why)}</i>")

    for job in (strong or look)[1:4]:
        u = (job.url or "").strip()
        t = _esc(job.title)
        link = f'<a href="{_esc(u)}">{t}</a>' if u.startswith("https://") else t
        lines.append(f"• {link} · {_esc(job.company)}")

    lines.append("")
    scanned = f"{stats['fetched']} postings scanned" if stats else f"{total} roles"
    lines.append(f"<i>{total} in the email · {scanned} · to {_esc(', '.join(recipients))}</i>")
    return "\n".join(lines)
