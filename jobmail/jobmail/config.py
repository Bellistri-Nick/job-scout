"""Configuration loaded from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def use_utf8_io() -> None:
    """Make stdout/stderr UTF-8 so arbitrary email text cannot kill a run.

    Summaries and subjects come from other people's mail: curly quotes, em
    dashes, accented names, emoji. Under a latin-1 locale -- which is what a
    systemd unit or a bare `sudo -u` shell can easily give you -- printing any
    of that raises UnicodeEncodeError and takes the whole poll down. errors is
    "replace" so a stray byte degrades one character instead of the process.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a reconfigurable stream
            pass


@dataclass
class Config:
    # --- Mailbox (Gmail defaults; every value is env-driven) ---
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""

    # --- Claude ---
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str = "low"  # classification does not repay deeper thinking

    # --- Storage ---
    data_dir: Path = field(default_factory=lambda: Path.home() / ".jobmail")
    db_path: Path | None = None

    # --- Alerts ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_email_to: str = ""
    alert_email_from: str = ""
    alerts_enabled: bool = True
    # Types that always warrant an alert even when no reply is required. An
    # offer with a Calendly link needs no reply and absolutely needs your
    # attention; "must I type a response" was the wrong test.
    alert_on_types: frozenset[str] = frozenset({
        "recruiter_outreach", "interview_request", "scheduling",
        "assessment", "offer", "reference_or_background", "follow_up",
    })

    # --- Obsidian ---
    obsidian_vault_path: Path | None = None
    obsidian_subfolder: str = "Applications"

    # --- Behaviour ---
    stale_after_days: int = 14
    deadline_alert_hours: int = 72  # must span the weekend gap in the poll schedule
    owner_name: str = ""  # used in prompts so Claude knows who "you" is
    dry_run: bool = False  # classify but do not alert / write Obsidian

    # --- Dashboard ---
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080

    @classmethod
    def load(cls, dotenv_path: str | Path | None = None) -> "Config":
        load_dotenv(dotenv_path, override=False)
        data_dir = Path(_env("JOBMAIL_DATA_DIR", str(Path.home() / ".jobmail"))).expanduser()
        vault = _env("OBSIDIAN_VAULT_PATH")
        cfg = cls(
            imap_host=_env("IMAP_HOST", "imap.gmail.com"),
            imap_port=int(_env("IMAP_PORT", "993")),
            imap_user=_env("IMAP_USER"),
            imap_password=_env("IMAP_PASSWORD"),
            imap_folder=_env("IMAP_FOLDER", "INBOX"),
            smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(_env("SMTP_PORT", "465")),
            smtp_user=_env("SMTP_USER") or _env("IMAP_USER"),
            smtp_password=_env("SMTP_PASSWORD") or _env("IMAP_PASSWORD"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            claude_model=_env("CLAUDE_MODEL", "claude-sonnet-5-5"),
            claude_effort=_env("CLAUDE_EFFORT", "low"),
            data_dir=data_dir,
            db_path=Path(_env("JOBMAIL_DB_PATH", str(data_dir / "jobmail.db"))).expanduser(),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
            alert_email_to=_env("ALERT_EMAIL_TO"),
            alert_email_from=_env("ALERT_EMAIL_FROM") or _env("IMAP_USER"),
            alerts_enabled=_env_bool("ALERTS_ENABLED", True),
            alert_on_types=frozenset(
                t.strip() for t in _env(
                    "ALERT_ON_TYPES",
                    "recruiter_outreach,interview_request,scheduling,assessment,"
                    "offer,reference_or_background,follow_up",
                ).split(",") if t.strip()
            ),
            obsidian_vault_path=Path(vault).expanduser() if vault else None,
            obsidian_subfolder=_env("OBSIDIAN_SUBFOLDER", "Applications"),
            stale_after_days=int(_env("STALE_AFTER_DAYS", "14")),
            deadline_alert_hours=int(_env("DEADLINE_ALERT_HOURS", "72")),
            owner_name=_env("OWNER_NAME"),
            dry_run=_env_bool("DRY_RUN", False),
            dashboard_host=_env("DASHBOARD_HOST", "0.0.0.0"),
            dashboard_port=int(_env("DASHBOARD_PORT", "8080")),
        )
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        return cfg
