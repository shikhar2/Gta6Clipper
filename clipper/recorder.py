"""Records a live stream into short .ts segments and keeps only the last few minutes."""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

AUDIO_RATE = 8000


@dataclass
class Segment:
    path: Path
    start: float  # wall-clock time (time.time()) of first frame
    end: float  # wall-clock time of last frame
    loudness: list[float]  # dB per second


def analyse_audio(path: Path) -> tuple[list[float], float]:
    """Returns (loudness in dB for each second, duration in seconds)."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn",
           "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-"]
    try:
        pcm = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        pcm = b""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return [], probe_duration(path)
    duration = samples.size / AUDIO_RATE
    dbs = []
    for i in range(0, samples.size, AUDIO_RATE):
        chunk = samples[i : i + AUDIO_RATE]
        if chunk.size < AUDIO_RATE // 4:
            break
        rms = float(np.sqrt(np.mean(chunk * chunk))) / 32768.0
        dbs.append(20 * np.log10(rms + 1e-6))
    return dbs, duration


def probe_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=nw=1:nk=1", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


class Recorder:
    def __init__(self, url: str, out_dir: Path, platform: str, segment_seconds: int = 4,
                 keep_seconds: int = 360):
        self.url = url
        self.out_dir = out_dir
        self.platform = platform
        self.segment_seconds = segment_seconds
        self.keep_seconds = keep_seconds
        self._sl: subprocess.Popen | None = None
        self._ff: subprocess.Popen | None = None
        self._known: dict[Path, Segment] = {}
        self._log = None

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        sl_cmd = ["streamlink", "--stdout", "--hls-live-edge", "2", "--retry-open", "3"]
        if self.platform == "twitch":
            sl_cmd += ["--twitch-disable-ads", "--twitch-low-latency"]
        sl_cmd += [self.url, "1080p60,1080p,best"]
        ff_cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
                  "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                  "-f", "segment", "-segment_time", str(self.segment_seconds),
                  "-reset_timestamps", "1", "-segment_format", "mpegts",
                  str(self.out_dir / "seg_%07d.ts")]
        self._log = open(self.out_dir.parent / "recorder.log", "ab")
        self._sl = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, stderr=self._log)
        self._ff = subprocess.Popen(ff_cmd, stdin=self._sl.stdout, stderr=self._log)
        self._sl.stdout.close()  # let ffmpeg own the pipe

    def alive(self) -> bool:
        return self._ff is not None and self._ff.poll() is None

    def stop(self) -> None:
        for p in (self._sl, self._ff):
            if p and p.poll() is None:
                p.terminate()
        for p in (self._sl, self._ff):
            if p:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
        if self._log:
            self._log.close()
            self._log = None

    def new_segments(self) -> list[Segment]:
        """Analyses segments that finished since the last call and returns them."""
        files = sorted(self.out_dir.glob("seg_*.ts"))
        if self.alive() and files:
            files = files[:-1]  # newest file is still being written
        fresh = []
        for f in files:
            if f in self._known:
                continue
            end = f.stat().st_mtime
            dbs, duration = analyse_audio(f)
            seg = Segment(f, end - duration, end, dbs)
            self._known[f] = seg
            fresh.append(seg)
        return fresh

    def segments_between(self, t0: float, t1: float) -> list[Segment]:
        segs = sorted(self._known.values(), key=lambda s: s.start)
        return [s for s in segs if s.end > t0 and s.start < t1 and s.path.exists()]

    def latest_end(self) -> float:
        return max((s.end for s in self._known.values()), default=0.0)

    def cleanup(self, keep_after: float | None = None) -> None:
        cutoff = time.time() - self.keep_seconds
        if keep_after is not None:
            cutoff = min(cutoff, keep_after)
        for f, seg in list(self._known.items()):
            if seg.end < cutoff:
                f.unlink(missing_ok=True)
                del self._known[f]
