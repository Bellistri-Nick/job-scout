"""SMTP delivery. Gmail with an app password is the path of least resistance."""
import os
import smtplib
import ssl
from email.message import EmailMessage


def _cfg():
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "465")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from": os.getenv("MAIL_FROM") or os.getenv("SMTP_USER", ""),
        "to": os.getenv("MAIL_TO", ""),
        "cc": os.getenv("MAIL_CC", ""),
    }


def send(subject, html_body, text_body, to=None):
    cfg = _cfg()
    recipient = to or cfg["to"]
    missing = [k for k in ("user", "password") if not cfg[k]]
    if missing or not recipient:
        raise RuntimeError(
            "Email not configured. Set SMTP_USER, SMTP_PASSWORD, and MAIL_TO in .env "
            "(missing: {})".format(", ".join(missing + ([] if recipient else ["MAIL_TO"]))))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = recipient
    recipients = [r.strip() for r in recipient.split(",") if r.strip()]
    if cfg["cc"]:
        msg["Cc"] = cfg["cc"]
        recipients += [r.strip() for r in cfg["cc"].split(",") if r.strip()]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=30) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg, to_addrs=recipients)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg, to_addrs=recipients)
    return recipients


def load_dotenv(path):
    """Minimal .env loader so the Pi needs no extra packages."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)
