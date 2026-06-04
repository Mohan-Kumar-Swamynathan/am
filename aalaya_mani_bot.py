#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║            ஆலய மணி — FULLY AUTOMATED BOT v2.0               ║
║  Script + Voice + Video + Trending + YouTube Upload          ║
║  Runs 24/7 — automatically posts at optimal times            ║
╚═══════════════════════════════════════════════════════════════╝

Setup:
  pip install google-generativeai edge-tts google-api-python-client \
             google-auth-oauthlib requests beautifulsoup4 schedule

  python aalaya_mani_bot.py --auth-youtube   (first time only)

Usage:
  python aalaya_mani_bot.py --daemon           # 24/7 scheduler
  python aalaya_mani_bot.py --day today        # today's deity video
  python aalaya_mani_bot.py --day all          # all 7 days
  python aalaya_mani_bot.py --trending         # trending topic video
  python aalaya_mani_bot.py --upload           # upload pending videos
"""

import argparse
import base64
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import pickle
import hashlib
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import google.generativeai as genai
except ImportError:
    print("pip install google-generativeai"); sys.exit(1)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("pip install google-api-python-client google-auth-oauthlib"); sys.exit(1)

try:
    import schedule
except ImportError:
    print("pip install schedule"); sys.exit(1)


# =============================================
# CONFIGURATION
# =============================================
GEMINI_KEY = "AQ.Ab8RN6JRCd3n8VmiwSH7tsBYZh6oSxErxG_1Xsaph6rN7wfZ1Q"
BGM_FILE = "bgm.mp3"
IMAGE_FILE = "image.png"
OUTPUT_DIR = "videos"
SHORTS_DIR = "shorts"
METADATA_DIR = "metadata"
SCRIPTS_DIR = "scripts"
QUEUE_FILE = "upload_queue.json"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_TOKEN_FILE = "youtube_token.pickle"
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"

FEMALE_HUMANIZE = (
    "highpass=f=100,lowpass=f=8000,"
    "equalizer=f=220:t=q:w=0.7:g=4,"
    "equalizer=f=500:t=q:w=0.9:g=3,"
    "equalizer=f=1100:t=q:w=0.8:g=2,"
    "equalizer=f=3200:t=q:w=1:g=-5,"
    "equalizer=f=5000:t=q:w=1:g=-9,"
    "equalizer=f=7000:t=q:w=1:g=-12,"
    "vibrato=f=6:d=0.06,"
    "chorus=0.6:0.8:45:0.3:0.2:2,"
    "aecho=0.8:0.35:40|55:0.18|0.12,"
    "acompressor=threshold=-22dB:ratio=3:attack=3:release=40:makeup=3,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)

# Upload schedule: (hour, minute)
WEEKDAY_UPLOAD_TIMES = [(6, 0), (18, 30)]
WEEKEND_UPLOAD_TIMES = [(7, 0), (19, 30)]

DAY_CONFIG = {
    "monday":    {"deity": "சிவன்",     "deity_en": "Shiva",    "topic": "திங்கள்கிழமை சிவன் வழிபாடு 7 பலன்கள்",          "emoji": "🕉",  "hashtags": "#சிவன் #MondayShiva #OmNamashivaya"},
    "tuesday":   {"deity": "முருகன்",   "deity_en": "Murugan",  "topic": "செவ்வாய்கிழமை முருகன் விரதம் 7 பலன்கள்",      "emoji": "🔱",  "hashtags": "#முருகன் #TuesdayMurugan #VelMurugan"},
    "wednesday": {"deity": "விநாயகர்",  "deity_en": "Vinayagar","topic": "புதன்கிழமை விநாயகர் வழிபாடு 7 பலன்கள்",         "emoji": "🐘",  "hashtags": "#விநாயகர் #WednesdayVinayagar #Pillaiyar"},
    "thursday":  {"deity": "பெருமாள்",  "deity_en": "Perumal",  "topic": "வியாழக்கிழமை பெருமாள் வழிபாடு 7 பலன்கள்",       "emoji": "🙏",  "hashtags": "#பெருமாள் #ThursdayPerumal #Govinda"},
    "friday":    {"deity": "லட்சுமி",   "deity_en": "Lakshmi",  "topic": "வெள்ளிக்கிழமை லட்சுமி வழிபாடு 7 பலன்கள்",       "emoji": "🪷",  "hashtags": "#லட்சுமி #FridayLakshmi #MahaLakshmi"},
    "saturday":  {"deity": "ஐயப்பன்",   "deity_en": "Ayyappan", "topic": "சனிக்கிழமை ஐயப்பன் வழிபாடு 7 பலன்கள்",         "emoji": "🎵",  "hashtags": "#ஐயப்பன் #SaturdayAyyappan #SwamiyeSaranam"},
    "sunday":    {"deity": "சூரியன்",   "deity_en": "Surya",    "topic": "ஞாயிற்றுக்கிழமை சூரிய வழிபாடு 7 பலன்கள்",      "emoji": "🌞",  "hashtags": "#சூரியன் #SundaySurya #SuryaBhagavan"},
}

HINDU_FESTIVALS = {
    (1, 13): "பொங்கல் தினம்",
    (1, 14): "பொங்கல் திருநாள்",
    (1, 15): "மாட்டுப் பொங்கல்",
    (1, 16): "காணும் பொங்கல்",
    (2, 21): "சிவராத்திரி",
    (3, 14): "ஹோலி",
    (3, 29): "உகாதி",
    (3, 30): "உகாதி",
    (4, 14): "தமிழ் புத்தாண்டு",
    (7, 16): "கோகுலாஷ்டமி",
    (8, 27): "விநாயகர் சதுர்த்தி",
    (9, 7): "ஓணம்",
    (10, 2): "சரஸ்வதி பூஜை",
    (10, 12): "தசரா",
    (10, 20): "தீபாவளி",
    (11, 1): "தீபாவளி",
    (11, 15): "கார்த்திகை தீபம்",
    (12, 25): "வைகுண்ட ஏகாதசி",
}

SCRIPT_PROMPT = """You are a Tamil devotional content writer for YouTube channel "ஆலய மணி".

