"""The controller: finds streams, runs sessions, makes clips, talks to you on Telegram."""
from __future__ import annotations

import html
import logging
import queue
import shutil
import threading
import time
from pathlib import Path

from . import youtube
from .config import Settings
from .editor import ClipJob, build_clip
from .session import StreamSession
from .state import State
from .telegram import Buttons, Telegram
from .titles import make_post, telegram_caption
from .twitch import TwitchAPI

log = logging.getLogger(__name__)

HELP = """<b>GTA 6 Clipper commands</b>
/status – what is being recorded
/watch name – start clipping a Twitch channel
/watch @handle – start clipping a YouTube channel
/clip name – make a clip of the last 40 seconds now
/approve name – they said YES (sends held clips)
/stop name – stop recording
/ignore name – never alert me about them again
/help – this message"""


def parse_channel(arg: str) -> tuple[str, str]:
    """'name' → twitch, '@handle' or 'yt:@handle' → youtube."""
    arg = arg.strip()
    if arg.lower().startswith("yt:"):
        arg = arg[3:]
        if not arg.startswith("@"):
            arg = "@" + arg
    if arg.startswith("@"):
        return "youtube", arg.lower()
    return "twitch", arg.lower()


def credit_for(platform: str, channel: str) -> str:
    return f"twitch.tv/{channel}" if platform == "twitch" else f"youtube.com/{channel}"


def permission_text(name: str) -> str:
    return (f"Hey {name}! Love your GTA 6 stream 🔥 Can I clip your best moments for "
            f"YouTube Shorts / Instagram Reels? Every clip credits you with your channel "
            f"link on screen and in the description. Is that OK?")


