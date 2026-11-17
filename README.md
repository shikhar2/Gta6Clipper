# GTA 6 Clipper 🎮✂️

Watches GTA 6 live streams, **finds the funny/crazy moments automatically**, turns them into
**vertical Shorts/Reels with subtitles + streamer credit**, and sends them to your **Telegram**.
You then post them to YouTube Shorts and Instagram Reels from your phone.

```
Streamer goes live on Twitch / YouTube
        │  (checked every 60 s)
        ▼
Telegram alert: "🔴 xyz is live – 85 viewers"  →  [🎬 Clip]  [✅ They said YES]  [🙈 Ignore]
        │
        ▼
Records the stream (keeps last 6 min)  +  reads Twitch chat
        │
        ▼
Moment found  (chat explodes "KEKW 💀 CLIP IT"  or  streamer screams)
        │
        ▼
Cuts 30 s before + 10 s after → 9:16 vertical → big subtitles → "twitch.tv/name" credit
        │
        ├── Streamer said YES  → clip + title + hashtags sent to your Telegram → you post it
        └── Not approved yet   → clip is HELD. You ask them. Tap "✅ They said YES" → clips sent
```

## ⚠️ The one rule

**Only post clips of streamers who said YES.** Posting someone's stream without permission
gets copyright strikes (3 strikes = channel deleted). The tool helps you: clips from
people who haven't approved are **held** and not sent to you until you tap "They said YES".
Screenshot every "yes" as proof.

---

## Setup (about 30 minutes, one time)

### Step 1 – Rent a server (DigitalOcean, about ₹1,500–3,000 for launch week)

1. Make an account at **digitalocean.com**
2. **Create → Droplets**
   - Region: **Bangalore** (closest to India)
   - Image: **Ubuntu 24.04**
   - Size: **Basic → Regular → 4 CPU / 8 GB RAM**
   - Authentication: **Password** (simple) or SSH key
3. Create it and copy the **IP address**
4. Connect to it from your computer's terminal (Windows: PowerShell):
   ```
   ssh root@YOUR_SERVER_IP
   ```
5. When launch week is over, **destroy the droplet** so you stop paying.

### Step 2 – Twitch API keys (free)

1. Log in to Twitch → **Settings → Security and Privacy → turn on Two-Factor Authentication** (required)
2. Open **https://dev.twitch.tv/console** → **Register Your Application**
   - Name: `gta6clipper-yourname` (must be unique)
   - OAuth Redirect URLs: `http://localhost:3000`
   - Category: `Application Integration`
   - Client Type: `Confidential`
3. Click **Manage** → copy the **Client ID** → click **New Secret** → copy the **Client Secret**

### Step 3 – Telegram bot (free)

1. In Telegram, open **@BotFather** → send `/newbot` → choose a name → copy the **token**
2. Open your new bot and press **Start** (send it "hi")

### Step 4 – Install the clipper on the server

```bash
git clone https://github.com/shikhar2/Gta6Clipper.git
cd Gta6Clipper
./setup.sh
```

### Step 5 – Add your keys

```bash
nano .env
```
Fill in `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`.
Save: **Ctrl+O, Enter, Ctrl+X**.

Get your chat id (send "hi" to your bot while this runs):
```bash
.venv/bin/python -m clipper chatid
```
Put the number it prints in `.env` as `TELEGRAM_CHAT_ID`.

### Step 6 – Check everything works

```bash
.venv/bin/python -m clipper selftest
```
Must end with **✅ SELFTEST PASSED** (first run downloads the subtitle model, ~1 minute).

### Step 7 – Test TODAY on GTA 5 (before launch)

In `.env` set `GAME_NAME=Grand Theft Auto V`, then:
```bash
sudo systemctl start gta6clipper
journalctl -u gta6clipper -f        # watch the log (Ctrl+C to exit the log, the tool keeps running)
```
You should get Telegram alerts about GTA 5 streamers within a minute or two.
Tap **🎬 Start clipping** on one and wait for a moment. If it works, change back to
`GAME_NAME=Grand Theft Auto VI` and restart:
```bash
sudo systemctl restart gta6clipper
```

Quick test of a single stream (no Twitch keys needed, clips are saved on the server):
```bash
.venv/bin/python -m clipper test https://twitch.tv/SOMESTREAMER
```

---

## Launch day

- Add streamers who already said yes to **`streamers.yaml`** (or use `/approve name` in Telegram).
  They are clipped **automatically** the moment they go live with GTA 6.
- New small streamers (20–500 viewers) show up as alerts. Tap **🎬 Start clipping**, paste the
  permission message (it's in the alert, tap to copy) in their chat.
- When they say yes → tap **✅ They said YES – send clips**.
- Each finished clip comes with a ready **title + description + hashtags** (tap to copy).
  Save the video → post to **YouTube Shorts** + **Instagram Reels** (add a trending sound on IG).

### Telegram commands

| Command | What it does |
|---|---|
| `/status` | What is recording right now |
| `/watch name` | Start clipping twitch.tv/name |
| `/watch @handle` | Start clipping a YouTube channel |
| `/clip name` | Clip the last 40 seconds right now (manual) |
| `/approve name` | They said YES – sends their held clips |
| `/stop name` | Stop recording them |
| `/ignore name` | Never alert about them again |

### Useful settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `MAX_SESSIONS` | 4 | Streams recorded at once (4 CPU server → 3–4) |
| `MIN_VIEWERS` / `MAX_VIEWERS` | 20 / 500 | Size of streamers to alert about |
| `LANGUAGES` | en,hi | Stream languages |
| `CLIP_BEFORE` / `CLIP_AFTER` | 30 / 10 | Clip length around the moment |
| `CHAT_MULTIPLIER` | 3.0 | Lower = more clips, higher = only big moments |
| `WHISPER_MODEL` | small | `base` is faster, `small` is more accurate |

After changing `.env`: `sudo systemctl restart gta6clipper`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No alerts | GTA 6 category may be empty before launch – test with `GAME_NAME=Grand Theft Auto V`. Check log: `journalctl -u gta6clipper -f` |
| "Twitch login failed" | Wrong Client ID / Secret in `.env` |
| No clips from a stream | Quiet chat = fewer moments. Use `/clip name` for manual clips, or lower `CHAT_MULTIPLIER` to 2.5 |
| YouTube stream won't record | `.venv/bin/pip install -U yt-dlp streamlink` then restart |
| Server slow / clips late | Lower `MAX_SESSIONS`, or `WHISPER_MODEL=base` |
| Disk full | `rm -rf data/sessions/*` (old clips) |

## How it finds moments

- **Twitch chat speed**: counts messages per second. When chat goes **3× faster than normal**
  (people spamming `KEKW`, `💀`, `W`, `CLIP IT`), that's a moment. Words like "clip" count extra.
- **Loudness**: when the streamer's audio jumps much louder than normal (screaming, laughing).
- After each clip there is a 60-second cooldown so you don't get 5 clips of the same thing.

## Run tests (for developers)

```bash
.venv/bin/python -m pytest -q
```
