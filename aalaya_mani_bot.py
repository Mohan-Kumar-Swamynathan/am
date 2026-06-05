#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║            ஆலய மணி — FULLY AUTOMATED BOT v2.0               ║
║  Script + Voice + Video + Trending + YouTube Upload          ║
║  Runs 24/7 — automatically posts at optimal times            ║
╚═══════════════════════════════════════════════════════════════╝

Setup:
  pip install google-genai groq edge-tts google-api-python-client \
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
import concurrent.futures
import datetime
import json
import os
import random
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
    import google.genai as genai
except ImportError:
    print("pip install google-genai"); sys.exit(1)

try:
    from groq import Groq
except ImportError:
    Groq = None

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
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False


# =============================================
# CONFIGURATION
# =============================================
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
BGM_FILE = "bgm.mp3"
IMAGE_FILE = "image.png"
OUTPUT_DIR = "videos"
SHORTS_DIR = "shorts"
METADATA_DIR = "metadata"
SCRIPTS_DIR = "scripts"
QUEUE_FILE = "upload_queue.json"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube",
                  "https://www.googleapis.com/auth/youtube.upload"]
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
    # Pongal / Thai
    (1, 13): "பொங்கல் தினம்", (1, 14): "பொங்கல் திருநாள்",
    (1, 15): "மாட்டுப் பொங்கல்", (1, 16): "காணும் பொங்கல்",
    (1, 23): "தை பூசம் Thai Pusam — முருகன் சிறப்பு",
    # Masi
    (2, 21): "மகா சிவராத்திரி — சிவன் சிறப்பு",
    (2, 26): "மாசி மகம் — புனித நீராடல்",
    # Panguni
    (3, 14): "ஹோலி", (3, 29): "பங்குனி உத்திரம் — பெருமாள் சிறப்பு",
    # Chithirai
    (4, 14): "தமிழ் புத்தாண்டு — சித்திரை திருநாள்",
    (4, 15): "மீனாட்சி திருக்கல்யாணம்",
    (4, 18): "சித்திரா பௌர்ணமி — சிவன் சிறப்பு",
    # Vaikasi
    (5, 24): "வைகாசி விசாகம் — முருகன் சிறப்பு",
    # Aani
    (6, 15): "ஆனி திருமஞ்சனம் — நடராஜர் சிறப்பு",
    # Aadi
    (7, 18): "ஆடி பூரம் — அம்மன் சிறப்பு",
    (7, 25): "ஆடி பெருக்கு — நதி வழிபாடு",
    # Avani
    (8, 16): "கோகுலாஷ்டமி — கிருஷ்ணர் சிறப்பு",
    (8, 27): "விநாயகர் சதுர்த்தி",
    # Purattasi
    (9, 7): "ஓணம்", (9, 20): "புரட்டாசி சனிக்கிழமை — பெருமாள் சிறப்பு",
    # Aippasi
    (10, 2): "சரஸ்வதி பூஜை — நவராத்திரி",
    (10, 3): "ஆயுத பூஜை", (10, 4): "விஜயதசமி",
    (10, 20): "தீபாவளி — லட்சுமி சிறப்பு",
    # Karthigai
    (11, 1): "கார்த்திகை சோமவாரம் — சிவன் சிறப்பு",
    (11, 10): "ஸ்கந்த சஷ்டி — முருகன் சூரசம்ஹாரம்",
    (11, 15): "கார்த்திகை தீபம் — திருவண்ணாமலை",
    # Margazhi
    (12, 1): "மார்கழி தொடக்கம் — திருப்பாவை திருவெம்பாவை",
    (12, 25): "வைகுண்ட ஏகாதசி — பெருமாள் சிறப்பு",
}

