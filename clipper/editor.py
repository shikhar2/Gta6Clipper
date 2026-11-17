"""Turns recorded segments into a finished vertical (9:16) clip."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import captions
from .recorder import Segment, probe_duration

log = logging.getLogger(__name__)


@dataclass
class ClipJob:
    platform: str
    channel: str
    credit: str  # e.g. "twitch.tv/name"
    reason: str
    t0: float
    t1: float
    segments: list[Segment]
    work_dir: Path
    stream_title: str = ""
    transcript: str = field(default="")


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-800:]}")


def snapshot_segments(segments: list[Segment], work_dir: Path) -> list[Segment]:
    """Copies segments into the job folder so the rolling buffer can delete the originals."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for s in segments:
        dst = work_dir / s.path.name
        try:
            os.link(s.path, dst)
        except OSError:
            shutil.copy2(s.path, dst)
        out.append(Segment(dst, s.start, s.end, s.loudness))
    return out


def build_clip(job: ClipJob, font_file: str, use_captions: bool, whisper_model: str,
               whisper_language: str, max_mb: int) -> Path:
    d = job.work_dir
    segs = sorted(job.segments, key=lambda s: s.start)
    if not segs:
        raise RuntimeError("no video segments for this moment")

    # 1. join segments (fast, no re-encode)
    listing = d / "list.txt"
    listing.write_text("".join(f"file '{s.path.resolve()}'\n" for s in segs))
    raw = d / "raw.ts"
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(listing), "-c", "copy", str(raw)])

    total = probe_duration(raw) or (segs[-1].end - segs[0].start)
    offset = max(0.0, job.t0 - segs[0].start)
    duration = min(job.t1 - job.t0, total - offset)
    if duration < 5:
        offset, duration = 0.0, total

    # 2. subtitles
    ass = d / "subs.ass"
    has_subs = False
    if use_captions:
        wav = d / "audio.wav"
        try:
            _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{offset:.2f}",
                  "-t", f"{duration:.2f}", "-i", str(raw), "-vn", "-ac", "1", "-ar", "16000",
                  str(wav)])
            words, _lang = captions.transcribe(wav, whisper_model, whisper_language)
            job.transcript = " ".join(w.text for w in words)
            has_subs = captions.write_ass(words, ass)
        except Exception as e:
            log.warning("captions skipped: %s", e)

    # 3. vertical edit: blurred background + sharp gameplay + credit + subtitles
    credit_file = d / "credit.txt"
    credit_file.write_text(job.credit)
    graph = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=20:2,eq=brightness=-0.10[bg];"
        "[0:v]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v1];"
        f"[v1]drawtext=fontfile='{font_file}':textfile='{credit_file}':fontsize=50:"
        "fontcolor=white:borderw=5:bordercolor=black:x=(w-text_w)/2:y=500"
    )
    graph += f",subtitles=filename='{ass}'[v]" if has_subs else "[v]"
    final = d / "clip.mp4"
    _encode(raw, offset, duration, graph, final, ["-crf", "23", "-maxrate", "6M", "-bufsize", "12M"])

    # 4. Telegram bots can only send files up to 50 MB
    limit = max_mb * 1024 * 1024
    if final.stat().st_size > limit:
        kbps = int(limit * 8 * 0.9 / duration / 1000) - 160
        smaller = d / "clip_small.mp4"
        _encode(raw, offset, duration, graph, smaller,
                ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"])
        smaller.replace(final)
    return final


def _encode(raw: Path, offset: float, duration: float, graph: str, out: Path, rate: list[str]):
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
          "-ss", f"{offset:.2f}", "-t", f"{duration:.2f}", "-i", str(raw),
          "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
          "-c:v", "libx264", "-preset", "veryfast", *rate, "-pix_fmt", "yuv420p", "-r", "30",
          "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out)])
