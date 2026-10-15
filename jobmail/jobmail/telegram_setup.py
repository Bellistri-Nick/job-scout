"""Telegram setup: `python -m jobmail.telegram_setup`.

Verifies the bot token, finds your chat id for you, and sends a test alert so
the whole path is proven before the Pi ever runs. Stdlib only, so it runs
wherever preflight does.

The chat id is the fiddly part done by hand: getUpdates returns a message id,
a sender id and a chat id, and only the last one is what the config wants.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config, use_utf8_io

API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def api(token: str, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    url = f"{API}/bot{token}/{method}"
    data = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if exc.code == 401:
            raise TelegramError("token rejected (401). Copy it again from @BotFather.") from exc
        raise TelegramError(f"HTTP {exc.code}: {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise TelegramError(f"could not reach api.telegram.org: {exc.reason}") from exc


def chats_from_updates(updates: dict) -> list[dict]:
    """Unique chats seen in getUpdates, newest first."""
    seen: dict[int, dict] = {}
    for upd in updates.get("result", []):
        for key in ("message", "edited_message", "channel_post"):
            chat = (upd.get(key) or {}).get("chat")
            if chat and chat.get("id") is not None:
                seen[chat["id"]] = chat
    return list(reversed(list(seen.values())))


def _label(chat: dict) -> str:
    name = chat.get("title") or " ".join(
        p for p in (chat.get("first_name"), chat.get("last_name")) if p
    ) or chat.get("username") or "?"
    return f"{name} ({chat.get('type', '?')})"


def write_chat_id(env_path: Path, chat_id: int) -> bool:
    """Set TELEGRAM_CHAT_ID in .env, replacing any existing value."""
    if not env_path.exists():
        return False
    text = env_path.read_text(encoding="utf-8")
    line = f"TELEGRAM_CHAT_ID={chat_id}"
    new, n = re.subn(r"(?m)^TELEGRAM_CHAT_ID=.*$", line, text)
    if n == 0:
        new = text.rstrip("\n") + f"\n{line}\n"
    env_path.write_text(new, encoding="utf-8")
    return True


def run(cfg: Config, env_path: Path | None = None, write: bool = False, printer=print) -> int:
    token = cfg.telegram_bot_token
    if not token:
        printer("\n  FAIL  TELEGRAM_BOT_TOKEN is not set in .env")
        printer("\n  Get one: open Telegram, message @BotFather, send /newbot, follow the")
        printer("  prompts, and copy the token it gives you (looks like 123456789:AAF...).\n")
        return 2

    me = api(token, "getMe").get("result", {})
    printer(f"\n  ok    token valid - bot is @{me.get('username', '?')}\n")

    chat_id = cfg.telegram_chat_id
    if not chat_id:
        chats = chats_from_updates(api(token, "getUpdates"))
        if not chats:
            printer("  FAIL  no chats found.\n")
            printer(f"  Open Telegram, find @{me.get('username', 'your bot')}, press Start and")
            printer("  send it any message. Then run this again.\n")
            printer("  (Telegram only reveals your chat id once you've messaged the bot first,")
            printer("  and it forgets messages older than 24 hours.)\n")
            return 1
        if len(chats) > 1:
            printer("  More than one chat has messaged this bot:\n")
            for c in chats:
                printer(f"    {c['id']}   {_label(c)}")
            printer("\n  Put the one you want in TELEGRAM_CHAT_ID and run again.\n")
            return 1
        chat = chats[0]
        chat_id = str(chat["id"])
        printer(f"  ok    found your chat: {_label(chat)}")
        if write and env_path and write_chat_id(env_path, chat["id"]):
            printer(f"  ok    wrote TELEGRAM_CHAT_ID={chat_id} to {env_path}")
        else:
            printer(f"\n  Add this line to .env:\n\n    TELEGRAM_CHAT_ID={chat_id}\n")
            printer("  (or re-run with --write and I'll do it)\n")

    api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": "\U0001f7e2 <b>jobmail</b> is wired up.\nThis is what an alert will look like.",
        "parse_mode": "HTML",
    })
    printer(f"  ok    test message sent to {chat_id} - check your phone\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail-telegram",
                                 description="Verify the Telegram bot token, find the chat id, send a test.")
    ap.add_argument("--env", help="path to .env file")
    ap.add_argument("--write", action="store_true", help="write the discovered chat id into .env")
    args = ap.parse_args(argv)
    use_utf8_io()
    env_path = Path(args.env) if args.env else Path(".env")
    try:
        return run(Config.load(args.env), env_path=env_path, write=args.write)
    except TelegramError as exc:
        print(f"\n  FAIL  {exc}\n")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