# Tamil month awareness for content relevance
TAMIL_MONTHS = {
    1: ("தை", "சூரியன் + பொங்கல் content dominates"),
    2: ("மாசி", "சிவராத்திரி + மாசி மகம் content"),
    3: ("பங்குனி", "பங்குனி உத்திரம் + பெருமாள் திருக்கல்யாணம்"),
    4: ("சித்திரை", "தமிழ் புத்தாண்டு + மீனாட்சி திருக்கல்யாணம்"),
    5: ("வைகாசி", "வைகாசி விசாகம் + முருகன் content peaks"),
    6: ("ஆனி", "ஆனி திருமஞ்சனம் + நடராஜர் content"),
    7: ("ஆடி", "அம்மன் content peaks — ஆடி வெள்ளி viral season"),
    8: ("ஆவணி", "கிருஷ்ணர் ஜெயந்தி + விநாயகர் சதுர்த்தி"),
    9: ("புரட்டாசி", "புரட்டாசி சனி viral + நவராத்திரி buildup"),
    10: ("ஐப்பசி", "நவராத்திரி + தீபாவளி — BIGGEST month for views"),
    11: ("கார்த்திகை", "கார்த்திகை தீபம் + ஸ்கந்த சஷ்டி + சிவன் content"),
    12: ("மார்கழி", "திருப்பாவை + வைகுண்ட ஏகாதசி + பெருமாள் content"),
}

EVERGREEN_VIRAL_TOPICS = [
    "ராகு கேது தோஷம் பரிகாரம்",
    "செவ்வாய் தோஷம் திருமணத் தடை நீக்கும் பரிகாரம்",
    "சனி பகவான் தோஷம் — யாருக்கு நல்லது யாருக்கு கெட்டது",
    "வீட்டில் குத்துவிளக்கு ஏற்ற வேண்டிய நேரமும் முறையும்",
    "கோயிலில் செய்யக்கூடாத 7 தவறுகள்",
    "நவக்கிரக தோஷம் நீக்கும் 9 கோயில்கள்",
    "அர்ச்சனை சொல்லும்போது எந்த நாமம் சொல்ல வேண்டும்",
    "108 திவ்ய தேசங்கள் — சென்றால் என்ன பலன்",
    "உங்கள் ஜன்ம நட்சத்திர கோயில் எது",
    "தீட்டு காலத்தில் பூஜை செய்யலாமா — உண்மை என்ன",
    "வாஸ்து படி வீட்டில் கடவுள் படம் வைக்கும் இடம்",
    "திருமண தடை நீக்க 7 சக்தி வாய்ந்த கோயில்கள்",
    "பிள்ளையார் சுழி ஏன் போடுகிறோம் — மறைந்த ரகசியம்",
    "சனிக்கிழமை எண்ணெய் தேய்க்கலாமா — உண்மை vs மூடநம்பிக்கை",
    "கர்ப்பிணி பெண்கள் எந்த கோயிலுக்கு செல்ல வேண்டும்",
]

SCRIPT_FORMATS = [
    "BENEFITS: List 7 powerful benefits of worshipping this deity on this day. Each benefit should have a real-life scenario, astrological reasoning, and emotional impact. Hook: 'ஏழாவது பலன் உங்களை ஆச்சரியப்படுத்தும்'",
    "STORY: Tell a captivating ancient Puranic story about this deity that most people don't know. Weave the moral into practical life advice. Include dramatic moments, dialogues between gods, and a surprising twist. End with what devotees can learn from this story today.",
    "SIGNS: Describe 7 mystical signs that this deity is already blessing the listener. Make it deeply personal — 'இந்த அறிகுறி உங்களுக்கு இருந்தால் நீங்கள் மிகவும் அதிர்ஷ்டசாலி'. Include dreams, coincidences, life events that signal divine grace.",
    "MISTAKES: Reveal 7 common mistakes devotees unknowingly make when worshipping this deity. Use a concerned, caring tone — not fear-based. Explain the right way to do each thing. 'இதை தெரியாமல் செய்கிறார்கள்' hook.",
    "SECRETS: Share 7 little-known temple secrets and rituals related to this deity. Include specific temples, rare practices, hidden meanings behind common rituals. Make the listener feel they're learning insider knowledge.",
    "TRANSFORMATION: Tell 3 powerful real-life transformation stories of devotees whose lives changed after praying to this deity. One about health, one about wealth, one about relationships. Make them emotional and relatable. End each with what the devotee did specifically.",
    "MANTRA: Explain the meaning and hidden power behind the most important mantras of this deity. Break down each word's meaning. Explain when to chant, how many times, and what happens spiritually when you chant. Include the science behind mantra vibrations.",
    "QUESTIONS: Start with a provocative question the listener has always wondered about. 'ஏன் சிவனுக்கு மூன்று கண்?' 'முருகனுக்கு ஏன் இரண்டு மனைவிகள்?' Answer it with deep philosophical and mythological reasoning. Then connect to 5 practical life lessons.",
]

