"""Loads settings from .env and the approved-streamer list from streamers.yaml."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")


def _int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default


def _float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _list(name: str, default: list[str]) -> list[str]:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Settings:
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    game_name: str = "Grand Theft Auto VI"
    discovery: bool = True
    min_viewers: int = 20
    max_viewers: int = 500
    languages: list[str] = field(default_factory=lambda: ["en", "hi"])
    max_pages: int = 40
    alerts_per_cycle: int = 5
    poll_seconds: int = 60

    max_sessions: int = 4
    clip_workers: int = 1
    segment_seconds: int = 4
    buffer_seconds: int = 360
    clip_before: int = 30
    clip_after: int = 10
    cooldown: int = 60
    chat_multiplier: float = 3.0
    audio_jump_db: float = 9.0
    audio_jump_db_with_chat: float = 12.0

    captions: bool = True
    whisper_model: str = "small"
    whisper_language: str = ""
    font_file: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    max_upload_mb: int = 49

    data_dir: Path = ROOT / "data"
    approved_twitch: list[str] = field(default_factory=list)
    approved_youtube: list[str] = field(default_factory=list)

    @property
    def twitch_ready(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env")
    s = Settings(
        twitch_client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
        twitch_client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        game_name=os.getenv("GAME_NAME", "Grand Theft Auto VI").strip(),
        discovery=_bool("DISCOVERY", True),
        min_viewers=_int("MIN_VIEWERS", 20),
        max_viewers=_int("MAX_VIEWERS", 500),
        languages=_list("LANGUAGES", ["en", "hi"]),
        max_pages=_int("MAX_PAGES", 40),
        alerts_per_cycle=_int("ALERTS_PER_CYCLE", 5),
        poll_seconds=_int("POLL_SECONDS", 60),
        max_sessions=_int("MAX_SESSIONS", 4),
        clip_workers=_int("CLIP_WORKERS", 1),
        segment_seconds=_int("SEGMENT_SECONDS", 4),
        buffer_seconds=_int("BUFFER_SECONDS", 360),
        clip_before=_int("CLIP_BEFORE", 30),
        clip_after=_int("CLIP_AFTER", 10),
        cooldown=_int("COOLDOWN", 60),
        chat_multiplier=_float("CHAT_MULTIPLIER", 3.0),
        audio_jump_db=_float("AUDIO_JUMP_DB", 9.0),
        audio_jump_db_with_chat=_float("AUDIO_JUMP_DB_WITH_CHAT", 12.0),
        captions=_bool("CAPTIONS", True),
        whisper_model=os.getenv("WHISPER_MODEL", "small").strip(),
        whisper_language=os.getenv("WHISPER_LANGUAGE", "").strip(),
        font_file=os.getenv("FONT_FILE", Settings.font_file).strip(),
        max_upload_mb=_int("MAX_UPLOAD_MB", 49),
        data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve(),
    )

    for p in (str(s.data_dir), s.font_file):
        if not _SAFE_PATH.match(p):
            raise SystemExit(
                f"Path '{p}' has spaces or special characters. "
                "Use a simple path (letters, numbers, - _ . /) for DATA_DIR and FONT_FILE."
            )

    streamers_file = ROOT / "streamers.yaml"
    if streamers_file.exists():
        data = yaml.safe_load(streamers_file.read_text()) or {}
        for item in data.get("twitch") or []:
            name = item["name"] if isinstance(item, dict) else str(item)
            s.approved_twitch.append(name.strip().lower().lstrip("@"))
        for item in data.get("youtube") or []:
            handle = item["handle"] if isinstance(item, dict) else str(item)
            handle = handle.strip()
            if not handle.startswith("@"):
                handle = "@" + handle
            s.approved_youtube.append(handle.lower())

    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
