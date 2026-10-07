"""Telegram setup helper. No network: the API call is stubbed."""

from __future__ import annotations

import pytest

from jobmail import telegram_setup as ts


def _updates(*chats) -> dict:
    return {"ok": True, "result": [
        {"update_id": i, "message": {"message_id": 900 + i, "from": {"id": 111},
                                     "chat": c, "text": "hi"}}
        for i, c in enumerate(chats)
    ]}


PRIVATE = {"id": 123456789, "type": "private", "first_name": "Sam"}
GROUP = {"id": -100200300, "type": "group", "title": "Job hunt"}


class FakeAPI:
    def __init__(self, updates=None):
        self.updates = updates if updates is not None else _updates(PRIVATE)
        self.sent: list[dict] = []

    def __call__(self, token, method, params=None, timeout=20.0):
        if method == "getMe":
            return {"ok": True, "result": {"username": "myjobs_bot"}}
        if method == "getUpdates":
            return self.updates
        if method == "sendMessage":
            self.sent.append(params)
            return {"ok": True}
        raise AssertionError(method)


@pytest.fixture
def out():
    lines: list[str] = []
    return lines, lines.append


def test_chat_id_is_the_chat_not_the_sender_or_message():
    """The whole point of this helper: three ids in the payload, one right answer."""
    chats = ts.chats_from_updates(_updates(PRIVATE))
    assert [c["id"] for c in chats] == [123456789]  # not 111 (from), not 900 (message_id)


def test_chats_are_deduped_newest_first():
    chats = ts.chats_from_updates(_updates(PRIVATE, GROUP, PRIVATE))
    assert [c["id"] for c in chats] == [-100200300, 123456789]


def test_discovers_chat_and_sends_test(cfg, monkeypatch, out):
    lines, printer = out
    api = FakeAPI()
    monkeypatch.setattr(ts, "api", api)
    cfg.telegram_bot_token, cfg.telegram_chat_id = "123:AAF", ""

    assert ts.run(cfg, printer=printer) == 0
    text = "\n".join(lines)
    assert "@myjobs_bot" in text
    assert "TELEGRAM_CHAT_ID=123456789" in text
    assert api.sent and api.sent[0]["chat_id"] == "123456789"


def test_writes_chat_id_into_env(cfg, tmp_path, monkeypatch, out):
    lines, printer = out
    monkeypatch.setattr(ts, "api", FakeAPI())
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=123:AAF\nTELEGRAM_CHAT_ID=\nSTALE_AFTER_DAYS=14\n",
                   encoding="utf-8")
    cfg.telegram_bot_token, cfg.telegram_chat_id = "123:AAF", ""

    assert ts.run(cfg, env_path=env, write=True, printer=printer) == 0
    body = env.read_text(encoding="utf-8")
    assert "TELEGRAM_CHAT_ID=123456789" in body
    assert body.count("TELEGRAM_CHAT_ID") == 1   # replaced, not appended
    assert "STALE_AFTER_DAYS=14" in body         # rest of the file untouched


def test_no_messages_yet_explains_what_to_do(cfg, monkeypatch, out):
    lines, printer = out
    monkeypatch.setattr(ts, "api", FakeAPI(updates={"ok": True, "result": []}))
    cfg.telegram_bot_token, cfg.telegram_chat_id = "123:AAF", ""

    assert ts.run(cfg, printer=printer) == 1
    text = "\n".join(lines)
    assert "press Start" in text and "@myjobs_bot" in text


def test_multiple_chats_asks_rather_than_guessing(cfg, monkeypatch, out):
    lines, printer = out
    monkeypatch.setattr(ts, "api", FakeAPI(_updates(PRIVATE, GROUP)))
    cfg.telegram_bot_token, cfg.telegram_chat_id = "123:AAF", ""

    assert ts.run(cfg, printer=printer) == 1
    text = "\n".join(lines)
    assert "123456789" in text and "-100200300" in text and "Job hunt" in text


def test_existing_chat_id_skips_discovery(cfg, monkeypatch, out):
    lines, printer = out
    api = FakeAPI(updates={"ok": True, "result": []})  # would fail discovery
    monkeypatch.setattr(ts, "api", api)
    cfg.telegram_bot_token, cfg.telegram_chat_id = "123:AAF", "555"

    assert ts.run(cfg, printer=printer) == 0
    assert api.sent[0]["chat_id"] == "555"


def test_missing_token_points_at_botfather(cfg, out):
    lines, printer = out
    cfg.telegram_bot_token = ""
    assert ts.run(cfg, printer=printer) == 2
    assert "@BotFather" in "\n".join(lines)
