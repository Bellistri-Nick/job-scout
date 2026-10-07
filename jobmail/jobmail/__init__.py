"""jobmail — a read-only job-search inbox agent.

Polls a dedicated IMAP mailbox, classifies each message with Claude,
tracks applications and correspondence in SQLite, alerts via Telegram and
email when a reply is needed, and mirrors application history into an
Obsidian vault.
"""

__version__ = "0.1.0"