Write a Tamil devotional YouTube narration script about: {topic}

STRICT RULES:
- Write ONLY in Tamil script (NO English words except mantra names)
- Exactly 2000 Tamil characters
- Start with: வணக்கம். ஆலய மணி சேனலுக்கு வரவேற்கிறோம்.
- Explain why this day is special for this deity
- List 7 detailed benefits (பலன் நம்பர் ஒன்று, இரண்டு, etc.)
- Each benefit should cover: what happens + why + how it helps
- Include specific pariharam (remedy) section at the end
- End with: subscribe/like CTA + deity mantra
- Speak directly to the listener (உங்களுக்கு, நீங்கள்)
- Emotional, devotional, warm tone
- Include astrological connections (graha, dosham references)
- This is narration script, NOT a song. Write in speaking style.
- Do NOT include any headings, brackets, or formatting. Just flowing Tamil text.
"""

TRENDING_PROMPT = """You are a Tamil devotional content strategist. Analyze these current trending topics in the Hindu/Tamil devotional world and suggest the BEST video topic for today.

Current date: {date}
Day: {day}
Upcoming festivals in India: {festivals}

Trending search data:
{trends}

Pick ONE specific topic that is:
1. Highly searchable right now
2. Has devotional/religious angle
3. Will attract Tamil YouTube audience
4. Can be made into 5000-char Tamil narration script

Return ONLY the topic string, no explanation. Example:
"செவ்வாய்கிழமை முருகன் விரதம் 7 பலன்கள்"
OR
"தீபாவளி 2026 லட்சுமி பூஜை முறை மற்றும் 5 பலன்கள்"
OR
"சிவராத்திரி 2026 விரதம் மற்றும் சிவன் அருள் பெற 7 வழிகள்"
"""

TITLE_PROMPT = """Generate a YouTube title in this exact format for a Tamil devotional video.
Topic: {topic}
Deity: {deity} ({deity_en})
Emoji: {emoji}

Format: [Tamil day + deity + key benefit] {emoji} [engaging hook] | [English equivalent] | ஆலய மணி

Example: செவ்வாய் முருகன் விரதம் 7 பலன்கள் 🔱 வாழ்க்கையே மாறும் | Tuesday Murugan | ஆலய மணி

Give ONLY the title, nothing else."""

DESC_PROMPT = """Generate a YouTube description for Tamil devotional video.
Topic: {topic}
Deity: {deity} ({deity_en})
Emoji: {emoji}

Include:
- Topic line with emoji
- 7 benefits as numbered list with emoji
- Listen instruction (daily/weekly)
- Subscribe + Like + Comment CTA in Tamil
- Email: aalayamani.official@gmail.com
- Series links placeholder
- Keywords line (Tamil + English)
- Hashtags: #ஆலயமணி #AalayaMani {hashtags} #TamilDevotional #தமிழ்பக்தி

Keep under 3000 characters. Mix Tamil and English for SEO."""

TAGS_PROMPT = """Generate YouTube tags (comma separated) for Tamil devotional video.
Topic: {topic}
Deity: {deity} ({deity_en})

Include Tamil + English tags. 20-25 tags total. Include: deity name in Tamil, deity name in English, day of worship, dosham pariharam, aalaya mani, tamil devotional 2026, trending tamil devotional {year}.

