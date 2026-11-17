"""Speech-to-text (faster-whisper) and big bold word-by-word subtitles (.ass)."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


@dataclass
class Word:
    start: float
    end: float
    text: str


def transcribe(wav: Path, model_size: str, language: str = "") -> tuple[list[Word], str]:
    """Returns (words with timings, detected language)."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            log.info("loading whisper model '%s' (first time downloads it)", model_size)
            _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = _model.transcribe(
            str(wav), word_timestamps=True, vad_filter=True,
            language=language or None, beam_size=1,
        )
        words = [Word(w.start, w.end, w.word.strip())
                 for seg in segments for w in (seg.words or []) if w.word.strip()]
    return words, info.language


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(words: list[Word], out: Path, max_words: int = 3, max_gap: float = 0.6) -> bool:
    """Writes subtitles in short chunks (2-3 words). Returns False if there is nothing to write."""
    chunks: list[list[Word]] = []
    for w in words:
        if chunks and len(chunks[-1]) < max_words and w.start - chunks[-1][-1].end < max_gap:
            chunks[-1].append(w)
        else:
            chunks.append([w])
    if not chunks:
        return False

    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,DejaVu Sans,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,6,3,2,70,70,470,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for i, chunk in enumerate(chunks):
        start = chunk[0].start
        end = chunks[i + 1][0].start if i + 1 < len(chunks) else chunk[-1].end + 0.4
        end = min(end, chunk[-1].end + 0.8)
        text = _clean(" ".join(w.text for w in chunk)).upper()
        colour = r"{\c&H00FFFF&}" if i % 2 else ""  # every other chunk yellow
        pop = r"{\fscx85\fscy85\t(0,80,\fscx100\fscy100)}"  # small pop-in
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{pop}{colour}{text}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
