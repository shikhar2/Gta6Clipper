"""One live stream being recorded and watched for good moments."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

from .config import Settings
from .detector import MomentDetector
from .editor import ClipJob, snapshot_segments
from .recorder import Recorder
from .twitch import TwitchChat

log = logging.getLogger(__name__)


def safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "stream"


class StreamSession:
    def __init__(self, settings: Settings, platform: str, channel: str, url: str, title: str,
                 credit: str, on_job: Callable[[ClipJob], None]):
        self.s = settings
        self.platform = platform
        self.channel = channel
        self.url = url
        self.title = title
        self.credit = credit
        self.on_job = on_job
        self.key = f"{platform}:{channel.lower()}"
        self.started = time.time()
        self.moments = 0
        self.ended = False

        self.dir = settings.data_dir / "sessions" / f"{platform}_{safe_name(channel)}_{int(self.started)}"
        self.recorder = Recorder(url, self.dir / "segments", platform,
                                 settings.segment_seconds, settings.buffer_seconds)
        self.detector = MomentDetector(
            self._on_moment, has_chat=(platform == "twitch"), cooldown=settings.cooldown,
            chat_multiplier=settings.chat_multiplier, audio_jump_db=settings.audio_jump_db,
            audio_jump_db_with_chat=settings.audio_jump_db_with_chat,
        )
        self.chat = TwitchChat(channel, self.detector.add_chat) if platform == "twitch" else None
        self._pending: list[tuple[float, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"session-{channel}")

    # ---- control ------------------------------------------------------------
    def start(self) -> None:
        log.info("start recording %s (%s)", self.key, self.url)
        self.recorder.start()
        if self.chat:
            self.chat.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def clip_now(self) -> None:
        """Manual clip of the last ~40 seconds (from the /clip command)."""
        self._on_moment(time.time() - self.s.clip_after, "manual clip", 0.0)

    # ---- internals ----------------------------------------------------------
    def _on_moment(self, ts: float, reason: str, strength: float) -> None:
        log.info("moment on %s: %s (%.1f)", self.key, reason, strength)
        with self._lock:
            self._pending.append((ts, reason))

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                for seg in self.recorder.new_segments():
                    self.detector.add_audio(seg.start, seg.loudness)
                self.detector.check(time.time())
                alive = self.recorder.alive()
                self._flush_pending(final=not alive)
                with self._lock:
                    keep = min((t - self.s.clip_before for t, _ in self._pending), default=None)
                self.recorder.cleanup(keep_after=keep)
                if not alive:
                    log.info("stream ended or recorder stopped: %s", self.key)
                    break
                self._stop.wait(1.0)
        except Exception:
            log.exception("session %s crashed", self.key)
        finally:
            self.recorder.stop()
            if self.chat:
                self.chat.stop()
            self.recorder.new_segments()
            self._flush_pending(final=True)
            self.ended = True

    def _flush_pending(self, final: bool) -> None:
        with self._lock:
            pending, self._pending = self._pending, []
        still_waiting = []
        latest = self.recorder.latest_end()
        for ts, reason in pending:
            t0, t1 = ts - self.s.clip_before, ts + self.s.clip_after
            if latest < t1 and not final:
                still_waiting.append((ts, reason))
                continue
            segs = self.recorder.segments_between(t0, t1)
            if not segs:
                continue
            self.moments += 1
            job_dir = self.dir / f"clip_{self.moments:03d}_{int(ts)}"
            job = ClipJob(
                platform=self.platform, channel=self.channel, credit=self.credit,
                reason=reason, t0=max(t0, segs[0].start), t1=min(t1, segs[-1].end),
                segments=snapshot_segments(segs, job_dir), work_dir=job_dir,
                stream_title=self.title,
            )
            self.on_job(job)
        if still_waiting:
            with self._lock:
                self._pending = still_waiting + self._pending