Give ONLY comma-separated tags, nothing else."""

PINNED_PROMPT = """Generate a YouTube pinned comment for Tamil devotional video.
Topic: {topic}
Deity: {deity}
Emoji: {emoji}

Include:
- Ask which benefit they need (numbered 1-7)
- Ask to comment their answer
- Subscribe CTA with bell emoji
- Deity mantra at end

Keep under 500 characters. Tamil only."""


# =============================================
# UTILITY FUNCTIONS
# =============================================

def run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def get_dur(f):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", f])
    return int(float(r.stdout.strip()))


def is_gif(f):
    return f.lower().endswith(".gif")


def check_prerequisites():
    for tool in ["ffmpeg", "ffprobe", "edge-tts"]:
        if not shutil.which(tool):
            print(f"ERROR: {tool} not installed"); sys.exit(1)
    if not os.path.exists(IMAGE_FILE):
        if os.environ.get("CI"):
            print(f"  WARNING: {IMAGE_FILE} not found — will generate placeholder")
        else:
            print(f"ERROR: {IMAGE_FILE} not found"); sys.exit(1)


def ensure_dirs():
    for d in [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR]:
        os.makedirs(d, exist_ok=True)


def get_gemini_model():
    genai.configure(api_key=GEMINI_KEY)
    return genai.GenerativeModel("gemini-2.0-flash")


def genai_retry(func, *args, max_retries=5, **kwargs):
    """Call a Gemini API function with retry on quota errors."""
    import time
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = str(e)
            if "429" in err or "ResourceExhausted" in err or "quota" in err.lower():
                wait = min(30 * (2 ** attempt), 300)
                print(f"  ⏳ Quota hit (attempt {attempt+1}/{max_retries}), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Gemini API failed after {max_retries} retries")


def trim_prefix(text, prefix):
    if text.startswith(prefix):
        return text[len(prefix):].strip()
    return text.strip()


def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return []


def save_queue(queue):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)


def has_today_video():
    today = datetime.datetime.now().strftime("%A").lower()
    video_path = f"{OUTPUT_DIR}/{today}_video.mp4"
    return os.path.exists(video_path)


def get_festivals_today():
    today = datetime.datetime.now()
    key = (today.month, today.day)
    return HINDU_FESTIVALS.get(key, "")


def get_upcoming_festivals(days=14):
    now = datetime.datetime.now()
    results = []
    for (m, d), name in sorted(HINDU_FESTIVALS.items()):
        dt = datetime.datetime(now.year, m, d)
        diff = (dt - now).days
        if 0 <= diff <= days:
            results.append(f"{name} ({diff} days away)")
    return "; ".join(results[:5])


# =============================================
# TRENDING TOPICS
# =============================================

def fetch_google_trends():
    """Scrape Google Trends hot searches for India devotional queries."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }
        urls = [
            "https://trends.google.com/trends/trendingsearches/daily?geo=IN",
            "https://trends.google.com/trends/trendingsearches/daily?geo=IN&cat=0",
        ]
        trends_text = ""
        for url in urls:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                items = soup.find_all("div", class_="title")
                for item in items[:20]:
                    t = item.get_text(strip=True)
                    if t:
                        trends_text += f"- {t}\n"
        return trends_text
    except:
        return ""


def fetch_youtube_trending():
    """Fetch trending Tamil devotional videos from YouTube web."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        urls = [
            "https://www.youtube.com/results?search_query=tamil+devotional+today&sp=CAMSAhAB",
            "https://www.youtube.com/results?search_query=tamil+bhakthi+song+trending&sp=CAMSAhAB",
        ]
        trends_text = ""
        for url in urls:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                scripts = soup.find_all("script")
                for script in scripts:
                    if "var ytInitialData" in script.text:
                        trends_text += f"[YouTube data fetched for query]\n"
                        break
        return trends_text
    except:
        return ""


def fetch_god_temple_news():
    """Fetch news about Tamil temples and events."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        urls = [
            "https://www.dinamani.com/tamilnadu",
            "https://temple.dinamalar.com/news.php",
        ]
        news_text = ""
        for url in urls:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    t = a.get_text(strip=True)
                    keywords = ["கோயில்", "திருவிழா", "பூஜை", "அபிஷேகம்",
                                "temple", "festival", "devotional", "பக்தி",
                                "விரதம்", "வழிபாடு"]
                    if any(k in t.lower() for k in keywords):
                        news_text += f"- {t[:100]}\n"
        return news_text
    except:
        return ""


