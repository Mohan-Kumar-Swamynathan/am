# ஆலய மணி — GitHub Actions Setup Guide

## Step 1: Push to GitHub

```bash
git init
git add -A
git commit -m "Initial commit — ஆலய மணி bot v2.0"
git remote add origin https://github.com/YOUR_USERNAME/aalaya-mani.git
git push -u origin main
```

## Step 2: Add Secrets to GitHub

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **Add these 3 secrets**:

## Free LLM Stack ($0 — no credit card)

The bot uses **only free-tier providers**. Add at least **one** of these secrets:

| Secret | Provider | Get key |
|--------|----------|---------|
| `GROQ_API_KEY` | Groq (Llama 3.3) | https://console.groq.com |
| `GEMINI_KEY` | Google Gemini Flash | https://aistudio.google.com/apikey |
| `GH_PAT_TOKEN` | GitHub Models (Llama 3.3) | GitHub PAT with `models:read` |
| `CEREBRAS_API_KEY` | Cerebras (optional) | https://cloud.cerebras.ai |

Recommended: set **GROQ_API_KEY** + **GEMINI_KEY** so the bot can spread load and avoid 429 rate limits.

Optional free extras:
- `PEXELS_API_KEY` — bonus stock photos (Wikimedia + Pollinations work without it)
- `CEREBRAS_API_KEY` — extra LLM fallback

### Secret 1: `GROQ_API_KEY` (recommended — primary)
Get a free Groq API key (14,400 req/day, no credit card):
1. Go to https://console.groq.com → sign in with Google/GitHub
2. **API Keys** → Create API Key
3. Copy key → add as `GROQ_API_KEY` secret

### Secret 2: `GEMINI_KEY` (recommended backup)
```
YOUR_GEMINI_API_KEY_HERE
```
Get free key from: https://aistudio.google.com/apikey

> The bot rotates across Groq, Gemini, GitHub Models, and Cerebras — all free.

### Secret 3: `YOUTUBE_TOKEN_BASE64`
Run this **on your local machine**:

```bash
# Install
pip install google-api-python-client google-auth-oauthlib

# Get client_secrets.json from Google Cloud Console:
#   1. https://console.cloud.google.com/
#   2. Enable YouTube Data API v3
#   3. Create OAuth 2.0 credentials → Desktop app → Download JSON
#   4. Save as client_secrets.json in this folder

# Run the auth helper
python scripts/youtube_auth_setup.py
```

This will:
1. Open your browser for YouTube login
2. Save token locally
3. **Print a base64 string** — copy and paste as `YOUTUBE_TOKEN_BASE64`

### Secret 4: `CLIENT_SECRETS_BASE64`
The same script will also print a base64 version of your `client_secrets.json`.
Copy and paste as `CLIENT_SECRETS_BASE64`.

## Step 3: Add Assets (Optional)

For best results, add these to your repo:
- `image.png` — 1920×1080 background image (Tamil temple/god themed)
- `bgm.mp3` — Background music (will be mixed at 20% volume)

If missing, the workflow generates a placeholder.

## Step 4: Enable Workflows

The daily workflow runs automatically at:
- **00:00 UTC / 05:30 IST** — Generate video
- **01:00 UTC / 06:30 IST** — Upload (weekdays)
- **02:00 UTC / 07:30 IST** — Upload (weekends)
- **13:00 UTC / 18:30 IST** — Evening upload (weekdays)
- **14:00 UTC / 19:30 IST** — Evening upload (weekends)

## Manual Triggers

Go to **Actions** tab → select a workflow → **Run workflow**:

| Option | What it does |
|---|---|
| `today` | Today's deity video |
| `trending` | Trending topic video |
| `all` | All 7 days |
| `full` | Today + trending + upload both |
| `upload` | Upload any pending videos |
| custom `topic` | Your own topic text |

## Schedule for Daily Videos

```
Day        Deity        Upload Times (IST)
─────────────────────────────────────────────
Monday     சிவன்        6:00 AM / 6:30 PM
Tuesday    முருகன்      6:00 AM / 6:30 PM
Wednesday  விநாயகர்     6:00 AM / 6:30 PM
Thursday   பெருமாள்     6:00 AM / 6:30 PM
Friday     லட்சுமி      6:00 AM / 6:30 PM
Saturday   ஐயப்பன்      7:00 AM / 7:30 PM
Sunday     சூரியன்      7:00 AM / 7:30 PM
```

## Festival Auto-Detection

The bot automatically detects:
Pongal (Jan), Shivaratri (Feb), Holi (Mar), Ugadi (Mar/Apr),
Tamil New Year (Apr), Gokulashtami (Jul), Vinayagar Chaturthi (Aug),
Onam (Sep), Navaratri (Oct), Diwali (Oct/Nov), Karthigai (Nov),
Vaikuntha Ekadasi (Dec)

Festival scripts are enhanced automatically with day + festival combined content.

## Cost

**Total: ₹0 / $0**
- GitHub Actions: 2000 free minutes/month (this uses ~20 min/day = 600/mo)
- Gemini API: Free tier (60 requests/min, enough for this)
- YouTube API: Free tier (10,000 units/day, uploads are ~1600 units each)
