"""Checks whether a YouTube channel is live right now (free, no API key)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def live_now(handle: str) -> dict | None:
    """Returns {'url', 'title'} if the channel (e.g. '@name') is live, else None."""
    import yt_dlp

    url = f"https://www.youtube.com/{handle}/live"
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None  # not live (yt-dlp raises when there is no live video)
    if not info or not info.get("is_live"):
        return None
    return {"url": info.get("webpage_url") or url, "title": info.get("title") or ""}