SCRIPT_PROMPT = """You are a brilliant Tamil devotional storyteller for YouTube channel "ஆலய மணி". You speak like a wise temple priest sharing wisdom with a close friend — warm, emotional, sometimes humorous, always engaging.

Write a LONG Tamil devotional YouTube narration script about: {topic}

FORMAT TO USE: {format_style}

VOICE & TONE:
- Sound like a wise grandmother telling stories, NOT a textbook or robot
- Use rhetorical questions to engage: "தெரியுமா?", "என்ன நினைக்கிறீர்கள்?"
- Add emotional pauses with "..." between powerful moments
- Use vivid descriptions: sounds of temple bells, smell of camphor, feeling of peace
- Vary sentence length — mix short punchy sentences with longer flowing ones
- Include one moment that might make the listener emotional or get goosebumps

STRUCTURE:
- Start with a HOOK in the first 2 sentences — a question, a surprising fact, or a bold claim
- Then: வணக்கம். ஆலய மணி சேனலுக்கு வரவேற்கிறோம்.
- Build the content following the FORMAT above
- Include specific pariharam (remedy) section with exact steps
- End with a powerful emotional closing + deity mantra
- Last line: லைக், ஷேர், சப்ஸ்கிரைப் CTA (but make it feel natural, not forced)

STRICT RULES:
- Write ONLY in Tamil script. NO English words except deity names and mantras.
- MINIMUM 5000 Tamil characters. Each section needs 5-8 detailed sentences.
- This is spoken narration — write how people TALK, not how they write essays.
- NO headings, NO brackets, NO bullet points. Just flowing Tamil speech.
- NO repetitive phrases — every sentence should add something new.
- Make the listener feel this video was made specifically for THEM.
"""

TRENDING_PROMPT = """You are a Tamil devotional YouTube content strategist with deep knowledge of Hindu calendar, festivals, astrology, and what Tamil devotional audience searches for.

TODAY: {date} ({day})
TAMIL MONTH: {tamil_month} — {month_trend}
UPCOMING FESTIVALS: {festivals}
TODAY'S DEITY (day-based): {today_deity}

ADDITIONAL TRENDING SIGNALS:
{trends}

YOUR TASK: Pick the SINGLE BEST video topic for TODAY that will get MAXIMUM views.

DECISION FRAMEWORK (in this priority order):
1. Is there a MAJOR festival TODAY or in 2 days? → Create festival-specific content (e.g., "சிவராத்திரி விரதம் 7 ரகசியங்கள்")
2. Is there a festival in 3-7 days? → Create preparation/preview content (e.g., "வைகாசி விசாகம் நெருங்குகிறது — 5 முக்கிய தயாரிப்புகள்")
3. Is there an astrological event happening NOW? (graha peyarchi, eclipse, rahu kalam special) → Create astrology content
4. Is this Tamil month known for specific worship? → Create month-special content (e.g., ஆடி = அம்மன், மார்கழி = திருப்பாவை)
5. None of the above? → Pick from these PROVEN VIRAL topics that ALWAYS get views:
   - செவ்வாய் தோஷம் / ராகு கேது தோஷம் / சனி தோஷம் (dosham content)
   - "இந்த கோயிலுக்கு போனால்..." (specific temple content)
   - "தெரியாமல் செய்யும் தவறுகள்" (mistakes content)
   - "இந்த அறிகுறி இருந்தால்..." (signs content)
   - Deity-specific deep content for today's day

IMPORTANT:
- Topic MUST be specific, not generic (bad: "சிவன் பற்றி" → good: "சிவன் கோயிலில் செய்யக்கூடாத 7 தவறுகள்")
- Topic MUST sound like a real YouTube title people would click
- Topic MUST be in Tamil (English words only for proper nouns)
- Include a number if possible (7 பலன்கள், 5 ரகசியங்கள், 3 கதைகள்)

Return ONLY the topic string, nothing else. Example:
"சிவராத்திரி 2026 — சிவன் கோயிலில் இரவு முழுவதும் விழித்திருந்தால் என்ன நடக்கும்?"
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

Keep under 3000 characters. Mix Tamil and English for SEO.

IMPORTANT: Tamil is the primary language. Add English SEO keywords naturally where specified."""

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


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def get_dur(f):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", f])
    return int(float(r.stdout.strip()))


