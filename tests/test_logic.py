"""Run with:  python -m pytest -q"""
from clipper.app import parse_channel
from clipper.captions import Word, write_ass
from clipper.detector import MomentDetector
from clipper.titles import make_post


def _run_chat(rates):
    fired = []
    det = MomentDetector(lambda *a: fired.append(a), has_chat=True, cooldown=60)
    t0 = 1_000_000
    for sec, n in enumerate(rates):
        for _ in range(n):
            det.add_chat(t0 + sec + 0.5, "hello")
        det.check(t0 + sec + 1.0)
    return fired, t0


def test_chat_spike_fires_once():
    rates = [1] * 200 + [8] * 10 + [1] * 30 + [9] * 5  # second spike is inside cooldown
    fired, t0 = _run_chat(rates)
    assert len(fired) == 1
    ts, reason, strength = fired[0]
    assert reason == "chat exploded"
    assert 195 <= ts - t0 <= 210
    assert strength > 3


def test_busy_chat_without_spike_does_not_fire():
    fired, _ = _run_chat([6] * 300)
    assert fired == []


def test_hype_words_count_more():
    det = MomentDetector(lambda *a: None, has_chat=True)
    det.add_chat(10.0, "hello")
    det.add_chat(11.0, "KEKW 💀")
    det.add_chat(12.0, "clip it")
    assert det.chat[10] == 1 and det.chat[11] == 2 and det.chat[12] == 3


def test_audio_spike_without_chat():
    fired = []
    det = MomentDetector(lambda *a: fired.append(a), has_chat=False, cooldown=60)
    levels = [-30.0] * 150 + [-12.0] * 5 + [-30.0] * 20
    det.add_audio(5000.0, levels)
    det.check(5000.0 + len(levels))
    assert len(fired) == 1 and 5148 <= fired[0][0] <= 5152


def test_parse_channel():
    assert parse_channel("SomeStreamer") == ("twitch", "somestreamer")
    assert parse_channel("@CoolYT") == ("youtube", "@coolyt")
    assert parse_channel("yt:CoolYT") == ("youtube", "@coolyt")


def test_title_uses_shouted_quote_and_credit():
    title, desc = make_post("twitch", "abc", "twitch.tv/abc", "No way bro! we escaped the cops.")
    assert title.startswith('"NO WAY BRO"') and len(title) <= 100
    assert "twitch.tv/abc" in desc and "#GTA6" in desc


def test_write_ass(tmp_path):
    words = [Word(0.0, 0.3, "no"), Word(0.3, 0.6, "way"), Word(0.6, 0.9, "bro"), Word(3.0, 3.4, "what{x}")]
    out = tmp_path / "s.ass"
    assert write_ass(words, out)
    text = out.read_text()
    assert "NO WAY BRO" in text and "WHAT(X)" in text
    assert not write_ass([], tmp_path / "e.ass")