def discover_trending_topic():
    """Use Gemini to find the best trending devotional topic for today."""
    print("  Scanning trending topics...")
    now = datetime.datetime.now()
    day_name = now.strftime("%A")

    trends_data = fetch_google_trends()
    yt_data = fetch_youtube_trending()
    temple_news = fetch_god_temple_news()
    festivals = get_upcoming_festivals()

    combined_trends = (
        f"--- Google Trends India ---\n{trends_data}\n"
        f"--- YouTube Trending ---\n{yt_data}\n"
        f"--- Temple News ---\n{temple_news}\n"
    )
    if not combined_trends.strip():
        combined_trends = "No specific trending data available. Use day-based default."

    model = get_gemini_model()
    prompt = TRENDING_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        day=day_name,
        festivals=festivals,
        trends=combined_trends
    )
    resp = genai_retry(model.generate_content, prompt)
    topic = resp.text.strip().strip('"').strip("'")
    print(f"  Trending topic: {topic}")
    return topic


# =============================================
# SCRIPT & METADATA GENERATION
# =============================================

def generate_script(topic):
    model = get_gemini_model()
    resp = genai_retry(model.generate_content, SCRIPT_PROMPT.format(topic=topic))
    return resp.text


def generate_metadata(config):
    model = get_gemini_model()
    metadata = {}

    print("  Generating title...")
    r = genai_retry(model.generate_content, TITLE_PROMPT.format(**config))
    metadata["title"] = r.text.strip()

    print("  Generating description...")
    r = genai_retry(model.generate_content, DESC_PROMPT.format(**config))
    metadata["description"] = r.text.strip()

    year = datetime.datetime.now().year
    print("  Generating tags...")
    r = genai_retry(model.generate_content, TAGS_PROMPT.format(**config, year=year))
    metadata["tags"] = r.text.strip()

    print("  Generating pinned comment...")
    r = genai_retry(model.generate_content, PINNED_PROMPT.format(**config))
    metadata["pinned_comment"] = r.text.strip()

    return metadata


# =============================================
# VIDEO CREATION
# =============================================

def create_video(script_text, image, output_name, bgm, bgm_vol=0.20):
    ensure_dirs()

    script_file = f"/tmp/{output_name}_script.txt"
    voice_file = f"/tmp/{output_name}_voice.mp3"
    human_file = f"/tmp/{output_name}_human.mp3"
    mixed_file = f"/tmp/{output_name}_mixed.mp3"
    video_file = f"{OUTPUT_DIR}/{output_name}_video.mp4"
    short_file = f"{SHORTS_DIR}/{output_name}_short.mp4"

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_text)

    print("  Step 1/5 Voice...")
    try:
        r = run(["edge-tts", "--file", script_file, "--voice", "ta-IN-PallaviNeural",
                 "--rate", "-8%", "--pitch", "+2Hz", "--write-media", voice_file],
                timeout=600)
    except subprocess.TimeoutExpired:
        print("ERROR: edge-tts timed out (>600s)"); return None
    if r.returncode != 0:
        print(f"ERROR voice: {r.stderr[-200:]}"); return None
    dur = get_dur(voice_file)
    print(f"    {dur}s")
    shutil.copy(voice_file, human_file)
    dur = get_dur(human_file)

    if os.path.exists(bgm):
        print("  Step 3/5 BGM...")
        fo = max(0, dur - 3)
        bfo = max(0, dur - 4)
        fc = (
            "[0:a]volume=1.0,afade=t=in:st=0:d=2,afade=t=out:st={}:d=3[voice];"
            "[1:a]volume={},afade=t=in:st=0:d=4,afade=t=out:st={}:d=4[bg];"
            "[voice][bg]amix=inputs=2:duration=first:dropout_transition=3[out]"
        ).format(fo, bgm_vol, bfo)
        run(["ffmpeg", "-y", "-i", human_file, "-i", bgm,
             "-filter_complex", fc, "-map", "[out]", "-ac", "2", mixed_file])
        audio = mixed_file if os.path.exists(mixed_file) else human_file
    else:
        audio = human_file
        print("  Step 3/5 No BGM")

    print("  Step 4/5 Video...")
    scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
    if is_gif(image):
        cmd = ["ffmpeg", "-y", "-ignore_loop", "0", "-i", image, "-i", audio,
               "-vf", scale + ",setsar=1", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video_file]
    else:
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image, "-i", audio,
               "-vf", scale + ",setsar=1", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "28", "-tune", "stillimage", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", video_file]
    r = run(cmd, timeout=600)
    if r.returncode != 0:
        print(f"ERROR video: {r.stderr[-200:]}"); return None
    mb = os.path.getsize(video_file) / (1024 * 1024)
    print(f"    {mb:.1f}MB")

    print("  Step 5/5 Short...")
    ss = 30 if dur > 90 else 10
    run(["ffmpeg", "-y", "-i", video_file, "-ss", str(ss), "-t", "50",
         "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", short_file],
        timeout=120)

    for f in [script_file, voice_file, human_file, mixed_file]:
        if os.path.exists(f):
            os.remove(f)

    return video_file