def ensure_images():
    """Generate placeholder images if none exist."""
    if os.path.exists(IMAGE_FILE) or os.path.isdir("images"):
        return
    log("🎨 No images found — generating placeholders...")
    try:
        from PIL import Image, ImageDraw, ImageFont
        os.makedirs("images", exist_ok=True)
        bg_colors = [(20,20,40), (40,20,30), (20,40,30), (30,30,50), (50,20,20)]
        overlays = ['ஆலய மணி', 'அருள் தரும்', 'பக்தி வழி', 'மந்திர ஒலி', 'ஆன்மிகம்']
        for i, (c, t) in enumerate(zip(bg_colors, overlays)):
            img = Image.new("RGB", (1920, 1080), color=c)
            d = ImageDraw.Draw(img)
            d.text((960, 540), t, fill=(255, 215, 0), font=ImageFont.load_default(), anchor="mm")
            img.save(f"images/bg_{i}.png")
        log(f"  Created {len(overlays)} placeholder images in images/")
    except Exception as e:
        log(f"  Warning: could not generate placeholders: {e}")


def check_prerequisites():
    for tool in ["ffmpeg", "ffprobe", "edge-tts"]:
        if not shutil.which(tool):
            print(f"ERROR: {tool} not installed"); sys.exit(1)
    if not GEMINI_KEY and not GROQ_API_KEY:
        print("ERROR: No LLM API key set!")
        print("  Set GEMINI_KEY: export GEMINI_KEY='your_key'")
        print("  Get free key: https://aistudio.google.com/apikey")
        sys.exit(1)
    ensure_images()
    ensure_bgm()


def ensure_bgm():
    """Generate copyright-free BGM if not found."""
    if os.path.exists(BGM_FILE):
        return
    log("🎵 No BGM found — generating copyright-free ambient track...")
    r = run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anoisesrc=d=600:c=pink:r=44100:a=0.02",
        "-af", "lowpass=f=300,equalizer=f=150:t=q:w=0.5:g=10,"
               "equalizer=f=100:t=q:w=0.5:g=8,"
               "aecho=0.8:0.6:100|150:0.3|0.2,volume=0.3",
        BGM_FILE
    ])
    if r.returncode == 0:
        log(f"  ✅ Generated copyright-free BGM: {BGM_FILE}")
    else:
        log("  ⚠️ BGM generation failed — videos will have voice only")


def ensure_dirs():
    for d in [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR]:
        os.makedirs(d, exist_ok=True)


def call_llm(prompt, max_retries=3):
    """Call LLM: Groq → Gemini fallback. Retries + exponential backoff on failure."""
    errs = []

    # Provider 1: Groq (if key set)
    if GROQ_API_KEY:
        for attempt in range(max_retries):
            try:
                client = Groq(api_key=GROQ_API_KEY)
                resp = client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=GROQ_MODEL, temperature=0.7, max_tokens=2500,
                )
                return resp.choices[0].message.content
            except Exception as e:
                wait = 10 * (attempt + 1)
                log(f"⏳ Groq error (attempt {attempt+1}/{max_retries}), retry in {wait}s: {str(e)[:80]}")
                errs.append(str(e))
                time.sleep(wait)
        log("⚠️ Groq failed, falling back to Gemini...")

    # Provider 2: Gemini fallback via new google.genai SDK
    client = genai.Client(api_key=GEMINI_KEY)
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            return resp.text
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = min(30 * (2 ** attempt), 300)
                log(f"⏳ Gemini quota hit (attempt {attempt+1}/{max_retries}), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise Exception(f"All LLM providers failed after retries. Errors: {'; '.join(errs[:3])}")


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
    """Use Gemini + calendar intelligence to find the best topic for today."""
    log("🔍 Analyzing trending topics...")
    now = datetime.datetime.now()
    day_name = now.strftime("%A")

    # Tamil month awareness
    month_num = now.month
    tamil_month, month_trend = TAMIL_MONTHS.get(month_num, ("", ""))

    # Today's deity
    day_deity_map = {
        "Monday": "சிவன் (Shiva)",
        "Tuesday": "முருகன் (Murugan)",
        "Wednesday": "விநாயகர் (Vinayagar)",
        "Thursday": "பெருமாள் (Perumal/Guru)",
        "Friday": "லட்சுமி/அம்மன் (Lakshmi/Amman)",
        "Saturday": "ஐயப்பன்/சனி (Ayyappan/Shani)",
        "Sunday": "சூரியன் (Surya/Navagraha)",
    }
    today_deity = day_deity_map.get(day_name, "")

    # Try scraping (but don't depend on it)
    trends_data = ""
    try:
        trends_data += fetch_google_trends() or ""
        trends_data += fetch_youtube_trending() or ""
        trends_data += fetch_god_temple_news() or ""
    except:
        pass

    # Add evergreen viral topics as fallback options
    if not trends_data.strip():
        viral_sample = random.sample(EVERGREEN_VIRAL_TOPICS, 5)
        trends_data = "No live trending data. Here are proven viral topics:\n"
        for t in viral_sample:
            trends_data += f"- {t}\n"

    festivals = get_upcoming_festivals()

    prompt = TRENDING_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        day=day_name,
        tamil_month=tamil_month,
        month_trend=month_trend,
        today_deity=today_deity,
        festivals=festivals or "No major festivals in next 14 days",
        trends=trends_data
    )
    topic = call_llm(prompt).strip().strip('"').strip("'")
    log(f"🔥 Trending topic: {topic}")
    return topic


