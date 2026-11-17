"""Minimal Telegram Bot API client: messages, videos, buttons and commands."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

import requests

log = logging.getLogger(__name__)

Buttons = list[list[tuple[str, str]]]  # rows of (label, callback_data)


def _markup(buttons: Buttons | None) -> str | None:
    if not buttons:
        return None
    return json.dumps({"inline_keyboard": [
        [{"text": label, "callback_data": data[:64]} for label, data in row] for row in buttons
    ]})


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self._s = requests.Session()
        self._send_lock = threading.Lock()

    def _call(self, method: str, http_timeout: float = 30, files=None, **params):
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(4):
            try:
                with self._send_lock if method != "getUpdates" else _NoLock():
                    r = self._s.post(f"{self.base}/{method}", data=params, files=files,
                                     timeout=http_timeout)
                body = r.json()
                if body.get("ok"):
                    return body["result"]
                if r.status_code == 429:
                    time.sleep(int(body.get("parameters", {}).get("retry_after", 5)) + 1)
                    continue
                log.error("telegram %s failed: %s", method, body.get("description"))
                return None
            except (requests.RequestException, ValueError) as e:
                log.warning("telegram %s error (try %d): %s", method, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
        return None

    def send(self, text: str, buttons: Buttons | None = None):
        return self._call("sendMessage", chat_id=self.chat_id, text=text[:4096],
                          parse_mode="HTML", disable_web_page_preview="true",
                          reply_markup=_markup(buttons))

    def send_photo_url(self, url: str, caption: str, buttons: Buttons | None = None):
        res = self._call("sendPhoto", chat_id=self.chat_id, photo=url, caption=caption[:1024],
                         parse_mode="HTML", reply_markup=_markup(buttons))
        return res or self.send(caption, buttons)

    def send_video(self, path: Path, caption: str, buttons: Buttons | None = None):
        with open(path, "rb") as f:
            return self._call("sendVideo", http_timeout=300, files={"video": (path.name, f, "video/mp4")},
                              chat_id=self.chat_id, caption=caption[:1024], parse_mode="HTML",
                              supports_streaming="true", width="1080", height="1920",
                              reply_markup=_markup(buttons))

    def answer(self, callback_id: str, text: str = ""):
        return self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])

    def updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return self._call("getUpdates", http_timeout=timeout + 15, offset=offset,
                          timeout=str(timeout)) or []

    def poll_forever(self, on_command: Callable[[str, list[str]], None],
                     on_button: Callable[[str, str], None], stop: threading.Event) -> None:
        offset = None
        while not stop.is_set():
            for upd in self.updates(offset):
                offset = upd["update_id"] + 1
                try:
                    self._dispatch(upd, on_command, on_button)
                except Exception:
                    log.exception("error handling telegram update")

    def _dispatch(self, upd: dict, on_command, on_button) -> None:
        if "callback_query" in upd:
            q = upd["callback_query"]
            chat = str(q.get("message", {}).get("chat", {}).get("id", ""))
            if chat != self.chat_id:
                return  # only you can press buttons
            self.answer(q["id"], "OK")
            on_button(q.get("data", ""), q["id"])
            return
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id", "")) != self.chat_id:
            return  # ignore everyone else
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            parts = text.split()
            on_command(parts[0].split("@")[0].lower(), parts[1:])


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def print_chat_ids(token: str) -> None:
    """Helper for setup: shows the chat id of whoever messaged the bot."""
    base = f"https://api.telegram.org/bot{token}"
    print("Open Telegram, send any message (like 'hi') to your bot. Waiting...")
    offset = None
    while True:
        r = requests.post(f"{base}/getUpdates", data={"timeout": 50, "offset": offset}, timeout=65)
        body = r.json()
        if not body.get("ok"):
            raise SystemExit(f"Telegram error: {body.get('description')} (check TELEGRAM_BOT_TOKEN)")
        for upd in body["result"]:
            offset = upd["update_id"] + 1
            chat = (upd.get("message") or {}).get("chat")
            if chat:
                print(f"\nYour TELEGRAM_CHAT_ID is: {chat['id']}")
                print("Put it in your .env file, then run:  python -m clipper run")
                return