# =============================================
# YOUTUBE UPLOAD
# =============================================

def get_token_from_env():
    """Restore YouTube token pickle from base64 env var (for CI/GitHub Actions)."""
    b64 = os.environ.get("YOUTUBE_TOKEN_BASE64")
    if b64:
        try:
            raw = base64.b64decode(b64)
            creds = pickle.loads(raw)
            print("  Restored YouTube token from env YOUTUBE_TOKEN_BASE64")
            return creds
        except Exception as e:
            print(f"  Warning: could not decode YOUTUBE_TOKEN_BASE64: {e}")

    b64 = os.environ.get("CLIENT_SECRETS_BASE64")
    if b64:
        try:
            raw = base64.b64decode(b64)
            with open(YOUTUBE_CLIENT_SECRETS, "wb") as f:
                f.write(raw)
            print("  Restored client_secrets.json from env CLIENT_SECRETS_BASE64")
        except Exception as e:
            print(f"  Warning: could not decode CLIENT_SECRETS_BASE64: {e}")

    return None


def get_authenticated_service():
    creds = None

    # Restore token from env var (GitHub Actions)
    env_creds = get_token_from_env()
    if env_creds:
        creds = env_creds

    if not creds and os.path.exists(YOUTUBE_TOKEN_FILE):
        with open(YOUTUBE_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(YOUTUBE_CLIENT_SECRETS):
                print(f"\nERROR: {YOUTUBE_CLIENT_SECRETS} not found!")
                print("To get it:")
                print("1. Go to https://console.cloud.google.com/")
                print("2. Create a project → Enable YouTube Data API v3")
                print("3. Create OAuth 2.0 credentials (Desktop app)")
                print("4. Download JSON and save as", YOUTUBE_CLIENT_SECRETS)
                print("\nOr run: python aalaya_mani_bot.py --auth-youtube\n")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                YOUTUBE_CLIENT_SECRETS, YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(YOUTUBE_TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(video_path, metadata, privacy="public"):
    """Upload video to YouTube. Returns video ID or None."""
    print(f"  Uploading: {os.path.basename(video_path)}...")

    if not os.path.exists(video_path):
        print(f"  ERROR: Video not found: {video_path}")
        return None

    youtube = get_authenticated_service()
    if not youtube:
        return None

    body = {
        "snippet": {
            "title": metadata["title"][:100],
            "description": metadata["description"][:5000],
            "tags": [t.strip() for t in metadata["tags"].split(",")][:30],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = request.execute()
        video_id = response["id"]
        print(f"  ✅ Uploaded: https://youtu.be/{video_id}")

        # Set pinned comment
        if metadata.get("pinned_comment"):
            try:
                youtube.commentThreads().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "topLevelComment": {
                                "snippet": {
                                    "textOriginal": metadata["pinned_comment"]
                                }
                            }
                        }
                    }
                ).execute()
                print("  ✅ Pinned comment set")
            except Exception as e:
                print(f"  ⚠ Could not pin comment: {e}")

        return video_id

    except Exception as e:
        print(f"  ❌ Upload failed: {e}")
        return None


def auth_youtube():
    """Run YouTube OAuth flow to generate token."""
    print("Authenticating with YouTube...")
    service = get_authenticated_service()
    if service:
        print("✅ YouTube authentication successful!")
        print(f"   Token saved to: {YOUTUBE_TOKEN_FILE}")
    return service


# =============================================
# PROCESSING PIPELINES
# =============================================

def process_day(day, image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Full pipeline for one day: trending → script → metadata → video → upload."""
    image = image or IMAGE_FILE
    bgm = bgm or BGM_FILE
    config = dict(DAY_CONFIG[day])

    topic = config["topic"]
    emoji = config["emoji"]
    deity = config["deity"]
    deity_en = config["deity_en"]

    print(f"\n{'='*50}")
    print(f"{emoji} {deity} — {deity_en} (trending-aware)")
    print(f"{'='*50}")

    # Check if festival is happening
    festival = get_festivals_today()
    if festival:
        enhanced_topic = f"{festival} - {topic}"
        print(f"  📅 Festival today: {festival}")
        config["topic"] = enhanced_topic

    print("Generating script...")
    script = generate_script(config["topic"])
    print(f"  Script: {len(script)} chars")

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(f"{SCRIPTS_DIR}/{day}.txt", "w", encoding="utf-8") as f:
        f.write(script)

    print("Generating YouTube metadata...")
    metadata = generate_metadata(config)

    os.makedirs(METADATA_DIR, exist_ok=True)
    with open(f"{METADATA_DIR}/{day}.txt", "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{metadata['title']}\n\n")
        f.write(f"DESCRIPTION:\n{metadata['description']}\n\n")
        f.write(f"TAGS:\n{metadata['tags']}\n\n")
        f.write(f"PINNED COMMENT:\n{metadata['pinned_comment']}\n")

    print("Creating video...")
    video = create_video(script, image, day, bgm, bgm_vol)

    if video:
        print(f"\n  ✅ VIDEO: {video}")
        print(f"  ✅ SHORT: {SHORTS_DIR}/{day}_short.mp4")
        print(f"  ✅ METADATA: {METADATA_DIR}/{day}.txt")
        print(f"\n  TITLE: {metadata['title']}")

        if upload:
            upload_to_youtube(video, metadata, privacy)
    else:
        print("  ❌ Video creation failed")

    return video


def process_trending(image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Trending topic based video generation."""
    image = image or IMAGE_FILE
    bgm = bgm or BGM_FILE
    print(f"\n{'='*50}")
    print("  🔥 TRENDING TOPIC MODE")
    print(f"{'='*50}")

    topic = discover_trending_topic()
    if not topic:
        print("  No trending topic found. Falling back to today's deity.")
        day = datetime.datetime.now().strftime("%A").lower()
        return process_day(day, image, bgm, bgm_vol, upload, privacy)

    safe_name = hashlib.md5(topic.encode()).hexdigest()[:8]
    config = {
        "topic": topic,
        "deity": "",
        "deity_en": "",
        "emoji": "🙏",
        "hashtags": "#தமிழ்பக்தி #ஆலயமணி #AalayaMani #TrendingDevotional",
    }

    print(f"\nGenerating script for trending topic...")
    script = generate_script(topic)
    print(f"  Script: {len(script)} chars")

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(f"{SCRIPTS_DIR}/trending_{safe_name}.txt", "w", encoding="utf-8") as f:
        f.write(script)

    print("Generating YouTube metadata...")
    metadata = generate_metadata(config)

    os.makedirs(METADATA_DIR, exist_ok=True)
    with open(f"{METADATA_DIR}/trending_{safe_name}.txt", "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{metadata['title']}\n\n")
        f.write(f"DESCRIPTION:\n{metadata['description']}\n\n")
        f.write(f"TAGS:\n{metadata['tags']}\n\n")
        f.write(f"PINNED COMMENT:\n{metadata['pinned_comment']}\n")

    print("Creating video...")
    video = create_video(script, image, f"trending_{safe_name}", bgm, bgm_vol)

    if video:
        print(f"\n  ✅ VIDEO: {video}")
        print(f"  ✅ METADATA: {METADATA_DIR}/trending_{safe_name}.txt")

        if upload:
            upload_to_youtube(video, metadata, privacy)
    else:
        print("  ❌ Video creation failed")

    return video


def upload_pending_videos(privacy="public"):
    """Upload all pending videos from queue."""
    queue = load_queue()
    if not queue:
        print("No pending uploads.")
        return

    for item in queue:
        vpath = item.get("video_path")
        if not os.path.exists(vpath):
            print(f"  Skipping (not found): {vpath}")
            continue
        print(f"\nUploading: {vpath}")
        metadata = item.get("metadata", {})
        vid = upload_to_youtube(vpath, metadata, privacy)
        if vid:
            item["status"] = "uploaded"
            item["video_id"] = vid
    save_queue(queue)


# =============================================
# DAEMON / SCHEDULER
# =============================================

def create_today_content():
    """Create content for today and queue for upload."""
    day = datetime.datetime.now().strftime("%A").lower()
    print(f"\n{'#'*50}")
    print(f"#  📅 Daily Job: {day.upper()}")
    print(f"{'#'*50}")

    video_path = f"{OUTPUT_DIR}/{day}_video.mp4"

    if os.path.exists(video_path):
        print("Video already exists for today. Checking upload queue...")
        queue = load_queue()
        already_queued = any(
            day in item.get("video_path", "") for item in queue
        )
        if already_queued:
            print("Already queued for upload. Skipping.")
            return
        print("Will generate trending bonus content instead.")

    # Always try trending first
    trending_topic = discover_trending_topic()

    # Generate main day video
    print("\n--- Main Day Video ---")
    video = process_day(day, upload=False)

    if video:
        metadata_path = f"{METADATA_DIR}/{day}.txt"
        metadata = {}
        if os.path.exists(metadata_path):
            with open(metadata_path, encoding="utf-8") as f:
                content = f.read()
                parts = content.split("\n\n")
                for part in parts:
                    if part.startswith("TITLE:"):
                        metadata["title"] = trim_prefix(part, "TITLE:")
                    elif part.startswith("DESCRIPTION:"):
                        metadata["description"] = trim_prefix(part, "DESCRIPTION:")
                    elif part.startswith("TAGS:"):
                        metadata["tags"] = trim_prefix(part, "TAGS:")
                    elif part.startswith("PINNED COMMENT:"):
                        metadata["pinned_comment"] = trim_prefix(part, "PINNED COMMENT:")

        queue = load_queue()
        queue.append({
            "video_path": video,
            "metadata": metadata,
            "day": day,
            "created": datetime.datetime.now().isoformat(),
            "status": "pending",
        })
        save_queue(queue)
        print(f"  ✅ Queued for upload: {os.path.basename(video)}")

    # Generate trending bonus video if different from day topic
    if trending_topic and DAY_CONFIG[day]["topic"] not in trending_topic:
        print("\n--- Trending Bonus Video ---")
        safe_name = hashlib.md5(trending_topic.encode()).hexdigest()[:8]
        bonus_video = f"{OUTPUT_DIR}/trending_{safe_name}_video.mp4"
        if not os.path.exists(bonus_video):
            process_trending(upload=False)
            bonus_video_path = f"{OUTPUT_DIR}/trending_{safe_name}_video.mp4"
            if os.path.exists(bonus_video_path):
                bonus_metadata_path = f"{METADATA_DIR}/trending_{safe_name}.txt"
                bonus_meta = {}
                if os.path.exists(bonus_metadata_path):
                    with open(bonus_metadata_path, encoding="utf-8") as f:
                        content = f.read()
                        parts = content.split("\n\n")
                        for part in parts:
                            if part.startswith("TITLE:"):
                                bonus_meta["title"] = trim_prefix(part, "TITLE:")
                            elif part.startswith("DESCRIPTION:"):
                                bonus_meta["description"] = trim_prefix(part, "DESCRIPTION:")
                            elif part.startswith("TAGS:"):
                                bonus_meta["tags"] = trim_prefix(part, "TAGS:")
                            elif part.startswith("PINNED COMMENT:"):
                                bonus_meta["pinned_comment"] = trim_prefix(part, "PINNED COMMENT:")

                queue = load_queue()
                queue.append({
                    "video_path": bonus_video_path,
                    "metadata": bonus_meta,
                    "day": f"trending_{safe_name}",
                    "created": datetime.datetime.now().isoformat(),
                    "status": "pending",
                })
                save_queue(queue)
                print(f"  ✅ Bonus queued for upload: trending_{safe_name}")

    # Try to upload pending
    print("\n--- Uploading Pending ---")
    upload_pending_videos()

    return True


def should_schedule_at(hour, minute):
    """Check if we should run the upload at this time."""
    now = datetime.datetime.now()
    return now.hour == hour and now.minute == minute


def run_scheduler_cycle():
    """Run one cycle of the scheduler (check what to do now)."""
    now = datetime.datetime.now()
    hour = now.hour
    minute = now.minute

    # Generate content once per day at 5:00 AM
    if hour == 5 and minute == 0:
        print(f"\n[{now}] ⏰ Generating today's content...")
        create_today_content()

    # Upload at scheduled times
    is_weekend = now.weekday() >= 5
    upload_times = WEEKEND_UPLOAD_TIMES if is_weekend else WEEKDAY_UPLOAD_TIMES

    for (uh, um) in upload_times:
        if hour == uh and minute == um:
            print(f"\n[{now}] ⏰ Upload window opened!")
            upload_pending_videos()


def daemon_mode():
    """Run 24/7 scheduler."""
    print("\n" + "=" * 50)
    print("  ஆலய மணி BOT — DAEMON MODE")
    print("  Auto-generates & uploads daily")
    print("=" * 50)
    print(f"\nSchedule:")
    print(f"  05:00 — Generate today's content + trending topic")
    print(f"  Weekdays 06:00, 18:30 — Auto-upload")
    print(f"  Weekends 07:00, 19:30 — Auto-upload")
    print(f"\nYouTube uploads enabled: {os.path.exists(YOUTUBE_TOKEN_FILE)}")
    print(f"\nPress Ctrl+C to stop\n")

    # Schedule jobs
    schedule.every().day.at("05:00").do(create_today_content)

    for (h, m) in WEEKDAY_UPLOAD_TIMES:
        t = f"{h:02d}:{m:02d}"
        schedule.every().monday.at(t).do(upload_pending_videos)
        schedule.every().tuesday.at(t).do(upload_pending_videos)
        schedule.every().wednesday.at(t).do(upload_pending_videos)
        schedule.every().thursday.at(t).do(upload_pending_videos)
        schedule.every().friday.at(t).do(upload_pending_videos)

    for (h, m) in WEEKEND_UPLOAD_TIMES:
        t = f"{h:02d}:{m:02d}"
        schedule.every().saturday.at(t).do(upload_pending_videos)
        schedule.every().sunday.at(t).do(upload_pending_videos)

    # Immediate first-run check
    create_today_content()
    upload_pending_videos()

    while True:
        schedule.run_pending()
        time.sleep(30)


# =============================================
# MAIN
# =============================================

def main():
    parser = argparse.ArgumentParser(
        description="ஆலய மணி — Fully Automated Devotional Content Bot"
    )
    parser.add_argument("--day", help="Day: monday/tuesday/.../sunday/today/all")
    parser.add_argument("--topic", help="Custom topic")
    parser.add_argument("--output", default="custom", help="Output name for custom topic")
    parser.add_argument("--image", default=IMAGE_FILE, help="Image file")
    parser.add_argument("--bgm", default=BGM_FILE, help="BGM file")
    parser.add_argument("--bgm-volume", type=float, default=0.20, help="BGM volume")
    parser.add_argument("--upload", action="store_true", help="Upload to YouTube after creation")
    parser.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"],
                        help="YouTube privacy setting")
    parser.add_argument("--daemon", action="store_true", help="Run 24/7 scheduler")
    parser.add_argument("--trending", action="store_true", help="Generate trending topic video")
    parser.add_argument("--upload-pending", action="store_true", help="Upload all pending")
    parser.add_argument("--auth-youtube", action="store_true", help="Authenticate YouTube OAuth")
    args = parser.parse_args()

    check_prerequisites()
    ensure_dirs()

    print("\n========================================")
    print("  ஆலய மணி — Full Automation v2.0")
    print("========================================")

    if args.auth_youtube:
        auth_youtube()
        return

    if args.daemon:
        daemon_mode()
        return

    if args.upload_pending:
        upload_pending_videos(args.privacy)
        return

    if args.trending:
        process_trending(args.image, args.bgm, args.bgm_volume, args.upload, args.privacy)
        return

    if args.topic:
        print(f"Custom Topic: {args.topic}")
        safe = hashlib.md5(args.topic.encode()).hexdigest()[:8]
        config = {
            "topic": args.topic,
            "deity": "",
            "deity_en": "",
            "emoji": "🙏",
            "hashtags": "#ஆலயமணி #AalayaMani #TamilDevotional",
        }
        print("Generating script...")
        script = generate_script(args.topic)
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        with open(f"{SCRIPTS_DIR}/{args.output}.txt", "w", encoding="utf-8") as f:
            f.write(script)
        print("Generating metadata...")
        metadata = generate_metadata(config)
        os.makedirs(METADATA_DIR, exist_ok=True)
        with open(f"{METADATA_DIR}/{args.output}.txt", "w", encoding="utf-8") as f:
            f.write(f"TITLE:\n{metadata['title']}\n\n")
            f.write(f"DESCRIPTION:\n{metadata['description']}\n\n")
            f.write(f"TAGS:\n{metadata['tags']}\n\n")
            f.write(f"PINNED COMMENT:\n{metadata['pinned_comment']}\n")
        print("Creating video...")
        video = create_video(script, args.image, args.output, args.bgm, args.bgm_volume)
        if video and args.upload:
            upload_to_youtube(video, metadata, args.privacy)
        return

    if args.day == "today":
        day = datetime.datetime.now().strftime("%A").lower()
        process_day(day, args.image, args.bgm, args.bgm_volume, args.upload, args.privacy)
    elif args.day == "all":
        for day in DAY_CONFIG:
            process_day(day, args.image, args.bgm, args.bgm_volume,
                        args.upload, args.privacy)
    elif args.day in DAY_CONFIG:
        process_day(args.day, args.image, args.bgm, args.bgm_volume,
                    args.upload, args.privacy)
    elif args.day:
        print(f"Unknown day: {args.day}")
        print(f"Valid: {', '.join(DAY_CONFIG.keys())}, today, all")
    else:
        print("Usage:")
        print("  python aalaya_mani_bot.py --daemon           # 24/7 auto pilot")
        print("  python aalaya_mani_bot.py --day today        # today's deity")
        print("  python aalaya_mani_bot.py --day all --upload # all 7 + upload")
        print("  python aalaya_mani_bot.py --trending         # trending topic")
        print("  python aalaya_mani_bot.py --auth-youtube     # YouTube auth")
        print("  python aalaya_mani_bot.py --topic '...'      # custom topic")

    print("\n========================================")
    print("  DONE! Upload: studio.youtube.com")
    print("========================================")


if __name__ == "__main__":
    main()