# =============================================
# SCRIPT & METADATA GENERATION
# =============================================

def generate_script(topic):
    t0 = time.time()
    # Pick a random format for variety
    format_style = random.choice(SCRIPT_FORMATS)
    log(f"  Format: {format_style.split(':')[0]}")
    text = call_llm(SCRIPT_PROMPT.format(topic=topic, format_style=format_style))
    # Retry once if script is too short
    if len(text) < 3000:
        log(f"  Script too short ({len(text)} chars), retrying with emphasis on length...")
        retry_prompt = SCRIPT_PROMPT.format(topic=topic, format_style=format_style) + "\n\nIMPORTANT: Your previous response was only " + str(len(text)) + " characters. Write at LEAST 5000 characters. Make each section much longer with examples and stories."
        text = call_llm(retry_prompt)
    log(f"  Script generated ({len(text)} chars) in {time.time()-t0:.0f}s")
    return text


def generate_metadata(config):
    metadata = {}
    t0 = time.time()

    log("  Title...")
    metadata["title"] = call_llm(TITLE_PROMPT.format(**config)).strip()

    log("  Description...")
    metadata["description"] = call_llm(DESC_PROMPT.format(**config)).strip()

    year = datetime.datetime.now().year
    log("  Tags...")
    metadata["tags"] = call_llm(TAGS_PROMPT.format(**config, year=year)).strip()

    log("  Pinned comment...")
    metadata["pinned_comment"] = call_llm(PINNED_PROMPT.format(**config)).strip()

    log(f"  Metadata complete ({time.time()-t0:.0f}s)")
    return metadata


# =============================================
# VIDEO CREATION — Ken Burns + Slideshow
# =============================================

def find_images(image_src):
    """Find images from file, comma-separated list, directory, or glob."""
    if not image_src:
        return []
    exts = (".png", ".jpg", ".jpeg", ".webp")

    # Directory → scan for images
    if os.path.isdir(image_src):
        found = []
        for f in sorted(os.listdir(image_src)):
            if f.lower().endswith(exts):
                found.append(os.path.join(image_src, f))
        return found[:10]

    # Glob pattern
    if "*" in image_src or "?" in image_src:
        import glob as _glob
        found = sorted(_glob.glob(image_src))
        return [f for f in found if f.lower().endswith(exts)][:10]

    # Comma-separated
    if "," in image_src:
        parts = [p.strip() for p in image_src.split(",")]
        return [p for p in parts if os.path.exists(p) and p.lower().endswith(exts)]

    # Single file
    if os.path.exists(image_src) and image_src.lower().endswith(exts):
        return [image_src]
    return [image_src]


