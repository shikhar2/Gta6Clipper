"""Twitch Helix API (finding live streams) and anonymous Twitch chat reader."""
from __future__ import annotations

import logging
import random
import socket
import ssl
import threading
import time
from typing import Callable, Iterator

import requests

log = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"


class TwitchAPI:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = ""
        self._expires = 0.0
        self._s = requests.Session()

    def _auth(self) -> None:
        r = self._s.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Twitch login failed ({r.status_code}). Check TWITCH_CLIENT_ID and "
                f"TWITCH_CLIENT_SECRET in .env. Reply: {r.text[:200]}"
            )
        body = r.json()
        self._token = body["access_token"]
        self._expires = time.time() + int(body.get("expires_in", 3600)) - 120

    def _get(self, path: str, params) -> dict:
        for attempt in range(2):
            if not self._token or time.time() > self._expires:
                self._auth()
            r = self._s.get(
                HELIX + path,
                params=params,
                headers={"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"},
                timeout=20,
            )
            if r.status_code == 401 and attempt == 0:
                self._token = ""
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 5))
                time.sleep(max(1.0, reset - time.time()))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Twitch API {path} failed")

    def game_id(self, name: str) -> str | None:
        data = self._get("/games", {"name": name}).get("data") or []
        if data:
            return data[0]["id"]
        found = self._get("/search/categories", {"query": name, "first": 5}).get("data") or []
        for g in found:
            if g["name"].lower() == name.lower():
                return g["id"]
        return found[0]["id"] if found else None

    def live_streams(self, game_id: str, languages: list[str], max_pages: int) -> Iterator[dict]:
        """Yields live streams of a game (highest viewers first)."""
        cursor = None
        for _ in range(max_pages):
            params: list[tuple[str, str]] = [("game_id", game_id), ("type", "live"), ("first", "100")]
            params += [("language", lang) for lang in languages]
            if cursor:
                params.append(("after", cursor))
            body = self._get("/streams", params)
            for s in body.get("data") or []:
                yield s
            cursor = (body.get("pagination") or {}).get("cursor")
            if not cursor:
                return

    def streams_by_login(self, logins: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(logins), 100):
            chunk = logins[i : i + 100]
            params = [("user_login", x) for x in chunk] + [("type", "live")]
            out += self._get("/streams", params).get("data") or []
        return out


class TwitchChat(threading.Thread):
    """Reads a channel's chat anonymously (no login needed) and calls on_message(ts, text)."""

    def __init__(self, channel: str, on_message: Callable[[float, str], None]):
        super().__init__(daemon=True, name=f"chat-{channel}")
        self.channel = channel.lower()
        self.on_message = on_message
        self._stop = threading.Event()
        self.messages = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 2
            except Exception as e:  # network hiccup: reconnect
                log.warning("chat %s disconnected: %s", self.channel, e)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 60)

    def _session(self) -> None:
        raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=30)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
        sock.settimeout(5)
        nick = f"justinfan{random.randint(10000, 99999)}"
        sock.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        buf = b""
        last_data = time.time()
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    if time.time() - last_data > 360:
                        raise ConnectionError("no data for 6 minutes")
                    continue
                if not chunk:
                    raise ConnectionError("closed by server")
                last_data = time.time()
                buf += chunk
                *lines, buf = buf.split(b"\r\n")
                for line in lines:
                    self._handle(sock, line.decode("utf-8", "replace"))
        finally:
            sock.close()

    def _handle(self, sock, line: str) -> None:
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        # :user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
        if " PRIVMSG " in line:
            parts = line.split(" :", 1)
            if len(parts) == 2:
                self.messages += 1
                self.on_message(time.time(), parts[1])