class App:
    def __init__(self, settings: Settings, force_approved: bool = False):
        self.s = settings
        self.force_approved = force_approved
        self.state = State(settings.data_dir / "state.db")
        for c in settings.approved_twitch:
            self.state.approve("twitch", c)
        for h in settings.approved_youtube:
            self.state.approve("youtube", h)
        self.tg = Telegram(settings.telegram_token, settings.telegram_chat_id) \
            if settings.telegram_ready else None
        self.twitch = TwitchAPI(settings.twitch_client_id, settings.twitch_client_secret) \
            if settings.twitch_ready else None
        self.sessions: dict[str, StreamSession] = {}
        self.jobs: queue.Queue[ClipJob] = queue.Queue()
        self.alerted: set[str] = set()
        self.offers: dict[str, dict] = {}
        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self._game_id: str | None = None
        self._yt_next = 0.0

    # ---- messaging helpers --------------------------------------------------
    def notify(self, text: str, buttons: Buttons | None = None) -> None:
        log.info("notify: %s", text.replace("\n", " | ")[:200])
        if self.tg:
            self.tg.send(text, buttons)

    def approved(self, platform: str, channel: str) -> bool:
        return self.force_approved or self.state.is_approved(platform, channel)

    # ---- main loop ----------------------------------------------------------
    def run(self) -> None:
        for _ in range(max(1, self.s.clip_workers)):
            threading.Thread(target=self._worker, daemon=True, name="clip-worker").start()
        if self.tg:
            threading.Thread(target=self.tg.poll_forever, daemon=True, name="telegram",
                             args=(self.on_command, self.on_button, self.stop_event)).start()
        mode = f"watching Twitch category '{self.s.game_name}'" if self.twitch else \
            "Twitch API not set up (only /watch and approved YouTube channels work)"
        self.notify(f"🤖 <b>GTA 6 Clipper is running</b>\n{html.escape(mode)}\n\n{HELP}")
        try:
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("tick failed")
                self.stop_event.wait(self.s.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def run_single(self, url: str) -> None:
        """Test mode: record one URL, clip moments, until the stream ends or Ctrl+C."""
        platform = "twitch" if "twitch.tv" in url else "youtube"
        channel = url.rstrip("/").split("/")[-1].split("?")[0] or "test"
        if platform == "youtube" and not channel.startswith("@"):
            channel = "youtube_test"
        threading.Thread(target=self._worker, daemon=True, name="clip-worker").start()
        if self.tg:
            threading.Thread(target=self.tg.poll_forever, daemon=True, name="telegram",
                             args=(self.on_command, self.on_button, self.stop_event)).start()
        self.start_session(platform, channel.lower(), url, "test")
        try:
            while any(not s.ended for s in self.sessions.values()):
                time.sleep(2)
            self.jobs.join()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.stop_event.set()
        with self._lock:
            for sess in self.sessions.values():
                sess.stop()

    def tick(self) -> None:
        self._reap()
        if self.twitch:
            self._check_approved_twitch()
            if self.s.discovery:
                self._discover()
        if time.time() >= self._yt_next:
            self._yt_next = time.time() + 120
            self._check_approved_youtube()

    # ---- finding streams ----------------------------------------------------
    def _game_matches(self, stream: dict) -> bool:
        return not self.s.game_name or stream.get("game_name", "").lower() == self.s.game_name.lower()

    def _check_approved_twitch(self) -> None:
        logins = self.state.approved_channels("twitch")
        if not logins:
            return
        for st in self.twitch.streams_by_login(logins):
            login = st["user_login"].lower()
            if self._game_matches(st) and f"twitch:{login}" not in self.sessions:
                self.start_session("twitch", login, f"https://twitch.tv/{login}", st.get("title", ""))

    def _check_approved_youtube(self) -> None:
        for handle in self.state.approved_channels("youtube"):
            if f"youtube:{handle}" in self.sessions:
                continue
            live = youtube.live_now(handle)
            if live:
                self.start_session("youtube", handle, live["url"], live["title"])

    def _discover(self) -> None:
        if not self._game_id:
            self._game_id = self.twitch.game_id(self.s.game_name)
            if not self._game_id:
                log.warning("Twitch category '%s' not found yet", self.s.game_name)
                return
        sent = 0
        for st in self.twitch.live_streams(self._game_id, self.s.languages, self.s.max_pages):
            viewers = int(st.get("viewer_count", 0))
            if viewers < self.s.min_viewers:
                break  # results are sorted by viewers, the rest are smaller
            login = st["user_login"].lower()
            if (viewers > self.s.max_viewers or st["id"] in self.alerted
                    or f"twitch:{login}" in self.sessions
                    or self.state.is_ignored("twitch", login)
                    or self.state.is_approved("twitch", login)):
                continue
            self.alerted.add(st["id"])
            self._alert(st)
            sent += 1
            if sent >= self.s.alerts_per_cycle:
                break

    def _alert(self, st: dict) -> None:
        login = st["user_login"].lower()
        name = st.get("user_name") or login
        self.offers[f"twitch:{login}"] = {"url": f"https://twitch.tv/{login}", "title": st.get("title", "")}
        thumb = (st.get("thumbnail_url") or "").replace("{width}", "640").replace("{height}", "360")
        caption = (
            f"🔴 <b>{html.escape(name)}</b> is live – {st.get('viewer_count')} viewers "
            f"({html.escape(st.get('language', ''))})\n{html.escape(st.get('title', ''))[:300]}\n"
            f"https://twitch.tv/{login}\n\n"
            f"Ask permission (tap to copy, paste in their chat):\n"
            f"<code>{html.escape(permission_text(name))}</code>"
        )
        buttons = [
            [("🎬 Start clipping (held until YES)", f"clip:twitch:{login}")],
            [("✅ They said YES – clip", f"yes:twitch:{login}"), ("🙈 Ignore", f"ignore:twitch:{login}")],
        ]
        if self.tg and thumb:
            self.tg.send_photo_url(f"{thumb}?t={int(time.time())}", caption, buttons)
        else:
            self.notify(caption, buttons)

    # ---- sessions -----------------------------------------------------------
    def start_session(self, platform: str, channel: str, url: str, title: str) -> None:
        key = f"{platform}:{channel}"
        with self._lock:
            if key in self.sessions:
                self.notify(f"Already recording <b>{html.escape(channel)}</b>.")
                return
            if len(self.sessions) >= self.s.max_sessions:
                self.notify(f"⚠️ Already recording {len(self.sessions)} streams (MAX_SESSIONS). "
                            f"/stop one first to add <b>{html.escape(channel)}</b>.")
                return
            sess = StreamSession(self.s, platform, channel, url, title,
                                 credit_for(platform, channel), self.jobs.put)
            self.sessions[key] = sess
            sess.start()
        held = "" if self.approved(platform, channel) else \
            "\n⏳ They have not said YES yet, so clips will be HELD (not sent) until you /approve."
        self.notify(f"🎬 Recording <b>{html.escape(channel)}</b> ({platform}). "
                    f"Good moments will be clipped automatically.{held}",
                    [[("📎 Clip now", f"clipnow:{platform}:{channel}"),
                      ("🛑 Stop", f"stop:{platform}:{channel}")]])

    def _reap(self) -> None:
        with self._lock:
            done = [k for k, s in self.sessions.items() if s.ended]
            for k in done:
                sess = self.sessions.pop(k)
                shutil.rmtree(sess.dir / "segments", ignore_errors=True)
                self.notify(f"⚫ <b>{html.escape(sess.channel)}</b> stopped/offline. "
                            f"{sess.moments} moment(s) clipped.")

    # ---- clip worker --------------------------------------------------------
    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self._process(job)
            except Exception as e:
                log.exception("clip failed")
                self.notify(f"❌ Clip from {html.escape(job.channel)} failed: {html.escape(str(e))[:300]}")
            finally:
                self.jobs.task_done()

    def _process(self, job: ClipJob) -> None:
        started = time.time()
        path = build_clip(job, self.s.font_file, self.s.captions, self.s.whisper_model,
                          self.s.whisper_language, self.s.max_upload_mb)
        for seg in job.segments:  # free disk space, keep only clip.mp4
            seg.path.unlink(missing_ok=True)
        (job.work_dir / "raw.ts").unlink(missing_ok=True)
        title, desc = make_post(job.platform, job.channel, job.credit, job.transcript)
        log.info("clip ready %s in %.0fs", path, time.time() - started)

        if self.approved(job.platform, job.channel):
            caption = telegram_caption(title, desc, f"✅ <b>READY TO POST</b> – "
                                       f"{html.escape(job.channel)} ({html.escape(job.reason)})")
            self._deliver(path, caption)
            self.state.add_clip(job.platform, job.channel, str(path), caption, "sent")
            return

        caption = telegram_caption(title, desc, f"✅ <b>APPROVED – READY TO POST</b> – "
                                   f"{html.escape(job.channel)}")
        self.state.add_clip(job.platform, job.channel, str(path), caption, "held")
        n = len(self.state.held_clips(job.platform, job.channel))
        if n == 1 or n % 5 == 0:
            p, c = job.platform, job.channel
            self.notify(
                f"⏳ <b>{n} clip(s) from {html.escape(c)} are ready but HELD.</b>\n"
                f"They have not said YES yet – do not post. Ask them:\n"
                f"<code>{html.escape(permission_text(c))}</code>",
                [[("✅ They said YES – send clips", f"approve:{p}:{c}")],
                 [("🛑 Stop", f"stop:{p}:{c}"), ("🗑 Ignore + stop", f"ignore:{p}:{c}")]],
            )

    def _deliver(self, path: Path, caption: str) -> None:
        if self.tg:
            if not self.tg.send_video(path, caption):
                self.notify(f"Clip saved on server but upload to Telegram failed: {path}")
        else:
            log.info("clip saved: %s", path)

    def release_held(self, platform: str, channel: str) -> int:
        held = self.state.held_clips(platform, channel)
        for clip_id, path, caption in held:
            if Path(path).exists():
                self._deliver(Path(path), caption)
            self.state.set_status(clip_id, "sent")
        return len(held)

    # ---- Telegram input -----------------------------------------------------
    def on_button(self, data: str, _callback_id: str) -> None:
        action, _, rest = data.partition(":")
        platform, _, channel = rest.partition(":")
        if not channel:
            return
        key = f"{platform}:{channel}"
        if action in ("clip", "yes"):
            if action == "yes":
                self.state.approve(platform, channel)
            offer = self.offers.get(key, {})
            url = offer.get("url") or (f"https://twitch.tv/{channel}" if platform == "twitch" else "")
            if url:
                self.start_session(platform, channel, url, offer.get("title", ""))
        elif action == "approve":
            self.state.approve(platform, channel)
            n = self.release_held(platform, channel)
            self.notify(f"✅ <b>{html.escape(channel)}</b> approved. Sent {n} held clip(s). "
                        f"New clips will come straight to you.")
        elif action == "stop":
            self._stop(key)
        elif action == "ignore":
            self.state.ignore(platform, channel)
            self._stop(key, quiet=True)
            self.notify(f"🙈 Ignoring <b>{html.escape(channel)}</b>.")
        elif action == "clipnow":
            sess = self.sessions.get(key)
            if sess:
                sess.clip_now()
                self.notify(f"📎 Clipping the last 40s of <b>{html.escape(channel)}</b>…")

    def _stop(self, key: str, quiet: bool = False) -> None:
        sess = self.sessions.get(key)
        if sess:
            sess.stop()
            if not quiet:
                self.notify(f"🛑 Stopping <b>{html.escape(sess.channel)}</b>…")
        elif not quiet:
            self.notify("Not recording that channel. Use /status to see names.")

    def on_command(self, cmd: str, args: list[str]) -> None:
        if cmd in ("/start", "/help"):
            self.notify(HELP)
            return
        if cmd == "/status":
            if not self.sessions:
                self.notify("Nothing recording right now. Waiting for streams…")
                return
            lines = []
            for sess in list(self.sessions.values()):
                mins = int((time.time() - sess.started) / 60)
                ok = "✅ approved" if self.approved(sess.platform, sess.channel) else "⏳ not approved"
                lines.append(f"• <b>{html.escape(sess.channel)}</b> ({sess.platform}) – {mins} min, "
                             f"{sess.moments} moments, {ok}")
            self.notify("🎥 <b>Recording</b>\n" + "\n".join(lines))
            return
        if not args:
            self.notify(f"Add a channel name, e.g. <code>{cmd} streamername</code>")
            return
        platform, channel = parse_channel(args[0])
        key = f"{platform}:{channel}"
        if cmd == "/watch":
            threading.Thread(target=self._watch, args=(platform, channel), daemon=True).start()
        elif cmd == "/stop":
            self._stop(key)
        elif cmd == "/clip":
            self.on_button(f"clipnow:{platform}:{channel}", "")
        elif cmd == "/approve":
            self.on_button(f"approve:{platform}:{channel}", "")
        elif cmd == "/ignore":
            self.on_button(f"ignore:{platform}:{channel}", "")
        else:
            self.notify("Unknown command.\n\n" + HELP)

    def _watch(self, platform: str, channel: str) -> None:
        if platform == "youtube":
            live = youtube.live_now(channel)
            if not live:
                self.notify(f"{html.escape(channel)} is not live on YouTube right now.")
                return
            self.start_session(platform, channel, live["url"], live["title"])
            return
        title = ""
        if self.twitch:
            live = self.twitch.streams_by_login([channel])
            if not live:
                self.notify(f"{html.escape(channel)} is not live on Twitch right now.")
                return
            title = live[0].get("title", "")
        self.start_session(platform, channel, f"https://twitch.tv/{channel}", title)
