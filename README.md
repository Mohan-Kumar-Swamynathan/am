# ஆலய மணி — Complete Automation Suite

Fully automated Tamil devotional YouTube content bot. Runs for **free on GitHub Actions**.

## Quick Start

```bash
# 1. Push to GitHub
git init && git add -A && git commit -m "Initial commit"
# 2. Add 3 secrets in GitHub → Settings → Secrets → Actions:
#    GEMINI_KEY, YOUTUBE_TOKEN_BASE64, CLIENT_SECRETS_BASE64
# 3. Push to main — done!
```

Full setup guide → **[SETUP.md](SETUP.md)**

## What It Does

| Time (IST) | Action |
|---|---|
| 5:30 AM | Generates today's deity video + trending bonus |
| 6:30 AM (weekdays) / 7:30 AM (weekends) | Uploads to YouTube |
| 6:30 PM (weekdays) / 7:30 PM (weekends) | Uploads any pending |

- Auto-detects festivals (Pongal, Diwali, Shivaratri, etc.)
- Scrapes Google Trends / YouTube / temple news for trending topics
- Generates Tamil narration → TTS voice → video with BGM → uploads to YouTube
- Sets title, description, tags, and pinned comment automatically

## Commands (local use)

```bash
python aalaya_mani_bot.py --day today          # today's deity
python aalaya_mani_bot.py --day all            # all 7 days
python aalaya_mani_bot.py --trending           # trending topic
python aalaya_mani_bot.py --daemon             # 24/7 scheduler
python aalaya_mani_bot.py --auth-youtube       # YouTube auth
python aalaya_mani_bot.py --topic "..." --upload  # custom + upload
```