def build_video_filter(images, total_frames, fps=25):
    """
    Build ffmpeg filter_complex string for multi-image Ken Burns + crossfade.
    Returns (inputs, filter_string, output_label).
    """
    num = len(images)
    seg_frames = total_frames // num
    overlap = int(fps * 0.8)  # 0.8s crossfade

    filters = []
    for i, img in enumerate(images):
        z = f"if(lte(on,1),1.0,min(1.0+0.0015*on,1.25))"
        filters.append(
            f"[{i}:v]loop=loop=-1:size=1:start=0,"
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='{z}':d={seg_frames}:fps={fps}:s=1920x1080,"
            f"trim=0:{seg_frames / fps:.2f},setpts=PTS-STARTPTS[v{i}]"
        )

    # Crossfade chain
    prev = "v0"
    xfade_dur = 0.8
    for i in range(1, num):
        offset = i * seg_frames / fps - xfade_dur
        label = f"x{i}"
        filters.append(
            f"[{prev}][v{i}]xfade=transition=fade:duration={xfade_dur}:offset={max(0,offset):.2f}[{label}]"
        )
        prev = label

    return num, ";".join(filters), prev


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

    log("🔊 Step 1/5 Voice (edge-tts)...")
    t0 = time.time()
    try:
        r = run(["edge-tts", "--file", script_file, "--voice", "ta-IN-PallaviNeural",
                 "--rate=-8%", "--pitch=+2Hz", "--write-media", voice_file],
                timeout=600)
    except subprocess.TimeoutExpired:
        log("❌ edge-tts timed out (>600s)"); return None
    if r.returncode != 0:
        log(f"❌ Voice error: {r.stderr[-200:]}"); return None
    dur = get_dur(voice_file)
    log(f"  Voice: {dur}s ({time.time()-t0:.0f}s generation)")

    log("🎧 Step 2/5 Humanizing voice...")
    r = run(["ffmpeg", "-y", "-i", voice_file, "-af", FEMALE_HUMANIZE, human_file])
    if r.returncode != 0:
        log("  ⚠️ Humanization failed, using raw voice")
        shutil.copy(voice_file, human_file)
    else:
        log("  ✅ Voice humanized (warm + reverb + vibrato)")
    dur = get_dur(human_file)

    if os.path.exists(bgm):
        log("🎵 Step 3/5 BGM mixing...")
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

    # ── Step 3: Video with Ken Burns + slideshow ──
    log("🎬 Step 3/5 Video (Ken Burns + slideshow)...")
    t0 = time.time()

    images = find_images(image)
    if not images:
        log(f"❌ No images found from: {image}")
        return None

    log(f"🖼️ Images: {len(images)} — {[os.path.basename(i)[:20] for i in images]}")

    fps = 25
    total_frames = max(int(dur * fps), 25)
    num_inputs, vfilter, vlabel = build_video_filter(images, total_frames, fps)

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y"]
    for img in images:
        cmd.extend(["-loop", "1", "-t", str(dur + 2), "-i", img])
    cmd.extend(["-i", audio, "-filter_complex", vfilter,
                "-map", f"[{vlabel}]", "-map", str(num_inputs) + ":a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                "-avoid_negative_ts", "make_zero", video_file])

    log(f"  Encoding {num_inputs} images × {dur}s @ {fps}fps...")
    r = run(cmd, timeout=600)
    if r.returncode != 0:
        log(f"⚠️ Slideshow failed, falling back to single image...")
        # Fallback: single image with simple zoom
        fallback_img = images[0]
        cmd2 = ["ffmpeg", "-y", "-loop", "1", "-i", fallback_img, "-i", audio,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video_file]
        r = run(cmd2, timeout=600)
        if r.returncode != 0:
            log(f"❌ Video error: {r.stderr[-200:]}")
            return None

    mb = os.path.getsize(video_file) / (1024 * 1024)
    log(f"  Video: {mb:.1f}MB ({time.time()-t0:.0f}s encode)")

    log("📱 Step 4/5 Short...")
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
    log(f"⬆️ Uploading: {os.path.basename(video_path)}...")

    if not os.path.exists(video_path):
        log(f"❌ Video not found: {video_path}")
        return None

    youtube = get_authenticated_service()
    if not youtube:
        return None

    body = {
        "snippet": {
            "title": metadata["title"][:100],
            "description": metadata["description"][:5000],
            "tags": [t.strip() for t in metadata["tags"].split(",")][:30],
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    t0 = time.time()

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = request.execute()
        video_id = response["id"]
        log(f"✅ Uploaded: https://youtu.be/{video_id} ({time.time()-t0:.0f}s)")

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
                log("  ✅ Pinned comment set")
            except Exception as e:
                log(f"  ⚠ Comment failed: {e}")

        return video_id

    except Exception as e:
        log(f"❌ Upload failed: {e}")
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
    """Full pipeline for one day: script + metadata (parallel) → video → upload."""
    image = image or IMAGE_FILE
    bgm = bgm or BGM_FILE
    config = dict(DAY_CONFIG[day])
    t_start = datetime.datetime.now()

    topic = config["topic"]
    emoji = config["emoji"]
    deity = config["deity"]
    deity_en = config["deity_en"]

    log(f"{'='*50}")
    log(f"{emoji} {deity} — {deity_en}")
    log(f"{'='*50}")

    festival = get_festivals_today()
    if festival:
        enhanced_topic = f"{festival} - {topic}"
        log(f"📅 Festival today: {festival}")
        config["topic"] = enhanced_topic

    # Run script & metadata generation in parallel
    log("🤖 Generating script + YouTube metadata (parallel)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sf = pool.submit(generate_script, config["topic"])
        mf = pool.submit(generate_metadata, config)
        script = sf.result()
        metadata = mf.result()
    log(f"✅ Script: {len(script)} chars | Title: {metadata.get('title','')[:60]}...")

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(f"{SCRIPTS_DIR}/{day}.txt", "w", encoding="utf-8") as f:
        f.write(script)

    os.makedirs(METADATA_DIR, exist_ok=True)
    with open(f"{METADATA_DIR}/{day}.txt", "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{metadata['title']}\n\n")
        f.write(f"DESCRIPTION:\n{metadata['description']}\n\n")
        f.write(f"TAGS:\n{metadata['tags']}\n\n")
        f.write(f"PINNED COMMENT:\n{metadata['pinned_comment']}\n")

    log("🎬 Creating video...")
    video = create_video(script, image, day, bgm, bgm_vol)

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    if video:
        log(f"✅ VIDEO: {video}")
        log(f"✅ SHORT: {SHORTS_DIR}/{day}_short.mp4")
        log(f"📺 {metadata['title']}")

        if upload:
            log("⬆️ Uploading to YouTube...")
            upload_to_youtube(video, metadata, privacy)
            log("✅ Upload complete")
    else:
        log("❌ Video creation failed")

    log(f"⏱️ Total: {elapsed:.0f}s")
    return video


def process_trending(image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Trending topic based video generation."""
    image = image or IMAGE_FILE
    bgm = bgm or BGM_FILE
    t_start = datetime.datetime.now()
    log(f"{'='*50}")
    log("🔥 TRENDING TOPIC MODE")
    log(f"{'='*50}")

    topic = discover_trending_topic()
    if not topic:
        log("  No trending topic. Falling back to today's deity.")
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

    # Parallel script + metadata
    log("🤖 Generating script + metadata (parallel)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sf = pool.submit(generate_script, topic)
        mf = pool.submit(generate_metadata, config)
        script = sf.result()
        metadata = mf.result()
    log(f"✅ Script: {len(script)} chars | {metadata.get('title','')[:60]}...")

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(f"{SCRIPTS_DIR}/trending_{safe_name}.txt", "w", encoding="utf-8") as f:
        f.write(script)

    os.makedirs(METADATA_DIR, exist_ok=True)
    with open(f"{METADATA_DIR}/trending_{safe_name}.txt", "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{metadata['title']}\n\n")
        f.write(f"DESCRIPTION:\n{metadata['description']}\n\n")
        f.write(f"TAGS:\n{metadata['tags']}\n\n")
        f.write(f"PINNED COMMENT:\n{metadata['pinned_comment']}\n")

    log("🎬 Creating video...")
    video = create_video(script, image, f"trending_{safe_name}", bgm, bgm_vol)

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    if video:
        log(f"✅ VIDEO: {video}")

        if upload:
            log("⬆️ Uploading...")
            upload_to_youtube(video, metadata, privacy)
    else:
        log("❌ Video creation failed")

    log(f"⏱️ Total: {elapsed:.0f}s")

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
    if not HAS_SCHEDULE:
        print("ERROR: pip install schedule"); sys.exit(1)
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
