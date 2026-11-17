"""Command line:  python -m clipper run | test URL | chatid | selftest"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time

from .config import load_settings


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m clipper", description="GTA 6 live stream clipper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="find streams, clip, send to Telegram (main mode)")
    t = sub.add_parser("test", help="record ONE stream URL and clip it (no Twitch API needed)")
    t.add_argument("url")
    sub.add_parser("chatid", help="find your TELEGRAM_CHAT_ID")
    st = sub.add_parser("selftest", help="check ffmpeg, fonts and captions work (offline)")
    st.add_argument("--no-captions", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S",
    )
    for noisy in ("urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    s = load_settings()
    _check_tools(need_streamlink=args.cmd in ("run", "test"))

    if args.cmd == "chatid":
        from .telegram import print_chat_ids

        if not s.telegram_token:
            sys.exit("Put TELEGRAM_BOT_TOKEN in .env first.")
        print_chat_ids(s.telegram_token)
    elif args.cmd == "run":
        from .app import App

        if not s.telegram_ready:
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (see README).")
        if not s.twitch_ready:
            logging.warning("No Twitch API keys: discovery is off. Only /watch and approved channels work.")
        App(s).run()
    elif args.cmd == "test":
        from .app import App

        App(s, force_approved=True).run_single(args.url)
    elif args.cmd == "selftest":
        selftest(s, captions=not args.no_captions)


def _check_tools(need_streamlink: bool) -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if need_streamlink and not shutil.which("streamlink"):
        missing.append("streamlink")
    if missing:
        sys.exit(f"Missing programs: {', '.join(missing)}. Run ./setup.sh first.")


def selftest(s, captions: bool) -> None:
    """Builds a fake 'stream' with a loud moment and checks a clip comes out."""
    from .detector import MomentDetector
    from .editor import ClipJob, build_clip
    from .recorder import Segment, analyse_audio

    d = s.data_dir / "selftest"
    shutil.rmtree(d, ignore_errors=True)
    seg_dir = d / "segments"
    seg_dir.mkdir(parents=True)
    print("1/4 making a fake 2-minute stream with a loud moment at 1:40 ...")
    audio = "aevalsrc='if(between(t,100,104),0.9,0.04)*sin(2*PI*440*t)':s=44100:d=120"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=s=1920x1080:r=30:d=120", "-f", "lavfi", "-i", audio,
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac", "-shortest",
         "-f", "segment", "-segment_time", str(s.segment_seconds), "-reset_timestamps", "1",
         "-segment_format", "mpegts", str(seg_dir / "seg_%07d.ts")],
        check=True,
    )

    print("2/4 analysing audio and detecting moments ...")
    fired = []
    det = MomentDetector(lambda ts, why, k: fired.append((ts, why, k)), has_chat=False,
                         cooldown=s.cooldown, audio_jump_db=s.audio_jump_db)
    base = time.time() - 1000
    segs, clock = [], base
    for f in sorted(seg_dir.glob("seg_*.ts")):
        dbs, dur = analyse_audio(f)
        seg = Segment(f, clock, clock + dur, dbs)
        segs.append(seg)
        clock += dur
        det.add_audio(seg.start, dbs)
        det.check(seg.end)
    if not fired:
        sys.exit("FAILED: the loud moment was not detected.")
    ts, why, strength = fired[0]
    print(f"    detected '{why}' at {ts - base:.0f}s (+{strength:.0f} dB)  ✓")

    print("3/4 cutting + vertical edit" + (" + captions (first run downloads the model)" if captions else "") + " ...")
    t0, t1 = ts - s.clip_before, ts + s.clip_after
    use = [x for x in segs if x.end > t0 and x.start < t1]
    job = ClipJob("twitch", "selftest", "twitch.tv/selftest", why, t0, t1, use, d / "clip")
    (d / "clip").mkdir()
    out = build_clip(job, s.font_file, captions, s.whisper_model, s.whisper_language, s.max_upload_mb)

    print("4/4 checking the result ...")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "default=nw=1", str(out)],
        capture_output=True, text=True,
    ).stdout
    print("    " + probe.strip().replace("\n", "  "))
    print(f"\n✅ SELFTEST PASSED. Open the clip: {out}")


if __name__ == "__main__":
    main()
