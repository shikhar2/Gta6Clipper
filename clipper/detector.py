"""Finds 'clip-worthy' moments from chat speed and loud audio."""
from __future__ import annotations

import re
import statistics
import threading
from collections import defaultdict
from typing import Callable

HYPE_WORDS = {
    "lul", "lol", "lmao", "lmfao", "omegalul", "kekw", "kek", "icant", "pog", "pogchamp",
    "poggers", "pogu", "w", "ww", "www", "wtf", "omg", "bro", "noo", "nooo", "noooo",
    "haha", "hahaha", "xd", "monkas", "pepelaugh", "sadge", "insane", "crazy",
    "bhai", "arre", "kya", "op",
}
HYPE_EMOJI = ("😂", "🤣", "💀", "😭", "🔥", "😱", "🤯")

# a moment = (time it happened, reason, strength)
MomentCallback = Callable[[float, str, float], None]


class MomentDetector:
    def __init__(self, on_moment: MomentCallback, has_chat: bool, cooldown: float = 60,
                 chat_multiplier: float = 3.0, audio_jump_db: float = 9.0,
                 audio_jump_db_with_chat: float = 12.0, warmup: float = 90):
        self.on_moment = on_moment
        self.has_chat = has_chat
        self.cooldown = cooldown
        self.chat_multiplier = chat_multiplier
        self.audio_jump = audio_jump_db_with_chat if has_chat else audio_jump_db
        self.warmup = warmup
        self.chat: dict[int, float] = defaultdict(float)
        self.audio: dict[int, float] = {}
        self._audio_checked = 0
        self._first_ts: float | None = None
        self._last_fire = 0.0
        self._lock = threading.Lock()

    # ---- inputs -------------------------------------------------------------
    def add_chat(self, ts: float, text: str) -> None:
        low = text.lower()
        weight = 1.0
        tokens = set(re.findall(r"[a-z]+", low))
        if tokens & HYPE_WORDS or any(e in text for e in HYPE_EMOJI):
            weight += 1.0
        if "clip" in low:
            weight += 2.0  # viewers literally asking for a clip
        with self._lock:
            self._mark(ts)
            self.chat[int(ts)] += weight

    def add_audio(self, start_ts: float, dbs: list[float]) -> None:
        with self._lock:
            self._mark(start_ts)
            for i, d in enumerate(dbs):
                self.audio[int(start_ts) + i] = d

    def _mark(self, ts: float) -> None:
        if self._first_ts is None:
            self._first_ts = ts

    # ---- evaluation ---------------------------------------------------------
    def check(self, now: float) -> None:
        fired = None
        with self._lock:
            if self._first_ts is None or now - self._first_ts < self.warmup:
                self._prune(now)
                return
            if now - self._last_fire >= self.cooldown:
                fired = self._check_chat(now) or self._check_audio(now)
                if fired:
                    self._last_fire = now
            self._prune(now)
        if fired:
            self.on_moment(*fired)

    def _check_chat(self, now: float):
        if not self.has_chat:
            return None
        t = int(now) - 1
        window = [self.chat.get(s, 0.0) for s in range(t - 5, t + 1)]
        base_vals = [self.chat.get(s, 0.0) for s in range(t - 185, t - 5)]
        rate = sum(window) / len(window)
        base = sum(base_vals) / len(base_vals)
        if sum(window) >= 8 and rate >= max(1.5, self.chat_multiplier * base):
            strength = rate / max(base, 0.2)
            # chat reacts a few seconds after the moment happened
            return (now - 5, "chat exploded", strength)
        return None

    def _check_audio(self, now: float):
        seconds = sorted(s for s in self.audio if s > self._audio_checked)
        hit = None
        for s in seconds:
            self._audio_checked = s
            if hit:
                continue
            recent = [self.audio[x] for x in range(s - 2, s + 1) if x in self.audio]
            history = [self.audio[x] for x in range(s - 122, s - 2) if x in self.audio]
            history = [h for h in history if h > -70]  # ignore silence
            if len(recent) < 3 or len(history) < 30:
                continue
            base = statistics.median(history)
            level = sum(recent) / len(recent)
            if level - base >= self.audio_jump and level > -35:
                hit = (float(s), "loud reaction", level - base)
        return hit

    def _prune(self, now: float) -> None:
        old = int(now) - 600
        for d in (self.chat, self.audio):
            for k in [k for k in d if k < old]:
                del d[k]
