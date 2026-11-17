"""Ready-to-paste titles, captions and hashtags for each clip."""
from __future__ import annotations

import html
import random
import re

HOOKS = [
    "Nobody was ready for this 💀",
    "GTA 6 went wrong 💀",
    "Wait for it... 😂",
    "Chat lost it 😂",
    "Vice City is WILD 🤯",
    "This GTA 6 moment is insane 😳",
    "Day one in GTA 6 be like 💀",
    "GTA 6 physics are crazy 🤯",
    "This is why GTA 6 took 12 years 😭",
    "I can't believe this happened in GTA 6 😭",
]

HASHTAGS = "#GTA6 #GTAVI #GTA6Gameplay #GTA6Funny #ViceCity #Gaming #Shorts"


def _quote(transcript: str) -> str | None:
    """Uses a short shouted line from the clip as the title when there is a good one."""
    for part in re.split(r"[.!?]+", transcript):
        words = part.strip().split()
        if 2 <= len(words) <= 6 and re.search(r"\b(no|what|bro|why|wait|bhai|arre|kya)\b", part, re.I):
            return " ".join(words).upper()
    return None


def make_post(platform: str, channel: str, credit: str, transcript: str) -> tuple[str, str]:
    """Returns (title, description)."""
    quote = _quote(transcript)
    hook = f'"{quote}" 💀 | GTA 6' if quote else random.choice(HOOKS)
    title = f"{hook} #shorts"[:100]
    where = "Twitch" if platform == "twitch" else "YouTube"
    description = (
        f"{hook}\n\n"
        f"🎮 Streamer: {channel} — {credit} ({where})\n"
        f"Go follow them for more GTA 6!\n\n"
        f"{HASHTAGS}"
    )
    return title, description


def telegram_caption(title: str, description: str, header: str) -> str:
    """HTML caption for Telegram (tap a code block to copy it)."""
    cap = (
        f"{header}\n\n"
        f"<b>Title</b>\n<code>{html.escape(title)}</code>\n\n"
        f"<b>Description / Reel caption</b>\n<code>{html.escape(description)}</code>"
    )
    return cap[:1024]
