#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║            ஆலய மணி — FULLY AUTOMATED BOT v5.1               ║
║  Script + Voice + Video + Trending + YouTube Upload          ║
║  Font fix · Shorts 40s · Script checks · CI alerts · v5.1    ║
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
GEMINI_KEY      = os.environ.get("GEMINI_KEY", "")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
PEXELS_API_KEY  = os.environ.get("PEXELS_API_KEY", "")   # ← set this env var
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GH_MODEL        = "gpt-4o-mini"
GROQ_MODEL      = "llama-3.3-70b-versatile"
GEMINI_MODEL_ECONOMY  = "gemini-1.5-flash"
GEMINI_MODEL_STANDARD = "gemini-2.0-flash"
GEMINI_MODEL_PREMIUM  = "gemini-2.5-flash"

# Script length targets — 5 min video
TARGET_MIN = 7000    # ~4 min minimum
TARGET_MAX = 10500   # ~5.5 min hard cap
BGM_FILE        = "bgm.mp3"
IMAGE_FILE      = "image.png"
OUTPUT_DIR      = "videos"
SHORTS_DIR      = "shorts"
METADATA_DIR    = "metadata"
SCRIPTS_DIR     = "scripts"
PEXELS_DIR      = "pexels_images"
QUEUE_FILE      = "upload_queue.json"
YOUTUBE_SCOPES  = ["https://www.googleapis.com/auth/youtube",
                   "https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_TOKEN_FILE     = "youtube_token.pickle"
SLEEP_PLAYLIST_ID      = os.environ.get("SLEEP_PLAYLIST_ID", "")  # set in GitHub secrets
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"

# Voice EQ: warm Tamil female voice — clear highs, gentle warmth, temple reverb
FEMALE_HUMANIZE = (
    "highpass=f=80,"
    "equalizer=f=250:t=q:w=0.8:g=3,"
    "equalizer=f=800:t=q:w=0.9:g=2,"
    "equalizer=f=2500:t=q:w=1:g=2,"
    "equalizer=f=5000:t=q:w=1:g=-3,"
    "equalizer=f=8000:t=q:w=1:g=-4,"
    "vibrato=f=3.8:d=0.025,"            # reduced: 5.5→3.8Hz, 0.04→0.025 depth (less robotic)
    "aecho=0.6:0.15:20|35:0.08|0.05,"  # tighter echo (less room reverb)
    "acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=2,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)

MALE_HUMANIZE = (
    "highpass=f=70,"
    "equalizer=f=150:t=q:w=0.7:g=2,"   # chest resonance
    "equalizer=f=500:t=q:w=0.8:g=1.5,"
    "equalizer=f=2000:t=q:w=1:g=2,"
    "equalizer=f=6000:t=q:w=1:g=-2,"
    "vibrato=f=3.2:d=0.018,"            # very subtle on male
    "acompressor=threshold=-16dB:ratio=2:attack=6:release=60:makeup=2.5,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)
# ═══════════════════════════════════════════════════════════════
# FREE MEDIA: Wikimedia Commons + Pollinations AI
# ═══════════════════════════════════════════════════════════════

AM_WIKIMEDIA_QUERIES = {
    "முருகன்":   ["Murugan temple Tamil Nadu gopuram", "Kartikeya sculpture South India"],
    "சிவன்":    ["Shiva temple Tamil Nadu ancient", "Nataraja bronze Chola sculpture"],
    "விநாயகர்": ["Ganesha sculpture Tamil Nadu", "Pillayar temple South India"],
    "நடராஜர்":  ["Nataraja bronze sculpture Chola", "Shiva dance sculpture India"],
    "ஐயப்பன்":  ["Ayyappa temple Kerala", "Sabarimala temple South India"],
    "அம்மன்":   ["Amman temple Tamil Nadu festival", "Mariamman temple Tamil Nadu"],
    "பெருமாள்": ["Vishnu temple Tamil Nadu Vaishnava", "Perumal temple gopuram"],
    "கிருஷ்ணர்":["Krishna temple South India", "Guruvayur temple Kerala"],
    "லட்சுமி":  ["Lakshmi temple South India gold", "Mahalakshmi sculpture India"],
    "சூரியன்":  ["Sun temple India Surya", "Konark sun temple India"],
    "default":  ["Hindu temple gopuram Tamil Nadu", "Dravidian temple architecture India"],
}

def fetch_wikimedia_images_am(deity_name, output_dir, count=4):
    """Fetch real temple/deity photos from Wikimedia Commons — truly free CC license."""
    import urllib.parse
    queries = AM_WIKIMEDIA_QUERIES.get(deity_name, AM_WIKIMEDIA_QUERIES["default"])
    images = []
    os.makedirs(output_dir, exist_ok=True)
    for query in queries[:2]:
        try:
            params = {
                "action": "query", "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrlimit": str(count * 2), "prop": "imageinfo",
                "iiprop": "url|size|mime", "iiurlwidth": "1920", "format": "json"
            }
            resp = requests.get("https://commons.wikimedia.org/w/api.php",
                               params=params, timeout=10).json()
            pages = resp.get("query", {}).get("pages", {})
            for page in pages.values():
                ii = page.get("imageinfo", [{}])[0]
                url = ii.get("thumburl", "") or ii.get("url", "")
                mime = ii.get("mime", "")
                if url and "image" in mime and not url.endswith(".svg"):
                    r = requests.get(url, timeout=30, stream=True)
                    if r.status_code == 200:
                        fname = os.path.join(output_dir, f"wiki_{len(images)}.jpg")
                        with open(fname, "wb") as f:
                            for chunk in r.iter_content(8192): f.write(chunk)
                        images.append(fname)
                        if len(images) >= count:
                            return images
        except Exception as e:
            log(f"  ⚠️ Wikimedia: {e}")
    return images


def fetch_pollinations_image_am(deity_en, topic, output_path):
    """Free AI-generated unique image — no API key, no cost, unique per video."""
    import urllib.parse, random
    prompt = (f"ancient {deity_en} Hindu temple Tamil Nadu South India, "
              f"golden hour dramatic lighting, intricate stone carvings, "
              f"devotees worship, cinematic wide shot, photorealistic 8K HDR, "
              f"no text no watermark")
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
           f"?width=1920&height=1080&nologo=true&enhance=true&seed={random.randint(1,99999)}")
    try:
        r = requests.get(url, timeout=20, stream=True)
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(8192): f.write(chunk)
            log(f"  🎨 AI image generated: {os.path.basename(output_path)}")
            return output_path
    except Exception as e:
        log(f"  ⚠️ Pollinations: {e}")
    return None


def add_end_screen(youtube_service, video_id, duration_seconds):
    """Add subscribe button + recent upload card in last 20 seconds."""
    end_ms = max(0, int(duration_seconds) - 20) * 1000
    try:
        youtube_service.videos().update(
            part="endScreenContent",
            body={
                "id": video_id,
                "endScreenContent": {
                    "elements": [
                        {
                            "type": "SUBSCRIBE",
                            "position": {"cornerPosition": "TOP_RIGHT", "type": "CORNER"},
                            "startOffsetMs": str(end_ms),
                            "durationMs": "20000",
                        },
                        {
                            "type": "RECENT_UPLOAD",
                            "position": {"cornerPosition": "BOTTOM_LEFT", "type": "CORNER"},
                            "startOffsetMs": str(end_ms),
                            "durationMs": "20000",
                        },
                    ]
                }
            }
        ).execute()
        log("  ✅ End screen added (subscribe + recent video)")
    except Exception as e:
        log(f"  ⚠️ End screen: {e}")


# Upload schedule: (hour, minute)
WEEKDAY_UPLOAD_TIMES = [(6, 0), (18, 30)]
WEEKEND_UPLOAD_TIMES = [(7, 0), (19, 30)]

# Day → primary deity mapping (used as SIGNAL, not hard rule)
# LLM can override based on festivals/trends
DAY_DEITY_MAP = {
    "monday":    {"deity": "சிவன்",    "deity_en": "Shiva",    "emoji": "🕉",  "hashtags": "#சிவன் #MondayShiva #OmNamashivaya"},
    "tuesday":   {"deity": "முருகன்",  "deity_en": "Murugan",  "emoji": "🔱",  "hashtags": "#முருகன் #TuesdayMurugan #VelMurugan"},
    "wednesday": {"deity": "விநாயகர்", "deity_en": "Vinayagar","emoji": "🐘",  "hashtags": "#விநாயகர் #WednesdayVinayagar #Pillaiyar"},
    "thursday":  {"deity": "பெருமாள்", "deity_en": "Perumal",  "emoji": "🙏",  "hashtags": "#பெருமாள் #ThursdayPerumal #Govinda"},
    "friday":    {"deity": "லட்சுமி",  "deity_en": "Lakshmi",  "emoji": "🪷",  "hashtags": "#லட்சுமி #FridayLakshmi #MahaLakshmi"},
    "saturday":  {"deity": "ஐயப்பன்",  "deity_en": "Ayyappan", "emoji": "🎵",  "hashtags": "#ஐயப்பன் #SaturdayAyyappan #SwamiyeSaranam"},
    "sunday":    {"deity": "சூரியன்",  "deity_en": "Surya",    "emoji": "🌞",  "hashtags": "#சூரியன் #SundaySurya #SuryaBhagavan"},
}

DEITY_EMOJI_MAP = {
    "சிவன்": "🕉", "முருகன்": "🔱", "விநாயகர்": "🐘",
    "பெருமாள்": "🙏", "லட்சுமி": "🪷", "ஐயப்பன்": "🎵",
    "சூரியன்": "🌞", "அம்மன்": "🌺", "கிருஷ்ணர்": "🦚",
    "சரஸ்வதி": "🎵", "": "🙏",
}

DEITY_HASHTAG_MAP = {
    "சிவன்":    "#சிவன் #Shiva #OmNamashivaya #MondayShiva",
    "முருகன்":  "#முருகன் #Murugan #VelMurugan #TuesdayMurugan",
    "விநாயகர்": "#விநாயகர் #Ganesh #Pillaiyar #WednesdayVinayagar",
    "பெருமாள்": "#பெருமாள் #Perumal #Govinda #Vishnu",
    "லட்சுமி":  "#லட்சுமி #Lakshmi #MahaLakshmi #FridayLakshmi",
    "ஐயப்பன்":  "#ஐயப்பன் #Ayyappan #SwamiyeSaranam #Sabarimala",
    "சூரியன்":  "#சூரியன் #Surya #SuryaBhagavan #Navagraha",
    "அம்மன்":   "#அம்மன் #Amman #Durgai #ShaktiPeeth",
    "கிருஷ்ணர்":"#கிருஷ்ணர் #Krishna #Govinda #RadheKrishna",
    "சரஸ்வதி":  "#சரஸ்வதி #Saraswati #Vidyadevi #Navaratri",
    "":          "#தமிழ்பக்தி #TamilDevotional #ஆலயமணி",
}

HINDU_FESTIVALS = {
    (1, 13): "பொங்கல் தினம்", (1, 14): "பொங்கல் திருநாள்",
    (1, 15): "மாட்டுப் பொங்கல்", (1, 16): "காணும் பொங்கல்",
    (1, 23): "தை பூசம் Thai Pusam — முருகன் சிறப்பு",
    (2, 21): "மகா சிவராத்திரி — சிவன் சிறப்பு",
    (2, 26): "மாசி மகம் — புனித நீராடல்",
    (3, 14): "ஹோலி", (3, 29): "பங்குனி உத்திரம் — பெருமாள் சிறப்பு",
    (4, 14): "தமிழ் புத்தாண்டு — சித்திரை திருநாள்",
    (4, 15): "மீனாட்சி திருக்கல்யாணம்",
    (4, 18): "சித்திரா பௌர்ணமி — சிவன் சிறப்பு",
    (5, 24): "வைகாசி விசாகம் — முருகன் சிறப்பு",
    (6, 15): "ஆனி திருமஞ்சனம் — நடராஜர் சிறப்பு",
    (7, 18): "ஆடி பூரம் — அம்மன் சிறப்பு",
    (7, 25): "ஆடி பெருக்கு — நதி வழிபாடு",
    (8, 16): "கோகுலாஷ்டமி — கிருஷ்ணர் சிறப்பு",
    (8, 27): "விநாயகர் சதுர்த்தி",
    (9, 7): "ஓணம்", (9, 20): "புரட்டாசி சனிக்கிழமை — பெருமாள் சிறப்பு",
    (10, 2): "சரஸ்வதி பூஜை — நவராத்திரி",
    (10, 3): "ஆயுத பூஜை", (10, 4): "விஜயதசமி",
    (10, 20): "தீபாவளி — லட்சுமி சிறப்பு",
    (11, 1): "கார்த்திகை சோமவாரம் — சிவன் சிறப்பு",
    (11, 10): "ஸ்கந்த சஷ்டி — முருகன் சூரசம்ஹாரம்",
    (11, 15): "கார்த்திகை தீபம் — திருவண்ணாமலை",
    (12, 1): "மார்கழி தொடக்கம் — திருப்பாவை திருவெம்பாவை",
    (12, 25): "வைகுண்ட ஏகாதசி — பெருமாள் சிறப்பு",
}

TAMIL_MONTHS = {
    1:  ("தை",       "சூரியன் + பொங்கல் content dominates"),
    2:  ("மாசி",     "சிவராத்திரி + மாசி மகம் content"),
    3:  ("பங்குனி",  "பங்குனி உத்திரம் + பெருமாள் திருக்கல்யாணம்"),
    4:  ("சித்திரை", "தமிழ் புத்தாண்டு + மீனாட்சி திருக்கல்யாணம்"),
    5:  ("வைகாசி",  "வைகாசி விசாகம் + முருகன் content peaks"),
    6:  ("ஆனி",     "ஆனி திருமஞ்சனம் + நடராஜர் content"),
    7:  ("ஆடி",     "அம்மன் content peaks — ஆடி வெள்ளி viral season"),
    8:  ("ஆவணி",   "கிருஷ்ணர் ஜெயந்தி + விநாயகர் சதுர்த்தி"),
    9:  ("புரட்டாசி","புரட்டாசி சனி viral + நவராத்திரி buildup"),
    10: ("ஐப்பசி",  "நவராத்திரி + தீபாவளி — BIGGEST month for views"),
    11: ("கார்த்திகை","கார்த்திகை தீபம் + ஸ்கந்த சஷ்டி + சிவன் content"),
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

# =============================================
# ANTI-MONOTONY: DEITY VOICE PERSONAS
# =============================================
DEITY_VOICE = {
    "சிவன்": (
        "நீங்கள் பேசுவது ஒரு தியான யோகியைப் போல — ஆழமான, அமைதியான, தத்துவமான குரல். "
        "வாக்கியங்கள் மெதுவாக, நிறைவாக இருக்கட்டும். அமைதியான இடைவெளிகள் அர்த்தம் தரட்டும். "
        "சிவனின் மூன்றாவது கண், தாண்டவம், கைலாசம் — இவற்றின் படிமங்களை உணர்வுடன் கொண்டுவாருங்கள். "
        "கேட்பவர் கண் மூடி தியானத்தில் இருப்பதுபோல் உணரட்டும்."
    ),
    "முருகன்": (
        "யுவ ஆற்றலும் போர் வீர உணர்வும் கொண்ட குரல் பேசுங்கள். வேகமான தாளம், தீர்க்கமான வாக்கியங்கள். "
        "முருகனின் வேல் போல் கூர்மையான வார்த்தைகள் பயன்படுத்துங்கள். "
        "கேட்பவர் தங்கள் வாழ்வில் வெற்றி பெறுவார்கள் என்ற நம்பிக்கையை ஏற்படுத்துங்கள். "
        "திருப்புகழின் ஓசை, காவடி சித்திரம், சூரசம்ஹாரம் — இவற்றை உயிரோடு கொண்டுவாருங்கள்."
    ),
    "விநாயகர்": (
        "அன்பான, சிறிது நகைச்சுவையான, அரவணைக்கும் குரல் பேசுங்கள். "
        "ஒரு நேசமான மாமாவைப்போல், எந்த தடையும் ஒரு புதிர் என்று சொல்வதுபோல். "
        "மோதக வாசனை, தும்பிக்கை வளைவு, சிரிக்கும் கண்கள் — இவற்றை உணர்வுடன் சித்தரியுங்கள். "
        "கேட்பவர் தங்கள் பிரச்சினைகள் கரைவதுபோல் உணரட்டும்."
    ),
    "பெருமாள்": (
        "பக்தி சொட்டும், சரணாகதி உணர்வு மிகுந்த குரல் பேசுங்கள். "
        "திவ்யப் பிரபந்தத்தின் இனிமை, கருணை கடல், நம்மாழ்வாரின் பாசுரங்கள் — இவை தொக்கி நிற்கட்டும். "
        "கேட்பவர் பெருமாளின் திருவடிகளில் சரண் அடைவதுபோல் உணரட்டும். "
        "வாக்கியங்கள் நதி ஓட்டம்போல் மெதுவாக, தொடர்ச்சியாக இருக்கட்டும்."
    ),
    "லட்சுமி": (
        "மென்மையான, ஆசை தரும், உயர்ந்த இலட்சியங்களை தூண்டும் குரல் பேசுங்கள். "
        "செல்வம், அழகு, நிறைவு — இவற்றின் படிமங்களை ஆசையுடன் சித்தரியுங்கள். "
        "கேட்பவர் தங்களுக்கு செல்வம் வருவதற்கு தகுதியானவர்கள் என்று உணரட்டும். "
        "தாமரை மலர் மணம், வெண்ணிற ஒளி, தங்க மழை — இவற்றை வார்த்தைகளில் கொண்டுவாருங்கள்."
    ),
    "ஐயப்பன்": (
        "துறவு, ஒழுக்கம், சகோதரத்துவம் — இவற்றின் கடுமையான, ஆனால் அன்பான குரல் பேசுங்கள். "
        "சபரிமலை பாதையின் கஷ்டம், மாலை அணிவதன் புனிதம், சுவாமியே சரணம் என்ற முழக்கம் — "
        "இவை உடலில் சிலிர்ப்பை ஏற்படுத்தட்டும். "
        "கேட்பவர் ஒரு தீர்மானமான புனித யாத்திரையில் இருப்பதுபோல் உணரட்டும்."
    ),
    "சூரியன்": (
        "பொழுது விடியும் உற்சாகம், தன்னம்பிக்கை, புதுத் தொடக்கம் — இவற்றின் சுறுசுறுப்பான குரல் பேசுங்கள். "
        "சூரிய உதயத்தின் சிவப்பு ஒளி, ஆதித்ய ஹ்ருதயம், நவகிரகங்களின் தலைவன் — "
        "இவற்றை ஆற்றலுடன் சித்தரியுங்கள். "
        "கேட்பவர் ஒவ்வொரு நாளும் வெற்றிகரமாக தொடங்குவார்கள் என்ற உணர்வை ஏற்படுத்துங்கள்."
    ),
}

# =============================================
# ANTI-MONOTONY: VARIED HOOK STYLES
# =============================================
HOOK_STYLES = [
    "SHOCK_FACT: முதல் 2 வாக்கியங்களில் ஒரு அதிர்ச்சியான, யாரும் அறியாத உண்மையை சொல்லுங்கள். "
    "உதாரணம்: 'இந்த ஒரு விஷயம் தெரியாமல் நீங்கள் ஆயிரம் முறை கோயிலுக்கு போனாலும் பலன் இல்லை...'",

    "PAIN_POINT: கேட்பவரின் வலியில் நேரடியாக நுழையுங்கள். "
    "உதாரணம்: 'எத்தனை நேரம் பிரார்த்தனை செய்தாலும் பலன் கிட்டவில்லை என்று தோன்றுகிறதா? "
    "இன்று அந்த காரணம் தெரியும்...'",

    "MYSTERY_DROP: ஒரு மர்மத்தை நடுவில் போட்டுவிட்டு ஆரம்பியுங்கள். "
    "உதாரணம்: 'திருப்பதியில் ஒரு இரகசிய கதவு இருக்கிறது. அதை 200 ஆண்டுகளாக யாரும் திறக்கவில்லை...'",

    "STORY_MIDWAY: ஒரு கதையின் மையத்தில் தொடங்குங்கள் — எந்த முன்னுரையும் இல்லாமல். "
    "உதாரணம்: 'அன்று இரவு, அந்த ஏழை விவசாயி கோயில் கதவு மூடும் நேரத்தில் மண்டியிட்டு அழுதார்...'",

    "PROVOKE_QUESTION: கேட்பவரை நேரடியாக சவால் விடுங்கள். "
    "உதாரணம்: 'உங்கள் பூஜை கடவுளுக்கு உண்மையில் எட்டுகிறதா? இல்லை வெறும் பழக்கமா? "
    "இன்று தெளிவாகி விடும்...'",

    "CONTRAST_OPEN: இரண்டு முற்றிலும் மாறுபட்ட நிலைகளை ஒப்பிட்டு ஆரம்பியுங்கள். "
    "உதாரணம்: 'ஒரே தெருவில் இரண்டு பேர் — ஒருவருக்கு எல்லாம் கிடைக்கிறது, "
    "மற்றவருக்கு ஒன்றும் இல்லை. இரண்டு பேரும் கோயிலுக்கு போகிறார்கள். வித்தியாசம் என்ன?'",
]

# =============================================
# ANTI-MONOTONY: CONTENT STRUCTURES (not always "7")
# =============================================
CONTENT_STRUCTURES = [
    {
        "name": "7_BENEFITS",
        "instruction": (
            "7 பலன்கள் பட்டியல் — ஆனால் ஒவ்வொரு பலனும் ஒரு உண்மையான வாழ்க்கை சூழ்நிலையுடன் "
            "விளக்கப்படட்டும். ஜோதிட காரணம் சேருங்கள். ஏழாவது பலன் மிகவும் ஆச்சரியமானதாக இருக்கட்டும். "
            "Hook: 'ஏழாவது பலன் கேட்டால் கண்ணீர் வரும்...'"
        ),
    },
    {
        "name": "PURANIC_STORY",
        "instruction": (
            "யாரும் அறியாத ஒரு புராண கதையை 3 காட்சிகளாக சொல்லுங்கள். "
            "1ம் காட்சி: கதாபாத்திரங்கள் அறிமுகம் + சிக்கல். "
            "2ம் காட்சி: நெருக்கடியின் உச்சம் — கடவுளுக்கும் சவால். "
            "3ம் காட்சி: திருப்புமுனை + தெய்வீக தீர்வு. "
            "கடவுள்களுக்கு இடையே உரையாடல்கள் வேண்டும். முடிவில் இன்றைய வாழ்க்கைக்கு பாடம்."
        ),
    },
    {
        "name": "5_SECRETS",
        "instruction": (
            "யாரும் சொல்லாத 5 இரகசியங்கள் — கோயில் அர்ச்சகர்களுக்கு மட்டும் தெரிந்தவை என்ற உணர்வில். "
            "ஒவ்வொரு இரகசியமும் ஒரு குறிப்பிட்ட கோயில் அல்லது சடங்குடன் தொடர்புடையதாக இருக்கட்டும். "
            "கேட்பவர் உள்நாட்டு ஞானம் பெறுவதுபோல் உணரட்டும்."
        ),
    },
    {
        "name": "3_TRANSFORMATIONS",
        "instruction": (
            "3 உண்மையான பக்தர்களின் வாழ்க்கை மாற்றக் கதைகள். "
            "கதை 1: உடல் நலம் — யாரோ குணமடைந்த கதை. "
            "கதை 2: பொருளாதாரம் — ஏழ்மையிலிருந்து எழுந்த கதை. "
            "கதை 3: உறவுகள் — குடும்பம் கூடிய கதை. "
            "ஒவ்வொரு கதையிலும் அந்த பக்தர் என்ன குறிப்பிட்ட செயல் செய்தார் என்று சொல்லுங்கள்."
        ),
    },
    {
        "name": "SINGLE_DEEP_STORY",
        "instruction": (
            "ஒரே ஒரு நீண்ட, ஆழமான கதை — எந்த பட்டியலும் வேண்டாம். "
            "திரைப்படம்போல் ஆரம்பம், நடு, முடிவு இருக்கட்டும். "
            "கதாபாத்திரங்கள் உயிரோடு இருக்கட்டும். படிப்பினையை நேரடியாக சொல்லாதீர்கள் — "
            "கேட்பவரே புரிந்துகொள்வதுபோல் விடுங்கள். "
            "இது மிகவும் சக்திவாய்ந்த format — emotions maximum ஆக இருக்கட்டும்."
        ),
    },
    {
        "name": "MYTHS_VS_TRUTH",
        "instruction": (
            "5 பொதுவான நம்பிக்கைகளை எடுங்கள் — ஒவ்வொன்றையும் 'இப்படி நம்புகிறார்கள் vs உண்மை என்ன' "
            "என்ற format-ல் விளக்குங்கள். "
            "பயம் அல்லாமல், அக்கறையுடன் சொல்லுங்கள். "
            "நிறைய பேர் செய்யும் தவறை நாசூக்காக திருத்துங்கள். "
            "Hook: 'இதை தெரியாமல் செய்கிறார்கள்...'"
        ),
    },
    {
        "name": "DIVINE_SIGNS",
        "instruction": (
            "7 அறிகுறிகள் — கடவுள் உங்களுக்கு ஆசி தருகிறார் என்பதற்கான அடையாளங்கள். "
            "கனவுகள், தற்செயல் நிகழ்வுகள், உடல் உணர்வுகள் — இவற்றை விளக்குங்கள். "
            "மிகவும் தனிப்பட்டதாக இருக்கட்டும் — 'இது உங்களுக்கு நடந்திருந்தால்...' என்று. "
            "Hook: 'இந்த அறிகுறி உங்களுக்கு இருந்தால் நீங்கள் அதிர்ஷ்டசாலி...'"
        ),
    },
    {
        "name": "MANTRA_SCIENCE",
        "instruction": (
            "இந்த கடவுளின் மிக முக்கியமான மந்திரத்தை விளக்குங்கள். "
            "ஒவ்வொரு வார்த்தையின் அர்த்தம் சொல்லுங்கள். "
            "எப்போது சொல்ல வேண்டும், எத்தனை முறை, என்ன நேரத்தில் — குறிப்பிட்டு சொல்லுங்கள். "
            "ஒலி அதிர்வின் அறிவியல் கோணமும் சேருங்கள். "
            "கேட்பவர் மந்திரம் சொல்ல ஆரம்பிக்கட்டும் என்ற உந்துதல் கொடுங்கள்."
        ),
    },
]

# =============================================
# ANTI-MONOTONY: CLOSING STYLES (not always same mantra + CTA)
# =============================================
CLOSING_STYLES = [
    "மந்திரம் + ஆசி: இந்த கடவுளின் மந்திரத்துடன் முடியுங்கள். கேட்பவருக்கு ஆசி கொடுங்கள். இயல்பாக subscribe சொல்லுங்கள்.",
    "21 நாள் சவால்: கேட்பவரை ஒரு குறிப்பிட்ட செயலை 21 நாட்கள் செய்ய சவால் விடுங்கள். 'நாளை முதல் செய்யுங்கள்...'",
    "NEXT VIDEO TEASE: ஒரு சுவாரஸ்யமான மர்மத்தை ஆரம்பித்துவிட்டு 'அதன் பதில் அடுத்த video-ல்...' என்று விடுங்கள்.",
    "FUTURE VISION: கேட்பவரின் வாழ்க்கை 6 மாதம் கழித்து எப்படி மாறியிருக்கும் என்று விவரியுங்கள் — இன்று இந்த வழிபாட்டை தொடங்கினால்.",
    "COMMUNITY: comments-ல் 'நீங்கள் எந்த பலனை அனுபவித்தீர்கள்?' என்று கேளுங்கள். பக்தர் சமுதாயத்தின் உணர்வை உருவாக்குங்கள்.",
]

# =============================================
# PROMPTS (FULLY REWRITTEN — ANTI-MONOTONY)
# =============================================

SCRIPT_PROMPT = """நீங்கள் "ஆலய மணி" YouTube சேனலுக்கான ஒரு திறமையான தமிழ் பக்தி கதாசிரியர். நீங்கள் ஒவ்வொரு முறையும் வேறுவிதமாக பேசுகிறீர்கள் — ஒரே மாதிரி இல்லாமல்.

விஷயம்: {topic}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
குரல் / உணர்வு (இந்த கடவுளுக்கு மட்டும்):
{deity_voice}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOOK வகை (இந்த முறை இந்த style பயன்படுத்துங்கள்):
{hook_style}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTENT STRUCTURE (இன்றைய format):
{content_structure}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLOSING STYLE:
{closing_style}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRUCTURE:
1. HOOK (மேலே சொன்னபடி) — 2 வாக்கியங்கள் மட்டும்
2. வணக்கம். ஆலய மணி சேனலுக்கு வரவேற்கிறோம். (1 வரி மட்டும்)
3. CONTENT (மேலே சொன்ன structure-ஐ பின்பற்றுங்கள்)
4. பரிகாரம் பிரிவு — குறிப்பிட்ட steps-உடன் (எப்போது, என்ன, எத்தனை முறை)
5. CLOSING (மேலே சொன்னபடி)

கட்டாய விதிகள்:
- தமிழ் எழுத்தில் மட்டும் எழுதுங்கள். deity பெயர்கள், mantras மட்டும் English.
- ⏱️ நேர வரம்பு: வீடியோ சரியாக 5 நிமிடம்.
- 5 நிமிட வீடியோவுக்கு: சரியாக 1400-1600 தமிழ் வார்த்தைகள் (ஒரு நிமிடத்திற்கு ~160 வார்த்தைகள்).
- ஒவ்வொரு பிரிவும் 5-6 வாக்கியங்கள் — ஆழமாக விவரியுங்கள், ஆனால் நீட்டாதீர்கள்.
- பேச்சு வழக்கில் எழுதுங்கள் — essay இல்லை, conversation.
- எந்த தலைப்பும் வேண்டாம் (1., 2., பலன் 1: போன்றவை கூடாது). தொடர் பேச்சு மட்டும்.
- bullet points, numbering, headers, markdown formatting எதுவும் வேண்டாம்.
- "NO REPETITION" — ஒரு வாக்கியம்கூட முந்தையதை மீண்டும் சொல்ல வேண்டாம்.
- கதையில் கதாபாத்திரங்களுக்கு உண்மையான தமிழ் பெயர்கள் + ஊர் பெயர் கொடுங்கள்
  ("சேலம் கோவிந்தன்", "மதுரை லக்ஷ்மி அக்கா", "கோயம்புத்தூர் ரமேஷ்" போன்றவை)
- REAL STORY STRUCTURE (most viral): 
  Problem → Wrong solution tried → Discovery of spiritual truth → Transformation
  "ஒரு நபர் 3 வருஷமா ஒரு பிரச்சனையால் கஷ்டப்பட்டார்... கோவிலில் ஒரு அர்ச்சகர் சொன்னது..."
- SCIENCE + SPIRITUALITY bridge: Connect every practice to ONE verifiable fact
  (circadian rhythm, frequency, neuroscience, astronomy — not pseudoscience)
- உணர்ச்சியான தருணங்களில் "..." பயன்படுத்துங்கள். வேகமான பகுதிகளில் குறுகிய வாக்கியங்கள்.
- கேட்பவர் "இது என்னக்காகவே செய்யப்பட்டது" என்று உணரவேண்டும்.

YOUTUBE RETENTION RULES:
1. HOOK (0-15s): Start with emotion, question, or surprising fact — NOT deity name.
   Bad: "இன்று நாம் முருகன் பற்றி பேசுவோம்..."
   Bad: "இந்த ரகசியம் யாரும் சொல்லவில்லை..." (overused)
   Good: "ஒரு கேள்வி — நீங்கள் கோயில் போகிறீர்கள், ஆனால் பலன் கிடைக்கிறதா?"
   Good: "108 என்ற எண்ணுக்கு பின்னால் ஒரு astronomical fact இருக்கு — கேளுங்கள்"
   Good: "என் அம்மா 40 வருஷமா இந்த தவறை செய்தார் — நீங்களும் செய்கிறீர்களா?"
   Good: "ஒரு அர்ச்சகர் என்னிடம் சொன்னது: 'இந்த ஒரு விஷயம் மாத்திரம் வீட்டில் செய்யுங்கள்...'"
   
VIRAL COMMENT TRIGGER (every video must end with one):
   "நீங்கள் எந்த கோவிலுக்கு அடிக்கடி போவீர்கள்? கீழே சொல்லுங்கள் 👇"
   "உங்கள் வீட்டில் இந்த பழக்கம் இருக்கா? Comment பண்ணுங்கள்"
   "இந்த தகவல் பிடித்தவர்கள் உங்கள் குடும்பத்தினருக்கு Share பண்ணுங்கள் 🙏"
   Good: "என் அம்மா 40 வருஷமா இந்த தவறை செய்தார் — நீங்களும் செய்கிறீர்களா?"

2. PATTERN INTERRUPT every 30s: "ஆனால் இதை எத்தனை பேர் தெரிஞ்சுக்கிறோம்?"

3. PERSONAL RELEVANCE: Connect to viewer's daily life.
   "நீங்கள் தினமும் செய்யும் இந்த ஒரு செயல்..." makes them stay.

4. SPECIFIC FACTS: Exact mantra counts, specific festival dates, real temple names.
   "சரியாக 108 முறை" > "பல முறை"

5. EMOTIONAL CLOSE: End with hope/comfort, not instruction.
   "இன்று இரவு தூங்கும்முன் இதை ஒரு முறை சொல்லுங்கள் — நாளை வித்தியாசம் தெரியும்"

PAUSE MARKERS — மிக முக்கியம் (இயற்கையான மனித குரல் உணர்வுக்காக):
Script-ல் இந்த markers-ஐ சரியான இடத்தில் வையுங்கள்:
- Hook reveal-க்கு பிறகு:       [PAUSE_LONG]   (நீண்ட இடைவெளி)
- முக்கிய எண்/fact-க்கு பிறகு: [PAUSE_SHORT]  (குறுகிய இடைவெளி)
- கேள்வி கேட்பதற்கு முன்:      [PAUSE_MED]    (நடுத்தர இடைவெளி)
- Section மாறும் போது:         [PAUSE_LONG]

உதாரணம்:
"முருகன் கோவிலில் இந்த ஒரு தவறை செய்தால் — பலன் கிடைக்காது. [PAUSE_LONG]
நம்மில் பலர் தினமும் செய்கிறோம். [PAUSE_SHORT]
உங்களுக்கும் இந்த தவறு நடந்திருக்கிறதா? [PAUSE_MED]"
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

DAILY_TOPIC_PROMPT = """நீங்கள் "ஆலய மணி" YouTube channel-க்கான content strategist.
இந்த channel Tamil devotional content — temple stories, deity legends, spiritual practices — தருகிறது.

TODAY: {date} | {day}
TAMIL FESTIVAL CONTEXT: {festival_context}
RECENTLY USED TOPICS — DO NOT repeat: {recent_topics}

CONTENT CATEGORY ROTATION (6 categories — never same 2 days in a row):
1. DEITY STORY — lesser-known legend, an event from the deity's life nobody talks about
2. TEMPLE MYSTERY — a specific temple with a surprising architectural or scientific fact
3. FESTIVAL MEANING — the real reason behind ONE specific ritual (not generic festival overview)
4. MANTRA SCIENCE — what happens physically/spiritually when you chant THIS mantra
5. SPIRITUAL PRACTICE — step-by-step guide to one daily practice with exact method
6. HISTORY — how a specific Tamil tradition started, its historical origin story

⚠️ BANNED WORDS/SUFFIXES (appear too often — instant reject):
TITLES must NOT contain: "ரகசியம்", "யாரும் அறியாத", "மர்மம்", "அதிசயம்",
"தெரியுமா?", "இதன் பின்னணி என்ன?", "ஏன் என்று தெரியுமா?"
TITLES must NOT end with: "...பின்னணி என்ன?", "...விளக்கம்!", "...ரகசியம்!"

Instead use:
- Specific number: "108 தடவை ஏன்? NASA சொல்வது இதுதான்"
- Named place: "ஆடி அமாவாசை: Rameswaram-ல் 50,000 பேர் ஒரே நேரத்தில் என்ன செய்கிறார்கள்?"
- Contradiction: "கோவிலில் செல்போன் கூடாது என்று யார் சொன்னது? Agama Shastra சொல்வது வேறு"
- Story: "திருப்பதி அர்ச்சகர் 40 வருஷமா ஒரு தவறு செய்தார் — TTD கண்டுபிடித்தது எப்படி?"

GOOD TOPIC ANGLES (use these instead):
- Specific number: "108 முறை ஏன்? அறிவியல் சொல்வது இதுதான்"
- Contradiction: "கோயிலில் செய்யக்கூடாது என்று நினைத்தது — உண்மையில் செய்யலாம்"
- Personal relevance: "இந்த ஒரு தவறை நீங்கள் தினமும் செய்கிறீர்களா?"
- Story hook: "ஒரு விவசாயி கேட்ட கேள்வி — முருகன் கோயில் அர்ச்சகரை திக்கு முக்காட வைத்தது"
- Comparison: "திருப்பதி vs திருவண்ணாமலை — எந்த கோயில் நட்சத்திரக்காரர்களுக்கு சிறந்தது?"

GREAT TOPIC FORMULA = Specific Deity/Temple + Surprising Fact + Viewer Relevance
Examples:
- "பழனி முருகன் ஆண்டி வேடத்தில் ஏன்? இந்த உண்மை கேட்டால் கண்ணீர் வரும்"
- "சிதம்பரம் கோயில் கூரை தங்கத்தால் மூடப்பட்டது ஏன்? இது architecture அல்ல, astrology"
- "காலை 6 மணிக்கு விளக்கு ஏற்றினால் மட்டும் ஏன் பலன்? circadian rhythm சொல்வது இதுதான்"
- "விநாயக சதுர்த்தி அன்று சந்திரனை ஏன் பார்க்கக்கூடாது? நாசா-வின் விளக்கம்"

CHECK today's date {date}:
- Festival in next 7 days? → prioritise with specific ritual angle
- Tamil Nadu news this week? → tie devotional content to current events
- Season/month context? → Aadi=Amman, Karthigai=Shiva, Margazhi=Vishnu, Panguni=Murugan

ENGAGEMENT BOOSTERS (use at least one per topic):
- "இந்த வாரம் மட்டும் செய்யுங்கள்" (time-limited action)
- Real Tamil Nadu temple name (Madurai Meenakshi, Thanjavur Brihadeeswarar, Palani, Sabarimala)
- Connect to something in viewer's daily life (work stress, family problem, health)

Return ONLY valid JSON:
{{
  "topic": "<specific topic WITHOUT ரகசியம்/மர்மம் — use specific fact or angle>",
  "deity": "<சிவன்|முருகன்|விநாயகர்|பெருமாள்|லட்சுமி|ஐயப்பன்|அம்மன்|நடராஜர்|கிருஷ்ணர்|generic>",
  "category_number": <1-6>,
  "hook_angle": "<one surprising specific fact — no generic spirituality>",
  "thumbnail_hook": "<3-4 words max for thumbnail — bold and clickable>",
  "reason": "<why NOT similar to recent topics>"
}}
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
# PEXELS IMAGE FETCHING
# =============================================

# Deity-specific Pexels search queries for high-quality, relevant images
DEITY_PEXELS_QUERIES = {
    "சிவன்":     ["shiva temple india", "shiva lingam", "hindu temple meditation", "tiruvannamalai temple"],
    "முருகன்":   ["murugan temple", "vel spear temple", "palani temple", "kavadi festival"],
    "விநாயகர்": ["ganesh temple", "ganesha statue india", "vinayagar festival", "elephant god temple"],
    "பெருமாள்":  ["vishnu temple india", "tirupati balaji", "perumal temple", "vaishnava temple"],
    "லட்சுமி":   ["lakshmi temple", "lotus flower india", "diwali lamp", "goddess temple gold"],
    "ஐயப்பன்":  ["sabarimala temple", "ayyappan devotees", "kerala temple pilgrims", "makaravilakku"],
    "சூரியன்":   ["sunrise india temple", "surya temple", "konark sun temple", "sunrise prayer"],
}

GENERIC_PEXELS_QUERIES = [
    "hindu temple india",
    "temple bell india",
    "camphor flame temple",
    "indian devotional prayer",
    "flowers temple offering india",
]


def fetch_pexels_images(deity, output_dir, count=5):
    """Fetch high-quality images from Pexels for the given deity."""
    if not PEXELS_API_KEY:
        log("⚠️ PEXELS_API_KEY not set — skipping Pexels fetch")
        return []

    os.makedirs(output_dir, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}
    downloaded = []

    queries = DEITY_PEXELS_QUERIES.get(deity, GENERIC_PEXELS_QUERIES)
    # Shuffle to get variety across runs
    queries = list(queries)
    # Date-based seed so different photos each week
    import datetime as _dt
    week_seed = int(_dt.datetime.now().strftime("%Y%W"))
    _rng = __import__('random').Random(week_seed)
    _rng.shuffle(queries)

    per_query = max(1, count // len(queries) + 1)

    for query in queries:
        if len(downloaded) >= count:
            break
        try:
            url = "https://api.pexels.com/v1/search"
            params = {
                "query": query,
                "per_page": per_query,
                "orientation": "landscape",
                "size": "large",
            }
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                log(f"  Pexels API error {resp.status_code} for query: {query}")
                continue

            data = resp.json()
            photos = data.get("photos", [])
            for photo in photos:
                if len(downloaded) >= count:
                    break
                img_url = photo["src"]["large2x"]  # 2560px wide, high quality
                photo_id = photo["id"]
                fname = os.path.join(output_dir, f"{photo_id}.jpg")
                if os.path.exists(fname):
                    downloaded.append(fname)
                    continue
                try:
                    img_resp = requests.get(img_url, timeout=30, stream=True)
                    if img_resp.status_code == 200:
                        with open(fname, "wb") as f:
                            for chunk in img_resp.iter_content(8192):
                                f.write(chunk)
                        downloaded.append(fname)
                        log(f"  📸 Downloaded: {os.path.basename(fname)} ({query})")
                except Exception as e:
                    log(f"  ⚠️ Image download failed: {e}")

        except Exception as e:
            log(f"  ⚠️ Pexels query failed ({query}): {e}")

    log(f"  ✅ Pexels: {len(downloaded)} images fetched for {deity or 'generic'}")
    return downloaded


def get_images_for_deity(deity, day_or_name):
    """
    Returns a list of image paths for video creation.
    Priority: Pexels fetch → local images dir → fallback placeholder.
    """
    pexels_dir = os.path.join(PEXELS_DIR, day_or_name)
    images = fetch_pexels_images(deity, pexels_dir, count=6)

    if images:
        return images

    # Fallback: scan local images/ directory
    if os.path.isdir("images"):
        exts = (".png", ".jpg", ".jpeg", ".webp")
        local = [os.path.join("images", f) for f in sorted(os.listdir("images"))
                 if f.lower().endswith(exts)]
        if local:
            log(f"  📁 Using {len(local)} local images from images/")
            return local[:6]

    # Fallback: single IMAGE_FILE
    if os.path.exists(IMAGE_FILE):
        return [IMAGE_FILE]

    return []


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
    try:
        return int(float(r.stdout.strip()))
    except (ValueError, AttributeError):
        return 0


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
    if not PEXELS_API_KEY:
        print("WARNING: PEXELS_API_KEY not set — will use local images only")
        print("  Get free key: https://www.pexels.com/api/")
    ensure_images()
    ensure_bgm()  # generic fallback


# Deity-specific BGM tone frequencies (temple bell harmonics)
DEITY_BGM_FREQ = {
    "சிவன்":    ("136.1", "272.2"),   # OM frequency — deep meditative
    "முருகன்":  ("174.0", "348.0"),   # energetic, warrior tone
    "விநாயகர்": ("528.0", "264.0"),   # transformation, warm
    "பெருமாள்": ("432.0", "216.0"),   # devotional bhakti tone
    "லட்சுமி":  ("417.0", "208.5"),   # abundance, graceful
    "ஐயப்பன்":  ("396.0", "198.0"),   # liberation, austere
    "சூரியன்":   ("285.0", "570.0"),   # sunrise energy
    "நடராஜர்":  ("136.1", "272.2"),   # same as Shiva — cosmic dance OM frequency
    "கிருஷ்ணர்": ("528.0", "264.0"),   # love/devotion frequency
    "அம்மன்":   ("417.0", "208.5"),   # power/protection
    "":          ("174.0", "348.0"),   # generic devotional
}

def ensure_bgm(deity=""):
    """Generate deity-specific copyright-free BGM if not found."""
    bgm_path = f"bgm_{deity or 'generic'}.mp3" if deity else BGM_FILE
    if os.path.exists(bgm_path):
        return bgm_path
    if deity and os.path.exists(BGM_FILE):
        # Try to generate deity-specific; fallback to generic
        pass
    log(f"🎵 Generating devotional BGM for {deity or 'generic'}...")
    freq1, freq2 = DEITY_BGM_FREQ.get(deity, DEITY_BGM_FREQ[""])
    # Sine wave at deity frequency + harmonic overtone + subtle pink noise bed
    r = run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency={freq1}:duration=360",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq2}:duration=360",
        "-f", "lavfi",
        "-i", "anoisesrc=d=360:c=pink:r=44100:a=0.008",
        "-filter_complex",
        "[0:a]volume=0.18,afade=t=in:st=0:d=5,afade=t=out:st=595:d=5[s1];"
        "[1:a]volume=0.07,afade=t=in:st=0:d=8[s2];"
        "[2:a]lowpass=f=400,volume=0.12[noise];"
        "[s1][s2][noise]amix=inputs=3:duration=first[out]",
        "-map", "[out]",
        "-ar", "44100", "-ac", "2",
        bgm_path
    ], timeout=60)
    if r.returncode == 0:
        log(f"  ✅ BGM: {bgm_path} ({freq1}Hz + {freq2}Hz harmonics)")
        return bgm_path
    else:
        # Fallback: simple pink noise bed
        run(["ffmpeg", "-y", "-f", "lavfi",
             "-i", "anoisesrc=d=360:c=pink:r=44100:a=0.015",
             "-af", "lowpass=f=500,volume=0.2", bgm_path], timeout=60)
        return bgm_path if os.path.exists(bgm_path) else BGM_FILE


def ensure_dirs():
    for d in [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR, PEXELS_DIR]:
        os.makedirs(d, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# LLM ROUTER — Groq reserved for scripts only
# Gemini Flash handles everything else (topic, metadata, MCQ etc)
# This keeps Groq usage under 15K tokens/day well within 100K limit
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# LLM ROUTER — Groq for scripts only, Gemini for everything else
# Keeps Groq daily usage ~26K/100K tokens (was 96K+)
# ═══════════════════════════════════════════════════════════════

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



USED_TOPICS_FILE = "used_topics.txt"


def load_recent_topics(n=20):
    """Load recently used topics — persists across GitHub Actions via git."""
    topics = []
    if os.path.exists(USED_TOPICS_FILE):
        with open(USED_TOPICS_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        topics = lines[-n:]
    return topics


def deduplicate_topic(topic):
    """Hard check: if topic was already used, append date to differentiate."""
    used = load_recent_topics(60)
    if topic in used:
        date_str = datetime.datetime.now().strftime("%d-%b-%Y")
        deduped = f"{topic} — {date_str}"
        log(f"  🚫 Topic already used → adjusted to: {deduped}")
        return deduped
    return topic


def save_used_topic(topic):
    """Append topic to git-committed file so future runs avoid repeats."""
    try:
        existing = []
        if os.path.exists(USED_TOPICS_FILE):
            with open(USED_TOPICS_FILE, encoding="utf-8") as f:
                existing = [l.strip() for l in f.readlines() if l.strip()]
        if topic not in existing:
            existing.append(topic)
        existing = existing[-60:]
        with open(USED_TOPICS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(existing) + "\n")
        run(["git", "config", "user.email", "bot@aalayamani.com"])
        run(["git", "config", "user.name",  "Aalaya Mani Bot"])
        run(["git", "add", USED_TOPICS_FILE])
        r = run(["git", "commit", "-m", f"chore: log topic [{topic[:40]}]"])
        if r.returncode == 0:
            run(["git", "push"])
            log("  ✅ Topic history committed to git")
        else:
            log("  ℹ️  Topic already committed")
    except Exception as e:
        log(f"  ⚠️ Could not save topic: {e}")

def get_trends_data():
    """Fetch trending signals from multiple sources."""
    trends = ""
    try: trends += fetch_google_trends() or ""
    except: pass
    try: trends += fetch_youtube_trending() or ""
    except: pass
    try: trends += fetch_god_temple_news() or ""
    except: pass
    if not trends.strip():
        sample = random.sample(EVERGREEN_VIRAL_TOPICS, 5)
        trends = "No live data. Evergreen viral topics:\n" + "\n".join(f"- {t}" for t in sample)
    return trends


def discover_daily_config(day=None):
    """
    LLM decides BOTH deity AND topic based on:
    - Day of week (default deity signal)
    - Tamil month + upcoming festivals
    - Trending signals
    Returns a full config dict ready for process_day_with_config().
    """
    log("🧠 LLM choosing today's best deity + topic...")
    now = datetime.datetime.now()
    day_name = (day or now.strftime("%A")).capitalize()
    day_key  = day_name.lower()

    month_num = now.month
    tamil_month, _ = TAMIL_MONTHS.get(month_num, ("", ""))
    default = DAY_DEITY_MAP.get(day_key, DAY_DEITY_MAP["sunday"])
    festivals   = get_upcoming_festivals()
    today_fest  = get_festivals_today()
    trends_data = get_trends_data()

    recent_topics = load_recent_topics(10)

    # Compute festival context here (not in main)
    import datetime as _dt
    _today = _dt.date.today()
    festival_ctx = "Regular day"
    _tamil_festivals = {
        (1,14):"Pongal — most important Tamil harvest festival",
        (4,13):"Tamil New Year (Puthandu)",
        (6,1):"Aani month — special for Nataraja",
        (7,1):"Aadi month — special for Amman",
        (9,1):"Purattasi — special for Perumal",
        (10,1):"Aippasi — Navarathri season",
        (11,1):"Karthigai Deepam month",
        (12,1):"Margazhi month — special Thiruvembavai",
    }
    for (_m,_d), _ctx in _tamil_festivals.items():
        if _today.month == _m and abs(_today.day - _d) <= 7:
            festival_ctx = _ctx; break

    prompt = DAILY_TOPIC_PROMPT.format(
        festival_context=festival_ctx,
        date=now.strftime("%Y-%m-%d"),
        day=day_name,
        recent_topics=", ".join(recent_topics[-5:]) if recent_topics else "None yet",
    )
    if recent_topics:
        prompt += (
            f"\n\nRECENTLY USED TOPICS (avoid repeating): "
            + ", ".join(recent_topics[-5:])
        )

    raw = call_llm(prompt, prefer="gemini", max_tokens=1000)
    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"): clean = clean[4:]
        data = json.loads(clean.strip())
        deity    = data.get("deity", default["deity"])
        deity_en = data.get("deity_en", default["deity_en"])
        topic    = deduplicate_topic(data.get("topic", ""))
        reason   = data.get("reason", "")
        log(f"  🎯 Deity: {deity} ({deity_en})")
        log(f"  📌 Topic: {topic}")
        log(f"  💡 Reason: {reason}")
    except Exception as e:
        log(f"  ⚠️ JSON parse failed ({e}) — using day default")
        deity    = default["deity"]
        deity_en = default["deity_en"]
        topic    = deduplicate_topic(f"{deity} வழிபாடு — இன்றைய சிறப்பு பலன்கள்")

    emoji    = DEITY_EMOJI_MAP.get(deity, "🙏")
    hashtags = DEITY_HASHTAG_MAP.get(deity, DEITY_HASHTAG_MAP[""])

    return {
        "deity":    deity,
        "deity_en": deity_en,
        "topic":    topic,
        "emoji":    emoji,
        "hashtags": hashtags,
        "day_key":  day_key,
    }


def discover_trending_topic():
    """Legacy wrapper — returns just the topic string."""
    config = discover_daily_config()
    return config.get("topic", "")


# =============================================
# SCRIPT & METADATA GENERATION
# =============================================


HOOK_USAGE_FILE   = "hook_usage.json"
FORMAT_USAGE_FILE = "format_usage.json"


def load_usage(fname):
    if os.path.exists(fname):
        with open(fname) as f: return json.load(f)
    return {}


def save_usage(fname, data):
    with open(fname, "w") as f: json.dump(data, f, indent=2)
    try:
        run(["git", "add", fname])
        run(["git", "commit", "-m", f"chore: usage update {os.path.basename(fname)}"])
        run(["git", "push"])
    except: pass


def pick_least_used(options, usage_dict, key_fn=None):
    """Pick option used least recently from usage history."""
    scored = []
    for opt in options:
        key = key_fn(opt) if key_fn else str(opt)
        scored.append((usage_dict.get(key, 0), opt))
    scored.sort(key=lambda x: x[0])
    chosen = scored[0][1]
    key = key_fn(chosen) if key_fn else str(chosen)
    usage_dict[key] = usage_dict.get(key, 0) + 1
    return chosen, usage_dict

def generate_script(topic, deity=""):
    t0 = time.time()

    # Pick anti-monotony elements randomly
    deity_voice = DEITY_VOICE.get(deity, (
        "இயல்பான, அன்பான, பக்தி மிகுந்த குரலில் பேசுங்கள். "
        "கேட்பவர் ஒரு நேசமான நண்பரிடம் பேசுவதுபோல் உணரட்டும்."
    ))
    hook_usage   = load_usage(HOOK_USAGE_FILE)
    format_usage = load_usage(FORMAT_USAGE_FILE)
    hook_style,   hook_usage   = pick_least_used(HOOK_STYLES,     hook_usage,   lambda x: x.split(':')[0])
    content_struct, format_usage = pick_least_used(CONTENT_STRUCTURES, format_usage, lambda x: x['name'])
    closing_style = random.choice(CLOSING_STYLES)
    save_usage(HOOK_USAGE_FILE,   hook_usage)
    save_usage(FORMAT_USAGE_FILE, format_usage)

    log(f"  🎭 Deity voice: {deity or 'generic'}")
    log(f"  🪝 Hook style: {hook_style.split(':')[0]}")
    log(f"  📋 Content structure: {content_struct['name']}")
    log(f"  🎬 Closing style: {closing_style.split(':')[0]}")

    prompt = SCRIPT_PROMPT.format(
        topic=topic,
        deity_voice=deity_voice,
        hook_style=hook_style,
        content_structure=content_struct["instruction"],
        closing_style=closing_style,
    )

    def build_prompt(attempt=0):
        note = ""
        if attempt > 0:
            note = (
                f"\n\nமுக்கியம் — ATTEMPT {attempt+1}: முந்தைய பதில் மிகவும் குறுகியது. "
                "சரியாக 1400-1600 வார்த்தைகள் எழுதுங்கள் (5 நிமிட வீடியோ). "
                "ஒவ்வொரு பிரிவும் 5-6 முழுமையான வாக்கியங்கள். குறுக்கு வழியில்லை."
            )
        return SCRIPT_PROMPT.format(
            topic=topic,
            deity_voice=deity_voice,
            hook_style=hook_style,
            content_structure=content_struct["instruction"],
            closing_style=closing_style,
        ) + note

    text = ""
    for attempt in range(3):
        resp = call_llm(build_prompt(attempt))
        chars = len(resp.strip())
        log(f"  Attempt {attempt+1}: {chars} chars")
        if chars >= TARGET_MIN:
            text = resp.strip(); break
        text = resp.strip()
        if attempt < 2:
            log(f"  Too short ({chars} < {TARGET_MIN}) — retrying in 15s..."); time.sleep(15)

    # Hard cap: trim at sentence boundary if over TARGET_MAX
    if len(text) > TARGET_MAX:
        log(f"  Script too long ({len(text)} chars) — trimming to 5 min...")
        trimmed = text[:TARGET_MAX]
        # Find last sentence end to avoid mid-sentence cut
        for punct in [".\n", ". ", "\n\n"]:
            idx = trimmed.rfind(punct)
            if idx > TARGET_MIN:
                trimmed = trimmed[:idx+1]
                break
        text = trimmed
        log(f"  Trimmed to {len(text)} chars")

    if len(text.strip()) < 100:
        log("  ❌ Script generation failed — all attempts returned empty")
        return ""
    log(f"  Script generated ({len(text)} chars) in {time.time()-t0:.0f}s")
    return text


COMBINED_META_PROMPT = """Generate YouTube metadata for a Tamil devotional video. Return ONLY valid JSON — no markdown, no explanation.

Topic: {topic}
Deity: {deity} ({deity_en})
Emoji: {emoji}
Hashtags: {hashtags}
Year: {year}

Return this exact JSON structure:
{{
  "title": "[Tamil title with deity + key benefit] {emoji} [hook] | [English equivalent] | ஆலய மணி",
  "description": "[Full description in Tamil+English, under 3000 chars, with benefits list, CTAs, hashtags]",
  "tags": "[comma separated 20-25 Tamil+English tags]",
  "pinned_comment": "[Tamil pinned comment under 500 chars asking which benefit they need + mantra]"
}}

Title example: செவ்வாய் முருகன் விரதம் 7 பலன்கள் 🔱 வாழ்க்கையே மாறும் | Tuesday Murugan | ஆலய மணி

MONETISATION-FOCUSED SEO RULES:

TITLE (CTR optimisation for devotional content):
- Include deity name + specific benefit or upcoming festival
- "முருகன் வழிபாடு" is generic — loses to specific titles
- "இந்த 5 நிமிட முருகன் பிரார்த்தனை தினமும் செய்யுங்கள் — கஷ்டம் தீரும்" wins
- Festival urgency: "ஆனி திருமஞ்சனம் நாளை — இந்த puja செய்யுங்கள்"
- Power words: ரகசியம், உண்மை, தினமும், இப்பவே, தெரியாத

DESCRIPTION LINE 1: The devotional hook or viewer benefit (search snippet)
DESCRIPTION LINE 2: "Learn about [deity/festival] in Tamil | ஆலய மணி"

TAGS (30 total — SEO priority order):
Tier 1 (5 high-volume English): "murugan songs", "shiva songs tamil", "devotional songs tamil", "tamil bhakti", "temple worship tamil"
Tier 2 (10 Tamil): deity name + festival + day name (செவ்வாய் கிழமை etc)
Tier 3 (10 long-tail): "how to do [ritual] at home tamil", "[deity] pooja vidhi tamil", "[temple name] history tamil", "[festival] 2026 tamil"
Tier 4 (5 trending): current festival/event if applicable

CHAPTERS (MANDATORY — YouTube shows these in search as clickable sections):
Add in description after line 2:
0:00 🔔 ஆரம்பம்
0:30 📖 [Deity] கதை / வரலாறு
2:00 🙏 வழிபாடு முறை
3:30 ⭐ பலன்கள் & அனுபவங்கள்
4:30 🎯 பரிகாரம் — Step by Step
5:30 🔔 Subscribe & Share
Generate based on actual script structure.

DESCRIPTION TEMPLATE:
Line 1: Tamil hook (same urgency as video opening)
Line 2: "Learn about [deity/festival] in Tamil | ஆலய மணி"
[CHAPTERS block]
🙏 இந்த video-ல் நீங்கள் தெரிந்துகொள்வது:
• [Point 1]
• [Point 2]
• [Point 3]
📿 [Deity] மந்திரம்: [main mantra]
🔔 Subscribe: @AalayaMani | 👍 Like | 🔔 Bell icon
📱 Share பண்ணுங்கள் — ஒரு நண்பருக்கு உதவலாம்
[hashtags]

ENGAGEMENT HOOKS (add these naturally in script):
- At 30s: Pattern interrupt — "இந்த ஒரு ரகசியம் — அர்ச்சகர்கள்கூட வெளியே சொல்வதில்லை..."
- At 60% video: "இது useful-ஆ இருந்தால் — subscribe பண்ணுங்கள். தினமும் இதுமாதிரி content வருது."
- At end: 2-choice comment bait — "நீங்கள் முருகனை வழிபடுவீர்களா — செவ்வாய் கிழமையா, தினமுமா? 👇"
- Share trigger: "இந்த video-ஐ உங்கள் குடும்பத்தினருக்கு share பண்ணுங்கள் — ஒரு good deed."
"""

def _build_description(config, data):
    """Build clean YouTube description from config + partial metadata."""
    deity    = config.get("deity", "")
    deity_en = config.get("deity_en", "")
    topic    = config.get("topic", "")
    hashtags = config.get("hashtags", "")
    year     = datetime.datetime.now().year
    title    = data.get("title", topic)

    return (
        f"{topic}\n\n"
        f"🙏 {deity} ({deity_en}) வழிபாடு | Tamil Devotional\n\n"
        f"இந்த video-வில்:\n"
        f"✨ {topic}\n"
        f"🔔 Subscribe செய்யுங்கள் | Like & Share பண்ணுங்கள்\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 ஆலய மணி | Tamil Devotional Channel | {year}\n"
        f"Every day: Deity stories, temple mysteries, spiritual wisdom\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hashtags}"
    )[:4900]


def _build_fallback_metadata(config, year):
    """Build complete metadata without LLM when JSON parse fails."""
    deity    = config.get("deity", "தெய்வம்")
    deity_en = config.get("deity_en", "God")
    topic    = config.get("topic", "")
    emoji    = config.get("emoji", "🙏")
    hashtags = config.get("hashtags", "")

    title = f"{topic} {emoji} | {deity_en} | ஆலய மணி"[:100]

    description = (
        f"{topic}\n\n"
        f"🙏 {deity} ({deity_en}) வழிபாடு | Tamil Devotional {year}\n\n"
        f"✨ {topic} பற்றிய முழு விளக்கம்\n"
        f"🔔 Subscribe: @aalayamani\n"
        f"👍 Like | 📤 Share | 💬 Comment\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ஆலய மணி — Daily Tamil Devotional Videos\n"
        f"Temple stories | Deity legends | Spiritual wisdom\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hashtags}"
    )[:4900]

    tags = (
        f"{deity}, {deity_en}, tamil devotional {year}, aalaya mani, "
        f"tamil god songs, temple stories tamil, {deity_en.lower()} songs, "
        f"devotional tamil, spiritual tamil, ஆலய மணி"
    )

    pinned = (
        f"🙏 {deity} அருள் உங்களுக்கு கிடைக்கட்டும்! "
        f"உங்களுக்கு என்ன வேண்டும்? Comment-ல் சொல்லுங்கள் 👇 "
        f"Subscribe செய்து Bell icon அழுத்துங்கள் 🔔"
    )

    return {
        "title":         title,
        "description":   description,
        "tags":          tags,
        "pinned_comment": pinned,
    }


def generate_metadata(config):
    t0 = time.time()
    year = datetime.datetime.now().year
    prompt = COMBINED_META_PROMPT.format(**config, year=year)
    log("  Generating all metadata in one call...")
    raw = call_llm_groq(prompt, max_retries=3)
    try:
        # Strip markdown fences
        clean = raw.strip()
        for fence in ["```json", "```JSON", "```"]:
            if clean.startswith(fence):
                clean = clean[len(fence):]
                break
        if "```" in clean:
            clean = clean[:clean.rfind("```")]
        clean = clean.strip()

        # Handle case where LLM wraps in outer object
        if not clean.startswith("{"):
            # Try to find JSON object
            import re as _re
            m = _re.search(r'{[\s\S]+}', clean)
            if m: clean = m.group(0)

        data = json.loads(clean)

        # Validate — ensure description is actual text, not JSON
        desc = data.get("description", "")
        if desc.strip().startswith("{") or desc.strip().startswith("["):
            # Description is raw JSON — extract from the parsed data instead
            log("  ⚠️ Description was JSON — rebuilding...")
            data["description"] = _build_description(config, data)

        log(f"  Metadata complete ({time.time()-t0:.0f}s)")
        return data

    except Exception as e:
        log(f"  ⚠️ JSON parse failed ({e}) — using fallback metadata")

    # Fallback: build clean metadata without LLM
    log(f"  Metadata complete ({time.time()-t0:.0f}s)")
    return _build_fallback_metadata(config, year)


# =============================================
# VIDEO CREATION — Ken Burns + Slideshow
# =============================================

def find_images(image_src):
    """Find images from file, comma-separated list, directory, or glob."""
    if not image_src:
        return []
    exts = (".png", ".jpg", ".jpeg", ".webp")

    if isinstance(image_src, list):
        return [f for f in image_src if os.path.exists(f)]

    if os.path.isdir(image_src):
        found = []
        for f in sorted(os.listdir(image_src)):
            if f.lower().endswith(exts):
                found.append(os.path.join(image_src, f))
        return found[:10]

    if "*" in image_src or "?" in image_src:
        import glob as _glob
        found = sorted(_glob.glob(image_src))
        return [f for f in found if f.lower().endswith(exts)][:10]

    if "," in image_src:
        parts = [p.strip() for p in image_src.split(",")]
        return [p for p in parts if os.path.exists(p) and p.lower().endswith(exts)]

    if os.path.exists(image_src) and image_src.lower().endswith(exts):
        return [image_src]
    return [image_src]


# Ken Burns motion presets — varied zoom + pan directions
KB_PRESETS = [
    # (zoom_expr, x_expr, y_expr, label)
    # zoom in, pan right
    ("min(1.0+0.0008*on,1.20)", "iw/2-(iw/zoom/2)+on*0.3", "ih/2-(ih/zoom/2)", "zoom-in pan-right"),
    # zoom in, pan left
    ("min(1.0+0.0008*on,1.20)", "iw/2-(iw/zoom/2)-on*0.3", "ih/2-(ih/zoom/2)", "zoom-in pan-left"),
    # zoom out, center
    ("max(1.25-0.0008*on,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", "zoom-out center"),
    # zoom in, pan up
    ("min(1.0+0.0008*on,1.15)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)+on*0.2", "zoom-in pan-up"),
    # zoom out, pan right
    ("max(1.20-0.0007*on,1.0)", "iw/2-(iw/zoom/2)+on*0.25", "ih/2-(ih/zoom/2)", "zoom-out pan-right"),
    # static slight zoom — for dramatic still shots
    ("min(1.0+0.0004*on,1.08)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", "slow-zoom"),
]

XFADE_TRANSITIONS = ["fade", "dissolve", "wipeleft", "wiperight", "slideleft", "fadeblack"]


def build_video_filter(images, total_frames, fps=25, seed=None):
    """
    Build ffmpeg filter_complex: varied Ken Burns (zoom+pan) + rotating transitions.
    Returns (num_inputs, filter_string, output_label).
    """
    import random as _rnd
    rng = _rnd.Random(seed)   # seeded for reproducibility per video

    num = len(images)
    seg_frames = total_frames // num

    filters = []
    for i in range(num):
        preset = KB_PRESETS[i % len(KB_PRESETS)]   # cycle through presets
        z_expr, x_expr, y_expr, label = preset
        # Slightly vary speed per image for organic feel
        speed_var = rng.uniform(0.85, 1.15)
        adj_frames = int(seg_frames * speed_var)
        adj_frames = max(adj_frames, fps * 2)  # minimum 2s per image
        log(f"    Image {i+1}: {label}")
        filters.append(
            f"[{i}:v]loop=loop=-1:size=1:start=0,"
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={adj_frames}:fps={fps}:s=1920x1080,"
            f"trim=0:{adj_frames / fps:.2f},setpts=PTS-STARTPTS[v{i}]"
        )

    prev = "v0"
    xfade_dur = 1.0
    for i in range(1, num):
        transition = XFADE_TRANSITIONS[i % len(XFADE_TRANSITIONS)]
        offset = i * (seg_frames / fps) - xfade_dur
        label = f"x{i}"
        filters.append(
            f"[{prev}][v{i}]xfade=transition={transition}:duration={xfade_dur}:offset={max(0.5,offset):.2f}[{label}]"
        )
        prev = label

    return num, ";".join(filters), prev


def make_intro_bell(output_path, duration=2.5):
    """Generate a temple bell ding — padded to exact duration."""
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=880:duration={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency=1320:duration={duration}",
        "-filter_complex",
        f"[0:a]volume=0.5,afade=t=in:st=0:d=0.2,afade=t=out:st={duration-0.5}:d=0.5[b1];"
        f"[1:a]volume=0.3,afade=t=out:st={duration-0.5}:d=0.5[b2];"
        f"[b1][b2]amix=inputs=2:duration=longest,"
        f"apad=pad_dur={duration}[bell]",
        "-map", "[bell]", "-ar", "44100", "-ac", "2",
        "-t", str(duration), output_path
    ], timeout=15)
    return os.path.exists(output_path)


def build_text_overlay(deity_name, deity_en, title_short, duration):
    """
    Build drawtext filter for video overlay:
    - Channel name top-left always visible
    - Deity name fades in at 0s, stays for 4s
    - Title text at bottom for first 8s
    """
    safe = lambda s: s.replace("'", "").replace(":", "-").replace('"', "")
    channel = safe("ஆலய மணி")
    deity   = safe(deity_name) if deity_name else safe(deity_en)
    title   = safe(title_short[:50]) if title_short else ""

    overlays = []

    # Channel name — top left, small, always visible
    overlays.append(
        f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf:text='{channel}':fontsize=28:fontcolor=white@0.75:"
        f"x=30:y=30:shadowcolor=black@0.8:shadowx=2:shadowy=2"
    )

    # Deity name — center top, large, fade in 0→2s, hold 4s, fade out
    if deity:
        overlays.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf:text='{deity}':fontsize=52:fontcolor=gold@1.0:"
            f"x=(w-text_w)/2:y=60:"
            f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
            f"alpha='if(lt(t,0.5),0,if(lt(t,2),(t-0.5)/1.5,if(lt(t,5),1,if(lt(t,6),(6-t),0))))'"
        )

    # Short title — bottom, fade in at 1s, hold 7s
    if title:
        overlays.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf:text='{title}':fontsize=34:fontcolor=white@0.9:"
            f"x=(w-text_w)/2:y=h-80:"
            f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
            f"alpha='if(lt(t,1),0,if(lt(t,2.5),(t-1)/1.5,if(lt(t,8),1,if(lt(t,9),(9-t),0))))'"
        )

    return ",".join(overlays)


def inject_pauses(text):
    """Convert [PAUSE_X] markers to natural ellipsis pauses for edge-tts."""
    text = text.replace("[PAUSE_LONG]",  "  ...  ")
    text = text.replace("[PAUSE_MED]",   " ... ")
    text = text.replace("[PAUSE_SHORT]", " .. ")
    return text


def create_video(script_text, images_input, output_name, bgm, bgm_vol=0.18,
                 deity_name="", deity_en="", title_short=""):
    ensure_dirs()

    script_file = f"/tmp/{output_name}_script.txt"
    voice_file  = f"/tmp/{output_name}_voice.mp3"
    human_file  = f"/tmp/{output_name}_human.mp3"
    bell_file   = f"/tmp/{output_name}_bell.mp3"
    mixed_file  = f"/tmp/{output_name}_mixed.mp3"
    video_raw   = f"/tmp/{output_name}_raw.mp4"
    video_file  = f"{OUTPUT_DIR}/{output_name}_video.mp4"
    short_file  = f"{SHORTS_DIR}/{output_name}_short.mp4"

    script_text = inject_pauses(script_text)  # humanise: add natural breath pauses
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_text)

    log("🔊 Step 1/6 Voice (edge-tts)...")
    t0 = time.time()
    try:
        r = run(["edge-tts", "--file", script_file, "--voice", "ta-IN-PallaviNeural",
                 "--rate=-11%", "--pitch=+1Hz", "--write-media", voice_file],
                timeout=600)
    except subprocess.TimeoutExpired:
        log("❌ edge-tts timed out (>600s)"); return None
    if r.returncode != 0:
        log(f"❌ Voice error: {r.stderr[-200:]}"); return None
    dur = get_dur(voice_file)
    log(f"  Voice: {dur}s ({time.time()-t0:.0f}s generation)")

    log("🎧 Step 2/6 Humanizing voice...")
    r = run(["ffmpeg", "-y", "-i", voice_file, "-af", FEMALE_HUMANIZE, human_file])
    if r.returncode != 0:
        log("  ⚠️ Humanization failed, using raw voice")
        shutil.copy(voice_file, human_file)
    else:
        log("  ✅ Voice humanized (warm + temple reverb)")
    dur = get_dur(human_file)

    # Generate intro bell
    make_intro_bell(bell_file)

    if os.path.exists(bgm):
        log("🎵 Step 3/6 BGM + bell mixing...")
        fo  = max(0, dur - 3)
        bfo = max(0, dur - 4)
        has_bell = os.path.exists(bell_file)
        if has_bell:
            fc = (
                "[0:a]adelay=2500|2500,volume=1.0,"   # voice starts after bell (2.5s)
                "afade=t=in:st=2.5:d=1.5,"
                "afade=t=out:st={fo}:d=3[voice];"
                "[1:a]volume={bv},"
                "afade=t=in:st=0:d=4,afade=t=out:st={bfo}:d=4[bg];"
                "[2:a]volume=0.7,afade=t=out:st=2:d=0.5[bell];"
                "[voice][bg][bell]amix=inputs=3:duration=first:dropout_transition=3[out]"
            ).format(fo=fo+2.5, bv=bgm_vol, bfo=bfo+2.5)
            run(["ffmpeg", "-y", "-i", human_file, "-i", bgm, "-i", bell_file,
                 "-filter_complex", fc, "-map", "[out]", "-ac", "2", "-c:a", "aac", "-b:a", "192k", mixed_file])
        else:
            fc = (
                "[0:a]volume=1.0,afade=t=in:st=0:d=2,afade=t=out:st={fo}:d=3[voice];"
                "[1:a]volume={bv},afade=t=in:st=0:d=4,afade=t=out:st={bfo}:d=4[bg];"
                "[voice][bg]amix=inputs=2:duration=first:dropout_transition=3[out]"
            ).format(fo=fo, bv=bgm_vol, bfo=bfo)
            run(["ffmpeg", "-y", "-i", human_file, "-i", bgm,
                 "-filter_complex", fc, "-map", "[out]", "-ac", "2", "-c:a", "aac", "-b:a", "192k", mixed_file])
        audio = mixed_file if os.path.exists(mixed_file) else human_file
    else:
        audio = human_file

    total_dur = get_dur(audio)

    log("🎬 Step 4/6 Video (Ken Burns + slideshow)...")
    t0 = time.time()

    # Resolve images
    if isinstance(images_input, list):
        images = [f for f in images_input if os.path.exists(f)]
    else:
        images = find_images(images_input)

    if not images:
        log(f"❌ No images found")
        return None

    log(f"🖼️ Using {len(images)} images: {[os.path.basename(i)[:20] for i in images]}")

    fps = 25
    # Use unique seed per video for reproducible but varied Ken Burns
    seed = int(hashlib.md5(output_name.encode()).hexdigest()[:8], 16)
    total_frames = max(int(total_dur * fps), 25)
    num_inputs, vfilter, vlabel = build_video_filter(images, total_frames, fps, seed=seed)

    cmd = ["ffmpeg", "-y"]
    for img in images:
        cmd.extend(["-loop", "1", "-t", str(total_dur + 2), "-i", img])
    cmd.extend(["-i", audio, "-filter_complex", vfilter,
                "-map", f"[{vlabel}]", "-map", str(num_inputs) + ":a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-ar", "44100", "-ac", "2",
                "-avoid_negative_ts", "make_zero", video_raw])

    log(f"  Encoding {num_inputs} images × {total_dur}s @ {fps}fps...")
    r = run(cmd, timeout=600)
    if r.returncode != 0:
        log(f"⚠️ Slideshow failed, falling back to single image...")
        fallback_img = images[0]
        r2 = run(["ffmpeg", "-y", "-loop", "1", "-i", fallback_img, "-i", audio,
                  "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                  "-pix_fmt", "yuv420p", "-c:a", "aac",
                  "-ar", "44100", "-ac", "2", video_raw], timeout=600)
        if r2.returncode != 0:
            log(f"❌ Video error: {r2.stderr[-200:]}")
            return None

    log("✍️ Step 5/6 Text overlays...")
    text_filter = build_text_overlay(deity_name, deity_en, title_short, total_dur)
    r3 = run(["ffmpeg", "-y", "-i", video_raw,
               "-vf", text_filter,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
               "-c:a", "copy", video_file], timeout=300)
    if r3.returncode != 0:
        log("  ⚠️ Text overlay failed — using raw video")
        shutil.copy(video_raw, video_file)
    else:
        log("  ✅ Overlays: channel name + deity + title")

    mb = os.path.getsize(video_file) / (1024 * 1024)
    log(f"  Video: {mb:.1f}MB ({time.time()-t0:.0f}s encode)")

    log("📱 Step 6/6 Shorts (reframed vertical)...")
    _vf = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=25:5[blurred];"
        "[fg]scale=1080:607,"
        "pad=1080:1920:0:(1920-607)/2:black[padded];"
        "[blurred][padded]overlay=0:(H-h)/2"
    )
    _r = run(["ffmpeg", "-y", "-i", video_file, "-ss", "0", "-t", "55",
              "-vf", _vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
              "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
              short_file], timeout=180)
    if _r.returncode != 0:
        run(["ffmpeg", "-y", "-i", video_file, "-ss", "0", "-t", "55",
             "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
                    "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-c:a", "aac", short_file], timeout=180)

    for f in [script_file, voice_file, human_file, bell_file, mixed_file, video_raw]:
        try:
            if os.path.exists(f): os.remove(f)
        except: pass

    return video_file


# =============================================
# YOUTUBE UPLOAD
# =============================================


# ═══════════════════════════════════════════════════════════════
# ENGAGEMENT FEATURES (ported from நிதி நீதி தமிழ் v1.7)
# ═══════════════════════════════════════════════════════════════

MCQ_PROMPT = """Generate a devotional quiz question for "ஆலய மணி" channel.
Topic: {topic}
Deity: {deity}
Script excerpt: {key_fact}
Rules: Tamil only, 4 options, 3 lines max, end with "சரியான answer comment பண்ணுங்கள் 👇"
Format: [Question]?\nA) [opt]  B) [opt]\nC) [opt]  D) [opt]\nசரியான answer comment பண்ணுங்கள் 👇
Return ONLY quiz text."""


def generate_mcq(topic, script_text, deity=""):
    try:
        raw = call_llm(MCQ_PROMPT.format(
            topic=topic, deity=deity or "கடவுள்",
            key_fact=script_text[:400]))
        if "A)" in raw and "comment" in raw.lower():
            log("  ✅ MCQ generated"); return raw.strip()
        return ""
    except Exception as e:
        log(f"  ⚠️ MCQ: {e}"); return ""


PLAYLIST_DEFINITIONS = {
    "sivan":     {"name": "சிவன் வழிபாடு | Shiva Devotional",
                  "keywords": ["சிவன்", "shiva", "shivaratri", "lingam"]},
    "murugan":   {"name": "முருகன் வழிபாடு | Murugan Devotional",
                  "keywords": ["முருகன்", "murugan", "skanda", "வேல்"]},
    "vinayagar": {"name": "விநாயகர் வழிபாடு | Vinayagar Devotional",
                  "keywords": ["விநாயகர்", "ganesh", "pillaiyar", "chaturthi"]},
    "perumal":   {"name": "பெருமாள் வழிபாடு | Perumal Devotional",
                  "keywords": ["பெருமாள்", "perumal", "vishnu", "thirupathi"]},
    "amman":     {"name": "அம்மன் வழிபாடு | Amman Devotional",
                  "keywords": ["அம்மன்", "lakshmi", "லட்சுமி", "durgai"]},
    "festival":  {"name": "திருவிழா சிறப்பு | Festival Specials",
                  "keywords": ["festival", "விழா", "deepavali", "pongal", "navratri"]},
    "pariharam": {"name": "தோஷ பரிகாரம் | Dosham Pariharam",
                  "keywords": ["தோஷம்", "pariharam", "rahu", "kethu", "sani"]},
    "general":   {"name": "பொது பக்தி | General Devotional", "keywords": []},
}
PLAYLIST_CACHE_FILE = "playlist_ids.json"


def load_playlist_cache():
    if os.path.exists(PLAYLIST_CACHE_FILE):
        with open(PLAYLIST_CACHE_FILE) as f: return json.load(f)
    return {}

def save_playlist_cache(c):
    with open(PLAYLIST_CACHE_FILE, "w") as f: json.dump(c, f, indent=2)

def detect_am_playlist(topic, deity=""):
    t = (topic + " " + deity).lower()
    for k, d in PLAYLIST_DEFINITIONS.items():
        if any(kw.lower() in t for kw in d["keywords"]): return k
    return "general"

def get_or_create_playlist(youtube, key):
    cache = load_playlist_cache()
    if key in cache: return cache[key]
    defn = PLAYLIST_DEFINITIONS.get(key)
    if not defn: return None
    try:
        resp = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        for item in resp.get("items", []):
            if item["snippet"]["title"] == defn["name"]:
                cache[key] = item["id"]; save_playlist_cache(cache); return item["id"]
        resp = youtube.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": defn["name"]},
                  "status":  {"privacyStatus": "public"}}).execute()
        cache[key] = resp["id"]; save_playlist_cache(cache)
        log(f"  ✅ Playlist created: {defn['name'][:40]}"); return resp["id"]
    except Exception as e:
        log(f"  ⚠️ Playlist error: {e}"); return None

def add_video_to_playlist(youtube, video_id, topic, deity=""):
    key = detect_am_playlist(topic, deity)
    pid = get_or_create_playlist(youtube, key)
    if not pid: return
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": pid, "resourceId":
                              {"kind": "youtube#video", "videoId": video_id}}}).execute()
        log(f"  ✅ Added to: {PLAYLIST_DEFINITIONS[key]['name'][:40]}")
    except Exception as e:
        log(f"  ⚠️ Playlist add: {e}")


RESPONDED_COMMENTS_FILE = "responded_comments.json"
COMMENT_RESPONSE_PROMPT = """Helpful Tamil devotional reply for "ஆலய மணி".
Video: {topic} | Deity: {deity}
Comment: {comment}
Write <150 char Tamil reply. Warm elder tone. Never claim to be bot.
Reply only."""

def load_responded():
    if os.path.exists(RESPONDED_COMMENTS_FILE):
        with open(RESPONDED_COMMENTS_FILE) as f: return set(json.load(f))
    return set()

def save_responded(ids):
    with open(RESPONDED_COMMENTS_FILE, "w") as f: json.dump(list(ids), f)

def respond_to_comments():
    log("💬 Responding to comments...")
    youtube = get_authenticated_service()
    if not youtube: log("⚠️ Auth required"); return
    responded = load_responded(); count = 0
    for meta_file in sorted(Path(METADATA_DIR).glob("*.txt"), reverse=True)[:5]:
        if count >= 10: break
        try:
            content = meta_file.read_text(encoding="utf-8")
            vid_id = deity = topic = ""
            for line in content.split("\n"):
                if line.startswith("VIDEO_ID:"): vid_id = line.split(":",1)[1].strip()
                if line.startswith("TITLE:"):    topic  = line.split(":",1)[1].strip()[:60]
                if line.startswith("DEITY:"):    deity  = line.split(":",1)[1].strip()
            if not vid_id: continue
            resp = youtube.commentThreads().list(
                part="snippet", videoId=vid_id, order="time", maxResults=20).execute()
            for item in resp.get("items", []):
                if count >= 10: break
                tid = item["id"]
                cmt = item["snippet"]["topLevelComment"]["snippet"]
                text = cmt.get("textDisplay", "")
                if (tid in responded or item["snippet"].get("totalReplyCount",0)>0 or
                    len(text)<5 or any(s in text.lower() for s in ["subscribe","http"])): continue
                if not ("?" in text or len(text) > 20): continue
                try:
                    reply = call_llm(COMMENT_RESPONSE_PROMPT.format(
                        topic=topic, deity=deity, comment=text[:200])).strip()
                    if reply and len(reply) > 5:
                        youtube.comments().insert(part="snippet",
                            body={"snippet":{"parentId":tid,"textOriginal":reply}}).execute()
                        responded.add(tid); count += 1
                        log(f"  ✅ Replied: {reply[:50]}..."); time.sleep(2)
                except Exception as e: log(f"  ⚠️ Reply: {e}")
        except Exception as e: log(f"  ⚠️ Fetch: {e}")
    save_responded(responded); log(f"✅ {count} replies posted")


ANALYTICS_FILE  = "analytics_insights.json"
UPDATE_CHECK_FILE = "update_checks.json"

def run_analytics_loop():
    log("📊 Analytics loop...")
    youtube = get_authenticated_service()
    if not youtube: log("⚠️ Auth required"); return
    deity_perf = {}
    for meta_file in sorted(Path(METADATA_DIR).glob("*.txt"), reverse=True)[:20]:
        try:
            content = meta_file.read_text(encoding="utf-8")
            vid_id = deity = ""
            for line in content.split("\n"):
                if line.startswith("VIDEO_ID:"): vid_id = line.split(":",1)[1].strip()
                if line.startswith("DEITY:"):    deity  = line.split(":",1)[1].strip()
            if not vid_id: continue
            try:
                from googleapiclient.discovery import build as _b
                ana = _b("youtubeAnalytics","v2",credentials=youtube._http.credentials)
                now = datetime.datetime.now().strftime("%Y-%m-%d")
                st  = (datetime.datetime.now()-datetime.timedelta(days=30)).strftime("%Y-%m-%d")
                r   = ana.reports().query(ids="channel==MINE",startDate=st,endDate=now,
                    metrics="views",filters=f"video=={vid_id}",dimensions="video").execute()
                rows = r.get("rows",[])
                if rows and deity: deity_perf.setdefault(deity,[]).append(int(rows[0][1]))
            except: pass
        except: pass
    deity_avg = {d:sum(v)/len(v) for d,v in deity_perf.items() if v}
    insights = {"best_deity": max(deity_avg,key=deity_avg.get) if deity_avg else "",
                "deity_avg": deity_avg, "updated": datetime.datetime.now().isoformat()}
    with open(ANALYTICS_FILE,"w") as f: json.dump(insights,f,indent=2)
    log(f"  ✅ Best deity: {insights['best_deity']}")
    try:
        run(["git","add",ANALYTICS_FILE])
        run(["git","commit","-m","chore: analytics update"])
        run(["git","push"])
    except: pass

def load_analytics_insights():
    if os.path.exists(ANALYTICS_FILE):
        try:
            with open(ANALYTICS_FILE) as f: return json.load(f)
        except: pass
    return {}

def run_update_checks():
    log("🔄 Update checks..."); youtube = get_authenticated_service()
    if not youtube: log("⚠️ Auth required"); return
    checks = {}
    if os.path.exists(UPDATE_CHECK_FILE):
        with open(UPDATE_CHECK_FILE) as f: checks = json.load(f)
    today = datetime.datetime.now().strftime("%Y-%m-%d"); count = 0
    for meta_file in sorted(Path(METADATA_DIR).glob("*.txt"), reverse=True):
        if count >= 5: break
        try:
            content = meta_file.read_text(encoding="utf-8")
            vid_id = topic = date = ""
            for line in content.split("\n"):
                if line.startswith("VIDEO_ID:"): vid_id = line.split(":",1)[1].strip()
                if line.startswith("TITLE:"):    topic  = line.split(":",1)[1].strip()[:80]
                if line.startswith("CREATED:"): date   = line.split(":",1)[1].strip()[:10]
            if not vid_id or not topic: continue
            try:
                if (datetime.datetime.now()-datetime.datetime.fromisoformat(date)).days<30: continue
            except: continue
            last = checks.get(vid_id,{}).get("last_check","")
            if last:
                try:
                    if (datetime.datetime.now()-datetime.datetime.fromisoformat(last)).days<7: continue
                except: pass
            raw = call_llm(f"Tamil devotional video topic: {topic}\nDate: {date}\nToday: {today}\n"
                           f"Does any festival date or ritual procedure need updating? "
                           f'Return JSON: {{"needs_update":true/false,"update_comment":"<Tamil <200 chars if needed>","reason":"<English>"}}')
            try:
                result = parse_json_response(raw)
                checks[vid_id] = {"last_check":today,"needs_update":result.get("needs_update",False)}
                if result.get("needs_update") and result.get("update_comment"):
                    cmt = f"📢 UPDATE ({today}): {result['update_comment']}\nஆலய மணி — புதுப்பிக்கப்பட்ட தகவல்"
                    try:
                        youtube.commentThreads().insert(part="snippet",
                            body={"snippet":{"videoId":vid_id,"topLevelComment":
                                             {"snippet":{"textOriginal":cmt}}}}).execute()
                        log(f"  ✅ Update posted")
                    except Exception as e: log(f"  ⚠️ Comment: {e}")
                count += 1
            except: pass
        except: pass
    with open(UPDATE_CHECK_FILE,"w") as f: json.dump(checks,f,ensure_ascii=False,indent=2)
    log(f"✅ {count} videos checked")

def post_community_content():
    log("📢 Community post...")
    now = datetime.datetime.now()
    topics = load_recent_topics(1)
    recent = topics[0] if topics else "பக்தி"
    try:
        raw = call_llm(
            f"Tamil devotional community post for 'ஆலய மணி'. "
            f"Day: {now.strftime('%A')}. Recent topic: {recent}. "
            f"Monday=poll, Wednesday=tip, Friday=fact, Sunday=quiz. "
            f'Return JSON: {{"type":"poll"or"post","text":"<Tamil<500chars>","options":["opt1","opt2","opt3","opt4"]}}')
        data = parse_json_response(raw)
        os.makedirs("community_posts",exist_ok=True)
        out = f"community_posts/{now.strftime('%Y%m%d')}.txt"
        with open(out,"w",encoding="utf-8") as f:
            f.write(f"Type: {data.get('type')}\nText: {data.get('text','')}\nOptions: {data.get('options',[])}\n")
        log(f"  ✅ Saved: {out}")
    except Exception as e: log(f"  ⚠️ Community: {e}")

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
    """Build YouTube API service with auto scope-refresh."""
    import pickle, base64, os
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    REQUIRED_SCOPES = {
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    }

    creds = None
    b64 = os.environ.get("YOUTUBE_TOKEN_BASE64", "")

    if b64:
        try:
            creds = pickle.loads(base64.b64decode(b64))
        except Exception as e:
            log(f"  ⚠️ Token decode failed: {e}")
            return None

    if not creds:
        token_file = "youtube_token.pickle"
        if os.path.exists(token_file):
            with open(token_file, "rb") as f:
                creds = pickle.load(f)

    if not creds:
        log("  ⚠️ No YouTube credentials found")
        return None

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log("  ✅ Token refreshed")
        except Exception as e:
            log(f"  ⚠️ Token refresh failed: {e}")
            return None

    # Check if force-ssl scope is present (needed for comments)
    token_scopes = set(getattr(creds, "scopes", []) or [])
    missing = REQUIRED_SCOPES - token_scopes
    if "https://www.googleapis.com/auth/youtube.force-ssl" in missing:
        log("  ℹ️ Token missing youtube.force-ssl — run setup_youtube_secrets.py locally to re-auth")
        # Still usable for upload, just not comments

    if not creds.valid:
        log("  ⚠️ Token invalid and cannot be refreshed — re-run auth setup")
        return None

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"  ⚠️ YouTube API build failed: {e}")
        return None


def validate_script(text, lang="tamil"):
    """
    Quality check on generated script.
    Returns (is_valid, cleaned_text, reason).
    """
    if not text or len(text) < 500:
        return False, text, "too short"

    # Strip markdown artifacts
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)  # headers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)      # bold/italic
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)     # bullets
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)  # numbered lists
    text = re.sub(r"```[^`]*```", "", text, flags=re.DOTALL)      # code blocks
    text = re.sub(r"\\[BEAT \\d+[^\\]]*\\]", "", text)                  # [BEAT 1] labels
    text = re.sub(r"\\[[A-Z][A-Z ]+\\]", "", text)                     # [HOOK] [CTA] labels
    text = re.sub(r"^\\s*\\*{2,}.*?\\*{2,}\\s*$", "", text, flags=re.MULTILINE) # **headers**
    text = re.sub(r"^-{3,}\\s*$", "", text, flags=re.MULTILINE)         # --- dividers
    text = re.sub(r"\\n{3,}", "\\n\\n", text)                            # excess blank lines
    text = text.strip()

    # Check Tamil character ratio (should be >40% for Tamil scripts)
    tamil_chars = len(re.findall(r"[\u0B80-\u0BFF]", text))
    total_chars = len(text.replace(" ","").replace("\n",""))
    if total_chars > 0:
        tamil_ratio = tamil_chars / total_chars
        if tamil_ratio < 0.30:
            return False, text, f"Tamil ratio too low: {tamil_ratio:.0%}"

    return True, text, "ok"


def failure_alert(message):
    """Print GitHub Actions error annotation for visibility in CI."""
    print(f"::error title=ஆலய மணி Bot Error::{message}")
    log(f"❌ ALERT: {message}")

def validate_tags(tags_str):
    """YouTube max: 500 chars total, max 30 tags."""
    tags = [t.strip() for t in tags_str.split(",") if t.strip()][:30]
    result, total = [], 0
    for tag in tags:
        if total + len(tag) + 1 <= 490:
            result.append(tag)
            total += len(tag) + 1
        else:
            break
    return ", ".join(result)


THUMBNAIL_DIR = "thumbnails"
TAMIL_BOLD_FONT = "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf"
ENG_BOLD_FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

AM_THUMB_CONFIGS = {
    "முருகன்":   {"c1":(55,10,0),  "c2":(15,2,0),  "acc":(255,130,0), "glow":(255,100,0)},
    "சிவன்":    {"c1":(8,0,35),   "c2":(2,0,12),  "acc":(140,90,255),"glow":(110,70,200)},
    "விநாயகர்": {"c1":(38,18,0),  "c2":(12,5,0),  "acc":(255,175,0), "glow":(210,140,0)},
    "நடராஜர்":  {"c1":(8,4,38),   "c2":(2,0,12),  "acc":(150,110,255),"glow":(120,85,210)},
    "ஐயப்பன்":  {"c1":(0,22,8),   "c2":(0,6,2),   "acc":(0,195,75),  "glow":(0,155,55)},
    "அம்மன்":   {"c1":(48,0,28),  "c2":(18,0,8),  "acc":(255,55,170),"glow":(215,35,135)},
    "பெருமாள்": {"c1":(0,28,48),  "c2":(0,8,18),  "acc":(0,175,215), "glow":(0,140,175)},
    "கிருஷ்ணர்":{"c1":(0,8,45),   "c2":(0,2,18),  "acc":(80,150,255),"glow":(50,120,220)},
    "லட்சுமி":  {"c1":(48,38,0),  "c2":(18,12,0), "acc":(255,215,0), "glow":(215,175,0)},
    "சூரியன்":  {"c1":(55,30,0),  "c2":(22,8,0),  "acc":(255,160,0), "glow":(225,120,0)},
    "default":   {"c1":(38,22,0),  "c2":(12,6,0),  "acc":(255,195,45),"glow":(195,155,0)},
}

def generate_thumbnail(title, deity_name, output_name, deity_en="", bg_image_path=None):
    """Dynamic thumbnail — photo background + high-contrast text overlay."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
        import math, random, hashlib
        os.makedirs(THUMBNAIL_DIR, exist_ok=True)

        W, H = 1280, 720

        AM_PALETTE = {
            "முருகன்":   ((50,8,0),   (12,2,0),  (255,125,0)),
            "சிவன்":    ((5,0,32),   (1,0,8),   (140,85,255)),
            "விநாயகர்": ((35,16,0),  (10,4,0),  (255,170,0)),
            "நடராஜர்":  ((6,2,35),   (1,0,8),   (145,105,255)),
            "ஐயப்பன்":  ((0,20,6),   (0,5,1),   (0,190,70)),
            "அம்மன்":   ((45,0,25),  (15,0,7),  (255,50,165)),
            "பெருமாள்": ((0,25,45),  (0,7,15),  (0,170,210)),
            "கிருஷ்ணர்":((0,6,42),   (0,1,15),  (75,145,255)),
            "லட்சுமி":  ((45,35,0),  (15,10,0), (255,210,0)),
            "சூரியன்":  ((52,28,0),  (20,6,0),  (255,155,0)),
            "default":  ((35,20,0),  (10,5,0),  (255,190,40)),
        }

        c1, c2, acc = AM_PALETTE.get(deity_name, AM_PALETTE["default"])
        topic_seed  = int(hashlib.md5(title.encode()).hexdigest()[:8], 16)
        random.seed(topic_seed)

        # Try photo background first (real temple/deity image)
        if bg_image_path and os.path.exists(bg_image_path):
            try:
                bg = Image.open(bg_image_path).convert("RGB").resize((W, H), Image.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=12))
                bg = ImageEnhance.Brightness(bg).enhance(0.28)  # darken for text readability
                # Tint with deity color
                tint = Image.new("RGB", (W, H), c1)
                img = Image.blend(bg, tint, alpha=0.35)
            except Exception:
                img = Image.new("RGB", (W, H), c1)
        else:
            img = Image.new("RGB", (W, H), c1)
        d   = ImageDraw.Draw(img)

        # Background gradient overlay (subtle — blends with photo)
        for y in range(H):
            t   = y / H
            col = tuple(int(c1[j]+(c2[j]-c1[j])*t) for j in range(3))
            overlay_img = Image.new("RGBA", (W, 1), col + (60,))  # 60/255 alpha
            img.paste(Image.new("RGB", (W, 1), col), (0, y),
                     Image.new("L", (W, 1), 60))

        def lf(size, tamil=False):
            try:
                p = TAMIL_BOLD_FONT if tamil else ENG_BOLD_FONT
                return ImageFont.truetype(p, size)
            except:
                return ImageFont.load_default()

        def sh(x, y, text, font, fill, shadow=(0,0,0)):
            for ox,oy in [(4,4),(-2,-2),(3,-2),(-2,3)]:
                d.text((x+ox,y+oy), text, font=font, fill=shadow)
            d.text((x,y), text, font=font, fill=fill)

        def is_tamil(t):
            return any("\u0B80"<=c<="\u0BFF" for c in t)

        def auto_font(text, size):
            return lf(size, tamil=is_tamil(text))

        def wrap(text, n=14):
            words=text.split(); lines,line=[],""
            for w in words:
                if len(line+w)<=n: line+=w+" "
                else:
                    if line: lines.append(line.strip())
                    line=w+" "
            if line: lines.append(line.strip())
            return lines[:3]

        # ── 4 dynamic accent patterns (rotate by topic seed) ──────────
        style = topic_seed % 4

        if style == 0:
            # Large deity initial letter — watermark right
            try:
                big = lf(340, tamil=True)
                d.text((W-240, H//2-170), deity_name[0], font=big,
                       fill=(*acc, 22))
            except: pass
            d.rectangle([0,0,W,14], fill=acc)
            d.rectangle([0,H-14,W,H], fill=acc)

        elif style == 1:
            # Diagonal light beam from top-right corner
            cx, cy = W+80, -60
            for r in range(580,0,-12):
                t = 1-r/580
                a = int(t*16)
                col = tuple(min(255, c+a*3) for c in c1)
                d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=col)
            d.rectangle([0,0,W,14], fill=acc)

        elif style == 2:
            # Concentric circles — sacred geometry right side
            cx2, cy2 = W-155, H//2
            for r in [210,165,122,82,48]:
                alpha = min(90, 25+(210-r)//8)
                d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r],
                          outline=(*acc, alpha), width=1)
            d.rectangle([0,0,16,H], fill=acc)

        else:
            # Radial burst from bottom-right
            for i in range(0, 180, 15):
                rad2 = math.radians(i)
                x2 = W + int(math.cos(rad2)*900)
                y2 = H + int(math.sin(rad2)*900)
                d.line([(W,H),(x2,y2)], fill=(*acc,10), width=2)
            d.rectangle([0,H-14,W,H], fill=acc)

        # ── TEXT (3 elements only) ─────────────────────────────────────
        # 1. Deity name — top-left, accent color
        sh(32, 28, deity_name, auto_font(deity_name, 60), acc)

        # 2. OM symbol — top-right
        sh(W-88, 18, "ॐ", lf(56), acc)

        # 3. Title — large, max readability
        lines = wrap(title, 14)
        ty = 116
        for i, ln in enumerate(lines):
            fs  = 84 if i==0 else 58
            col = (255,255,255) if i==0 else (235,224,203)
            sh(32, ty, ln, auto_font(ln, fs), col)
            ty += fs + 10

        # Thin accent underline
        d.rectangle([32, ty+8, min(32+360, int(W*0.62)), ty+14], fill=acc)

        # Channel — bottom-left, small
        try:
            d.text((32, H-38), "ஆலய மணி",
                   font=lf(22, tamil=True), fill=(*acc, 155))
        except: pass

        out = f"{THUMBNAIL_DIR}/{output_name}_thumb.png"
        img.save(out)
        log(f"  ✅ Thumbnail: {out}")
        return out

    except Exception as e:
        log(f"  ⚠️ Thumbnail failed: {e}")
        import traceback; log(traceback.format_exc()[:200])
        return None



# ═══════════════════════════════════════════════════════════════════
# RESILIENT LLM ROUTER — 5-provider waterfall
# Priority: Groq (fast) → Gemini (reliable) → GitHub Models (free)
#           → Cerebras (fast free) → Groq fallback models
#
# All providers use OpenAI-compatible SDK for consistency.
# GitHub Models: uses GITHUB_TOKEN (auto-set in Actions — zero config)
# Cerebras: uses CEREBRAS_API_KEY secret (optional, add if available)
# ═══════════════════════════════════════════════════════════════════

GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
CEREBRAS_KEY    = os.environ.get("CEREBRAS_API_KEY", "")

# ── Provider configs ────────────────────────────────────────────────
PROVIDERS = [
    # name, base_url, api_key, model, use_for
    ("groq",     "https://api.groq.com/openai/v1",         GROQ_API_KEY,  "llama-3.3-70b-versatile",        "script"),
    ("gemini",   None,                                       GEMINI_KEY,    "gemini-2.5-flash",               "all"),
    ("github",   "https://models.inference.ai.azure.com",  GITHUB_TOKEN,  "gpt-4o-mini",                    "all"),
    ("cerebras", "https://api.cerebras.ai/v1",              CEREBRAS_KEY,  "llama-3.3-70b",                  "all"),
    ("groq_fb",  "https://api.groq.com/openai/v1",         GROQ_API_KEY,  "llama3-8b-8192",                 "fallback"),
]

def _call_provider(name, base_url, api_key, model, prompt, max_tokens=4000):
    """Call a single provider. Returns text or raises."""
    if not api_key:
        raise Exception(f"{name}: no API key")

    if name == "gemini":
        # Gemini uses its own SDK
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model, contents=prompt)
        return resp.text
    else:
        # All others: OpenAI-compatible
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.85,
        )
        return resp.choices[0].message.content


def _is_retryable(err_str):
    """True if the error is transient (rate limit / server overload)."""
    return any(c in err_str for c in [
        "429", "503", "502", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
        "high demand", "overloaded", "ServiceUnavailable",
        "rate_limit", "tokens per day", "TPD", "Internal",
        "timeout", "timed out",
    ])


def call_llm(prompt, max_retries=3, prefer="gemini", max_tokens=4000):
    """
    Resilient multi-provider router.
    Tries each provider in priority order.
    On transient errors → retry with backoff.
    On permanent errors → skip to next provider immediately.
    """
    # Build provider order based on preference
    if prefer == "groq":
        order = ["groq", "gemini", "github", "cerebras", "groq_fb"]
    else:
        order = ["gemini", "groq", "github", "cerebras", "groq_fb"]

    provider_map = {p[0]: p for p in PROVIDERS}
    last_error = ""

    for provider_name in order:
        if provider_name not in provider_map:
            continue
        name, base_url, api_key, model, _ = provider_map[provider_name]
        if not api_key:
            continue   # skip providers with no key configured

        for attempt in range(max_retries):
            try:
                result = _call_provider(name, base_url, api_key, model, prompt, max_tokens)
                if result and result.strip():
                    if attempt > 0 or provider_name != order[0]:
                        log(f"  ✅ LLM: {name}/{model.split('-')[0]}")
                    return result.strip()
            except Exception as e:
                err = str(e)
                last_error = err
                if _is_retryable(err):
                    # Daily limit hit — skip provider entirely
                    if "tokens per day" in err or "TPD" in err or "daily" in err.lower():
                        log(f"  ⚠️ {name}: daily limit — trying next provider")
                        break
                    wait = min(10 * (2 ** attempt), 60)
                    log(f"  ⏳ {name} retry {attempt+1}/{max_retries} in {wait}s ({err[:60]})")
                    time.sleep(wait)
                else:
                    # Non-retryable (auth, invalid model etc) — skip provider
                    log(f"  ⚠️ {name}: {err[:80]} — skipping")
                    break

    raise Exception(f"All LLM providers failed. Last: {last_error[:150]}")


def call_llm_groq(prompt, max_retries=3):
    """Script generation — prefers Groq for quality, all providers as fallback."""
    return call_llm(prompt, max_retries=max_retries, prefer="groq", max_tokens=4000)


def call_llm_gemini(prompt, max_retries=3):
    """Explicit Gemini — but falls back gracefully to other providers."""
    return call_llm(prompt, max_retries=max_retries, prefer="gemini", max_tokens=2000)


# Keep _call_gemini and _call_groq for backward compatibility
def _call_gemini(prompt, max_retries=5):
    return call_llm(prompt, max_retries=max_retries, prefer="gemini")

def _call_groq(prompt, max_retries=3):
    return call_llm(prompt, max_retries=max_retries, prefer="groq")


UPLOAD_QUEUE_FILE = "upload_queue.json"


def is_quota_exceeded(err_str):
    """Check if error is YouTube quota exceeded."""
    return any(x in str(err_str).lower() for x in
               ["quotaexceeded", "quota exceeded", "usageexceeded",
                "403", "dailylimitexceeded"])


def queue_for_retry(video_path, metadata, privacy="public"):
    """Save failed upload to queue for next run."""
    try:
        queue = []
        if os.path.exists(UPLOAD_QUEUE_FILE):
            with open(UPLOAD_QUEUE_FILE) as f:
                queue = json.load(f)
        queue.append({
            "video_path": video_path,
            "metadata":   metadata,
            "privacy":    privacy,
            "queued_at":  datetime.datetime.now().isoformat(),
        })
        with open(UPLOAD_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)
        log(f"  📋 Queued for retry: {os.path.basename(video_path)}")
        # Commit queue to git so it persists
        try:
            run(["git", "config", "user.email", "bot@channel.com"])
            run(["git", "config", "user.name",  "Bot"])
            run(["git", "add", UPLOAD_QUEUE_FILE])
            run(["git", "commit", "-m", "chore: queue video for upload retry"])
            run(["git", "push"])
        except: pass
    except Exception as e:
        log(f"  ⚠️ Queue save failed: {e}")


def upload_pending_from_queue():
    """Upload any videos queued from previous failed runs."""
    if not os.path.exists(UPLOAD_QUEUE_FILE):
        return
    try:
        with open(UPLOAD_QUEUE_FILE) as f:
            queue = json.load(f)
        if not queue:
            return
        log(f"📤 Processing upload queue ({len(queue)} pending)...")
        youtube = get_authenticated_service()
        if not youtube:
            return
        remaining = []
        for item in queue:
            path = item.get("video_path", "")
            if not os.path.exists(path):
                log(f"  ⚠️ Queued file missing: {path} — skipping")
                continue
            try:
                vid = upload_to_youtube(path, item.get("metadata", {}),
                                        item.get("privacy", "public"))
                if vid:
                    log(f"  ✅ Queued upload succeeded: {vid}")
                else:
                    remaining.append(item)
            except Exception as e:
                if is_quota_exceeded(e):
                    log(f"  ⚠️ Still quota exceeded — keeping in queue")
                    remaining.append(item)
                else:
                    log(f"  ⚠️ Queue upload failed: {e}")
        with open(UPLOAD_QUEUE_FILE, "w") as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)
        if not remaining:
            try:
                run(["git", "add", UPLOAD_QUEUE_FILE])
                run(["git", "commit", "-m", "chore: clear upload queue"])
                run(["git", "push"])
            except: pass
    except Exception as e:
        log(f"  ⚠️ Queue processing failed: {e}")


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
            "categoryId": "27",   # Education (better recommendation pool for devotional)
            "defaultLanguage": "ta",
            "defaultAudioLanguage": "ta",
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

        # Upload custom thumbnail
        thumb = metadata.get("thumbnail_path", "")
        if thumb and os.path.exists(thumb):
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumb, mimetype="image/jpeg")
                ).execute()
                log("  ✅ Custom thumbnail uploaded")
            except Exception as e:
                log(f"  ⚠️ Thumbnail upload: {e}")

        # Add end screen elements
        video_dur = metadata.get("duration_seconds", 360)
        add_end_screen(youtube, video_id, video_dur)

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



# ═══════════════════════════════════════════════════════════════════════
# UNIVERSAL SCENE GENERATOR — pure Pillow, zero network, always works
# Generates 6-8 images per video using topic + deity/format as seed
# ═══════════════════════════════════════════════════════════════════════

def generate_video_scenes(output_name, topic="", scene_type="default",
                          num_scenes=6, channel="generic"):
    """Generate rich animated scene images. Pure Pillow — no network needed.

    channel: "am" = devotional, "nn" = finance, "tt" = cars, "generic"
    scene_type: format or deity or topic category
    Returns list of image paths.
    """
    from PIL import Image, ImageDraw, ImageFont
    import os, math, random, hashlib

    seed = int(hashlib.md5((output_name + topic).encode()).hexdigest()[:8], 16)
    random.seed(seed)
    W, H = 1920, 1080

    scene_dir = os.path.join(PEXELS_DIR, output_name)
    os.makedirs(scene_dir, exist_ok=True)

    def sf(size, bold=True):
        try:
            p = ENG_BOLD_FONT if bold else ENG_REG_FONT
            return ImageFont.truetype(p, size)
        except: return ImageFont.load_default()

    def tf(size):
        try: return ImageFont.truetype(TAMIL_BOLD_FONT, size)
        except: return ImageFont.load_default()

    def grad(d, c1, c2, w=W, h=H, axis='v'):
        for i in range(h if axis=='v' else w):
            t = i / (h if axis=='v' else w)
            col = tuple(int(c1[j]+(c2[j]-c1[j])*t) for j in range(3))
            if axis=='v': d.line([(0,i),(w,i)], fill=col)
            else: d.line([(i,0),(i,h)], fill=col)

    def glow(d, cx, cy, r_max, color, steps=15):
        for r in range(r_max, 0, -r_max//steps):
            t = 1-r/r_max
            a = int(t*28)
            d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=(*color[:3],a))

    paths = []

    # ── Select scene palette based on channel ────────────────────────
    if channel == "am":
        palettes = [
            {"c1":(45,8,0),  "c2":(10,2,0),  "acc":(255,125,0),  "name":"dawn"},
            {"c1":(5,0,30),  "c2":(1,0,8),   "acc":(140,85,255), "name":"dusk"},
            {"c1":(0,20,5),  "c2":(0,5,1),   "acc":(0,190,70),   "name":"forest"},
            {"c1":(40,0,22), "c2":(12,0,6),  "acc":(255,50,160), "name":"temple"},
            {"c1":(42,30,0), "c2":(12,8,0),  "acc":(255,200,0),  "name":"golden"},
            {"c1":(0,22,40), "c2":(0,6,12),  "acc":(0,170,210),  "name":"ocean"},
        ]
    elif channel == "nn":
        palettes = [
            {"c1":(28,3,3),  "c2":(50,6,6),  "acc":(225,35,35),  "name":"alert"},
            {"c1":(3,14,30), "c2":(5,24,52), "acc":(50,142,255), "name":"trust"},
            {"c1":(2,20,5),  "c2":(4,38,8),  "acc":(0,190,75),   "name":"growth"},
            {"c1":(22,14,3), "c2":(38,25,5), "acc":(255,160,0),  "name":"warm"},
            {"c1":(18,3,24), "c2":(30,5,40), "acc":(175,75,255), "name":"premium"},
            {"c1":(8,7,4),   "c2":(18,14,8), "acc":(215,162,0),  "name":"gold"},
        ]
    else:  # tt / generic
        palettes = [
            {"c1":(5,10,22), "c2":(18,8,38), "acc":(232,0,28),   "name":"speed"},
            {"c1":(4,22,5),  "c2":(2,8,2),   "acc":(0,215,95),   "name":"launch"},
            {"c1":(6,6,28),  "c2":(2,2,16),  "acc":(50,148,255), "name":"tech"},
            {"c1":(0,18,22), "c2":(0,6,10),  "acc":(0,228,198),  "name":"ev"},
            {"c1":(20,10,3), "c2":(7,3,0),   "acc":(255,138,0),  "name":"offroad"},
            {"c1":(8,6,4),   "c2":(20,14,8), "acc":(255,198,0),  "name":"classic"},
        ]

    scene_list = ["hero", "ambient", "detail", "wide", "close", "atmosphere",
                  "texture", "perspective"][:num_scenes]

    for i, scene_name in enumerate(scene_list):
        out = os.path.join(scene_dir, f"{i:02d}_{scene_name}.png")
        if os.path.exists(out) and os.path.getsize(out) > 5000:
            paths.append(out); continue

        pal = palettes[i % len(palettes)]
        c1, c2, acc = pal["c1"], pal["c2"], pal["acc"]
        rs = seed + i * 6547  # different seed per scene
        random.seed(rs)

        img = Image.new("RGB", (W,H), c1)
        d   = ImageDraw.Draw(img)
        grad(d, c1, c2)

        # ── Scene-specific elements ──────────────────────────────────

        if scene_name == "hero":
            # Central glow with radiating lines
            cx, cy = W//2, H//2
            glow(d, cx, cy, 500, acc, 20)
            for angle in range(0, 360, 12):
                rad = math.radians(angle + rs%30)
                length = random.randint(300, 700)
                x2 = cx + int(math.cos(rad)*length)
                y2 = cy + int(math.sin(rad)*length)
                d.line([(cx,cy),(x2,y2)], fill=(*acc,6+random.randint(0,8)), width=1)
            glow(d, cx, cy, 200, acc, 12)
            # Channel-specific symbol
            if channel == "am":
                try: d.text((cx,cy-40), "ॐ", font=sf(220), fill=(*acc,60), anchor="mm")
                except: pass
            elif channel == "nn":
                try: d.text((cx,cy-30), "₹", font=sf(260), fill=(*acc,50), anchor="mm")
                except: pass
            else:
                # Car silhouette
                s = 1.8
                body = [(cx-int(120*s),cy+int(25*s)),(cx-int(122*s),cy-int(8*s)),
                        (cx-int(95*s),cy-int(35*s)),(cx-int(30*s),cy-int(62*s)),
                        (cx+int(45*s),cy-int(62*s)),(cx+int(105*s),cy-int(30*s)),
                        (cx+int(122*s),cy-int(8*s)),(cx+int(124*s),cy+int(25*s))]
                d.polygon(body, fill=(22,25,38))
                d.polygon(body, outline=acc, width=2)

        elif scene_name == "ambient":
            # Particle field
            for _ in range(120):
                px = random.randint(0,W); py = random.randint(0,H)
                r = random.choice([1,1,1,2,2,3])
                a = random.randint(40,160)
                d.ellipse([px-r,py-r,px+r,py+r], fill=(*acc,a))
            # Horizontal streaks
            for _ in range(30):
                y2 = random.randint(0,H)
                ln = random.randint(50,400)
                x2 = random.randint(0,W)
                a = random.randint(15,50)
                d.line([(x2,y2),(x2+ln,y2)], fill=(*acc,a), width=1)
            # Central glow subtle
            glow(d, W//2+random.randint(-200,200), H//2+random.randint(-100,100), 300, acc, 8)

        elif scene_name == "detail":
            # Grid pattern with focal point
            for x in range(0,W,90):
                a = max(8, 30 - abs(x-W//2)//30)
                d.line([(x,0),(x,H)], fill=(*acc,a), width=1)
            for y in range(0,H,90):
                a = max(8, 30 - abs(y-H//2)//20)
                d.line([(0,y),(W,y)], fill=(*acc,a), width=1)
            # Focal circle
            cx2 = W//2 + random.randint(-200,200)
            cy2 = H//2 + random.randint(-80,80)
            glow(d, cx2, cy2, 280, acc, 15)
            for r in [200,160,120,80]:
                d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r], outline=(*acc,40+r//10), width=1)

        elif scene_name == "wide":
            # Panoramic horizontal layers
            num_layers = random.randint(4,7)
            for layer in range(num_layers):
                t = layer/num_layers
                y1 = int(H*t); y2 = int(H*(t+1/num_layers))+2
                darkness = 0.6 + t*0.4
                col = tuple(int(c1[j]*darkness + acc[j]*(1-darkness)*0.15) for j in range(3))
                d.rectangle([0,y1,W,y2], fill=col)
            # Horizon glow
            hy = H//2 + random.randint(-50,50)
            for r in range(H//3, 0, -H//60):
                t = 1-r/(H//3)
                a = int(t*12)
                d.ellipse([W//2-r*2,hy-r//2,W//2+r*2,hy+r//2], fill=(*acc,a))

        elif scene_name == "close":
            # Abstract close-up texture
            # Diagonal bands
            for i in range(-H, W+H, 80):
                a = random.randint(5,18)
                d.polygon([(i,0),(i+60,0),(i+60+H,H),(i+H,H)], fill=(*acc,a))
            # Dense particles in zone
            zx, zy = random.randint(W//4,W*3//4), random.randint(H//4,H*3//4)
            for _ in range(80):
                px = zx + random.randint(-250,250)
                py = zy + random.randint(-150,150)
                r = random.randint(2,6)
                a = random.randint(60,200)
                d.ellipse([px-r,py-r,px+r,py+r], fill=(*acc,a))

        elif scene_name == "atmosphere":
            # Misty layers from bottom
            for layer in range(8):
                t = layer/8
                y_base = H - int(layer * H//10)
                for y in range(max(0,y_base-80), min(H,y_base+80)):
                    tt = 1-abs(y-y_base)/80
                    a = int(tt * (20+layer*5))
                    col = tuple(min(255,c+a) for c in c1)
                    d.line([(0,y),(W,y)], fill=col)
            # Top vignette
            for y in range(H//4):
                t = 1-y/(H//4)
                col = tuple(int(c*t*0.8) for c in c1)
                d.line([(0,y),(W,y)], fill=col)
            # Floating orbs
            for _ in range(5):
                ox = random.randint(100,W-100)
                oy = random.randint(H//4,H*3//4)
                r = random.randint(30,80)
                glow(d, ox, oy, r*3, acc, 6)

        elif scene_name == "texture":
            # Geometric pattern
            size = random.choice([60,80,100])
            for row in range(H//size+2):
                for col2 in range(W//size+2):
                    x = col2*size + (row%2)*size//2
                    y = row*size
                    a = random.randint(5,22)
                    shape = (row+col2+rs) % 3
                    if shape == 0:
                        d.ellipse([x,y,x+size-4,y+size-4], outline=(*acc,a), width=1)
                    elif shape == 1:
                        d.rectangle([x+4,y+4,x+size-8,y+size-8], outline=(*acc,a), width=1)
                    else:
                        d.polygon([(x+size//2,y),(x+size,y+size),(x,y+size)],
                                  outline=(*acc,a), width=1)
            glow(d, W//2, H//2, 400, acc, 10)

        else:  # perspective
            # Tunnel / vanishing point
            cx3, cy3 = W//2+random.randint(-100,100), H//2+random.randint(-50,50)
            for r in range(600, 0, -20):
                t = 1-r/600; a = int(t*15)
                ratio = 0.6 + t*0.4
                d.ellipse([cx3-int(r*ratio),cy3-int(r*0.6),
                           cx3+int(r*ratio),cy3+int(r*0.6)],
                          outline=(*acc,a), width=1)
            glow(d, cx3, cy3, 120, acc, 10)
            # Radiating perspective lines
            for angle2 in range(0, 360, 20):
                rad2 = math.radians(angle2)
                length2 = 800
                x2 = cx3+int(math.cos(rad2)*length2)
                y2 = cy3+int(math.sin(rad2)*length2)
                d.line([(cx3,cy3),(x2,y2)], fill=(*acc,6), width=1)

        img.save(out)
        paths.append(out)

    log(f"  🎨 {len(paths)} scenes generated ({channel}/{scene_type})")
    return paths


def safe_process_day(day, image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Full pipeline: LLM picks best deity+topic → Pexels → script+metadata → video → upload."""
    bgm = bgm or BGM_FILE
    t_start = datetime.datetime.now()

    # LLM decides the best deity + topic for today
    config = discover_daily_config(day)
    topic    = config["topic"]
    emoji    = config["emoji"]
    deity    = config["deity"]
    deity_en = config["deity_en"]

    log(f"{'='*50}")
    log(f"{emoji} {deity} — {deity_en}")
    log(f"📌 {topic}")
    log(f"{'='*50}")

    # Generate / use deity-specific BGM
    deity_bgm = ensure_bgm(deity)
    if deity_bgm and os.path.exists(deity_bgm):
        bgm = deity_bgm
        log(f"🎵 Using deity BGM: {deity_bgm}")

    # Fetch images — Scenes (guaranteed) + Pexels + Wikimedia + Pollinations AI
    log("📸 Fetching images...")
    img_dir = f"/tmp/am_imgs_{day}"
    os.makedirs(img_dir, exist_ok=True)

    # Layer 1 (GUARANTEED): Animated scenes — pure PIL, zero network, always works
    images = generate_video_scenes(day, topic=topic, scene_type=deity,
                                   num_scenes=6, channel="am")
    log(f"  ✅ Scenes: {len(images)} generated")

    # Layer 2: Pexels images (fast, reliable)
    pexels_bonus = get_images_for_deity(deity, day)
    if pexels_bonus:
        images = pexels_bonus + images
        log(f"  ✅ Pexels: {len(pexels_bonus)} images")

    # Fallback: image.png if still nothing
    if not images and image:
        images = find_images(image)

    # Layer 3: Wikimedia (bonus — non-blocking, skip on any error)
    wiki_imgs = []
    try:
        wiki_imgs = fetch_wikimedia_images_am(deity, img_dir, count=3)
        if wiki_imgs:
            images = wiki_imgs + images
            log(f"  ✅ Wikimedia: {len(wiki_imgs)} temple photos")
    except Exception as e:
        log(f"  ⚠️ Wikimedia skipped: {e}")

    # Layer 4: Pollinations AI (bonus — non-blocking, skip on timeout)
    poll_img  = None
    try:
        poll_path = os.path.join(img_dir, "ai_scene.jpg")
        poll_img  = fetch_pollinations_image_am(deity_en, topic, poll_path)
        if poll_img:
            images = [poll_img] + images
            log(f"  🎨 AI image: generated")
    except Exception as e:
        log(f"  ⚠️ Pollinations skipped: {e}")

    log(f"  📦 Total images for video: {len(images)}")
    # Pass best bg image for thumbnail
    thumb_bg = poll_img or (wiki_imgs[0] if wiki_imgs else None)

    # Script first (most critical), then metadata — avoids double Groq 429
    log("🤖 Step 1: Generating script...")
    script = generate_script(config["topic"], deity)
    if not script or len(script.strip()) < 100:
        log("  ❌ Script empty — aborting pipeline")
        return None

    log("🤖 Step 2: Generating metadata...")
    metadata = generate_metadata(config)
    log(f"✅ Script: {len(script)} chars | Title: {metadata.get('title','')[:60]}...")
    metadata["topic"]          = config["topic"]
    metadata["deity"]          = deity
    metadata["script_preview"] = script[:500]

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(f"{SCRIPTS_DIR}/{day}.txt", "w", encoding="utf-8") as f:
        f.write(script)

    os.makedirs(METADATA_DIR, exist_ok=True)

    with open(f"{METADATA_DIR}/{day}.txt", "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{metadata['title']}\n\n")
        f.write(f"DESCRIPTION:\n{metadata['description']}\n\n")
        f.write(f"TAGS:\n{validate_tags(metadata.get('tags',''))}\n\n")
        f.write(f"PINNED COMMENT:\n{metadata['pinned_comment']}\n")
        f.write(f"DEITY: {deity}\n")
        f.write(f"CREATED: {datetime.datetime.now().isoformat()}\n")
    metadata["topic"]          = config["topic"]
    metadata["deity"]          = deity
    metadata["script_preview"] = script[:500]

    # Generate thumbnail with photo background
    log("🖼️ Generating thumbnail...")
    thumb_path = generate_thumbnail(
        metadata.get("title", topic), deity, day,
        deity_en=deity_en, bg_image_path=thumb_bg
    )
    if thumb_path:
        metadata["thumbnail_path"] = thumb_path
        log(f"  ✅ Thumbnail: {os.path.basename(thumb_path)}")

    log("🎬 Creating video...")
    title_short = metadata.get("title", "")[:50]
    metadata["duration_seconds"] = 360   # estimate for end screen timing
    video = create_video(script, images, day, bgm, bgm_vol,
                         deity_name=deity, deity_en=deity_en, title_short=title_short)

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    if video:
        log(f"✅ VIDEO: {video}")
        log(f"✅ SHORT: {SHORTS_DIR}/{day}_short.mp4")
        log(f"📺 {metadata['title']}")
        save_used_topic(topic)

        if upload:
            log("⬆️ Uploading to YouTube...")
            try:
                vid = upload_to_youtube(video, metadata, privacy)
                if vid:
                    log("✅ Upload complete")
                else:
                    log("⚠️ Upload skipped (auth issue) — video saved locally")
            except Exception as e:
                if is_quota_exceeded(e):
                    log(f"⚠️ YouTube quota exceeded — queued for next run")
                    queue_for_retry("", {}, "public")
                else:
                    log(f"⚠️ Upload failed (non-fatal): {e}")
    else:
        log("❌ Video creation failed")

    log(f"⏱️ Total: {elapsed:.0f}s")
    return video


def process_trending(image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Trending topic: LLM picks best deity+topic for today."""
    bgm = bgm or BGM_FILE
    t_start = datetime.datetime.now()
    log(f"{'='*50}")
    log("🔥 TRENDING / LLM-DECIDED MODE")
    log(f"{'='*50}")

    config = discover_daily_config()
    topic     = config["topic"]
    safe_name = hashlib.md5(topic.encode()).hexdigest()[:8]
    config["day_key"] = f"trending_{safe_name}"

    deity = config.get("deity", "")
    # Fetch deity-specific images from Pexels
    log("📸 Fetching Pexels images...")
    images = get_images_for_deity(deity, f"trending_{safe_name}")
    if image and not images:
        images = find_images(image)

    log("🤖 Generating script + metadata (parallel)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sf = pool.submit(generate_script, topic, "")
        mf = pool.submit(generate_metadata, config)
        script   = sf.result()
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
    video = create_video(script, images, f"trending_{safe_name}", bgm, bgm_vol)

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    if video:
        log(f"✅ VIDEO: {video}")
        save_used_topic(topic)
        if upload:
            log("⬆️ Uploading...")
            try:
                upload_to_youtube(video, metadata, privacy)
            except Exception as e:
                log(f"⚠️ Upload failed (non-fatal): {e}")
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

    trending_topic = discover_trending_topic()

    print("\n--- Main Day Video ---")
    video = safe_process_day(day, upload=False)

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
            "metadata":   metadata,
            "day":        day,
            "created":    datetime.datetime.now().isoformat(),
            "status":     "pending",
        })
        save_queue(queue)
        print(f"  ✅ Queued for upload: {os.path.basename(video)}")

    if trending_topic and metadata.get("title", "") and metadata["title"] not in trending_topic:
        print("\n--- Trending Bonus Video ---")
        safe_name   = hashlib.md5(trending_topic.encode()).hexdigest()[:8]
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
                    "metadata":   bonus_meta,
                    "day":        f"trending_{safe_name}",
                    "created":    datetime.datetime.now().isoformat(),
                    "status":     "pending",
                })
                save_queue(queue)
                print(f"  ✅ Bonus queued for upload: trending_{safe_name}")

    print("\n--- Uploading Pending ---")
    upload_pending_videos()

    return True


def should_schedule_at(hour, minute):
    now = datetime.datetime.now()
    return now.hour == hour and now.minute == minute


def run_scheduler_cycle():
    now    = datetime.datetime.now()
    hour   = now.hour
    minute = now.minute

    if hour == 5 and minute == 0:
        print(f"\n[{now}] ⏰ Generating today's content...")
        create_today_content()

    is_weekend   = now.weekday() >= 5
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
    print("  ஆலய மணி BOT — DAEMON MODE v3.0")
    print("  Anti-monotony: Varied hooks, deity voices, Pexels images")
    print("=" * 50)
    print(f"\nSchedule:")
    print(f"  05:00 — Generate today's content + trending topic")
    print(f"  Weekdays 06:00, 18:30 — Auto-upload")
    print(f"  Weekends 07:00, 19:30 — Auto-upload")
    print(f"\nPexels images enabled: {bool(PEXELS_API_KEY)}")
    print(f"YouTube uploads enabled: {os.path.exists(YOUTUBE_TOKEN_FILE)}")
    print(f"\nPress Ctrl+C to stop\n")

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

    create_today_content()
    upload_pending_videos()

    while True:
        schedule.run_pending()
        time.sleep(30)


# =============================================
# MAIN
# =============================================



# ═══════════════════════════════════════════════
# SLEEP MUSIC MODULE — merged from sleep-music-tamil
# ═══════════════════════════════════════════════

SLEEP_VIDEO_DURATION  = 10800  # 3 hours
CHANNEL_HANDLE        = "@aalayamani"
SLEEP_OUTPUT_DIR      = "sleep_videos"
SLEEP_THUMBS_DIR      = "sleep_thumbnails"
SLEEP_AUDIO_CACHE_DIR = "sleep_audio_cache"

MUSIC_PROFILES = {

    # SOLFEGGIO FREQUENCIES
    "174hz_pain_relief": {
        "title":       "174 Hz — வலி நிவாரணம் & ஆழ்ந்த தூக்கம் | 3 மணி நேர இசை",
        "title_en":    "174 Hz Solfeggio | Pain Relief Deep Sleep | 3 Hours",
        "description": "174 Hz — அடிப்படை சோல்ஃபெஜியோ அதிர்வெண். இந்த இசை உடல் வலியை குறைக்கும், ஆழமான தூக்கத்தை தரும்.",
        "tags":        "174hz,solfeggio,deep sleep tamil,pain relief,தூக்க இசை,meditation music tamil",
        "freq1": 174.0, "freq2": 87.0,  "freq3": 261.0,
        "nature": "pink", "nature_vol": 0.06,
        "binaural_beat": 3.5,   # delta wave
        "category": "sleep",
    },
    "285hz_healing": {
        "title":       "285 Hz — செல் குணமாதல் & தியானம் | 3 மணி நேர இசை",
        "title_en":    "285 Hz Healing Frequency | Tamil Meditation | 3 Hours",
        "description": "285 Hz — உடல் செல்களை குணப்படுத்தும் அதிர்வெண். காயங்கள் விரைவில் ஆற இந்த இசை உதவும்.",
        "tags":        "285hz,healing frequency,meditation tamil,தியான இசை,sleep music",
        "freq1": 285.0, "freq2": 142.5, "freq3": 427.5,
        "nature": "pink", "nature_vol": 0.05,
        "binaural_beat": 4.0,
        "category": "healing",
    },
    "396hz_fear_release": {
        "title":       "396 Hz — பயம் & குற்ற உணர்வை விடுவிக்கும் இசை | 3 Hours",
        "title_en":    "396 Hz | Release Fear & Guilt | Tamil Meditation Music",
        "description": "396 Hz — பயம், கவலை, குற்ற உணர்வுகளை விடுவிக்கும் சக்திவாய்ந்த அதிர்வெண்.",
        "tags":        "396hz,anxiety relief tamil,fear release,meditation music,தமிழ் தியானம்",
        "freq1": 396.0, "freq2": 198.0, "freq3": 594.0,
        "nature": "brown", "nature_vol": 0.07,
        "binaural_beat": 6.0,   # theta
        "category": "anxiety",
    },
    "417hz_change": {
        "title":       "417 Hz — மாற்றம் & எதிர்மறையை அகற்றும் இசை | 3 Hours",
        "title_en":    "417 Hz | Undoing Situations | Tamil Sleep Music",
        "description": "417 Hz — பழைய பாதங்களை அழிக்கும், மாற்றத்தை ஏற்படுத்தும் அதிர்வெண். தூக்கத்தில் மனசை refresh செய்யும்.",
        "tags":        "417hz,change frequency,sleep tamil,negative energy,meditation",
        "freq1": 417.0, "freq2": 208.5, "freq3": 625.5,
        "nature": "pink", "nature_vol": 0.05,
        "binaural_beat": 5.0,
        "category": "sleep",
    },
    "528hz_dna": {
        "title":       "528 Hz — DNA சரிசெய்யும் இசை & ஆழ்ந்த தூக்கம் | 3 Hours",
        "title_en":    "528 Hz DNA Repair | Love Frequency | Tamil Sleep Music",
        "description": "528 Hz — 'அன்பின் அதிர்வெண்'. DNA சரிசெய்யும், மன அமைதி தரும் மிகவும் பிரபலமான healing frequency.",
        "tags":        "528hz,dna repair,love frequency,sleep music tamil,healing,தூக்க இசை",
        "freq1": 528.0, "freq2": 264.0, "freq3": 792.0,
        "nature": "pink", "nature_vol": 0.04,
        "binaural_beat": 3.0,   # deep delta
        "category": "healing",
    },
    "639hz_relationships": {
        "title":       "639 Hz — உறவுகளை சரிசெய்யும் இசை | தியானம் | 3 Hours",
        "title_en":    "639 Hz Harmonizing Relationships | Tamil Meditation Music",
        "description": "639 Hz — குடும்ப உறவுகள், நட்பு, அன்பை மேம்படுத்தும் அதிர்வெண். தியானத்தில் இதய சக்கரத்தை திறக்கும்.",
        "tags":        "639hz,relationship healing,heart chakra,meditation tamil,harmony",
        "freq1": 639.0, "freq2": 319.5, "freq3": 958.5,
        "nature": "pink", "nature_vol": 0.05,
        "binaural_beat": 7.0,
        "category": "meditation",
    },
    "741hz_intuition": {
        "title":       "741 Hz — உள்ளுணர்வை விழிப்படுத்தும் இசை | 3 மணி நேரம்",
        "title_en":    "741 Hz Awakening Intuition | Tamil Meditation | 3 Hours",
        "description": "741 Hz — ஆறாவது புலன், உள்ளுணர்வை விழிப்படுத்தும் அதிர்வெண். ஆழ்ந்த தியானத்திற்கு சிறந்தது.",
        "tags":        "741hz,intuition,sixth sense,meditation music tamil,chakra healing",
        "freq1": 741.0, "freq2": 370.5, "freq3": 247.0,
        "nature": "white_rain", "nature_vol": 0.06,
        "binaural_beat": 8.0,   # alpha
        "category": "meditation",
    },
    "852hz_spiritual": {
        "title":       "852 Hz — ஆன்மீக ஒழுங்கை மீட்டெடுக்கும் இசை | 3 Hours",
        "title_en":    "852 Hz Return to Spiritual Order | Tamil Sleep Music",
        "description": "852 Hz — ஆன்மீக விழிப்புணர்வை அதிகரிக்கும் அதிர்வெண். மூன்றாம் கண் திறக்கும் தியானத்திற்கு பயன்படும்.",
        "tags":        "852hz,spiritual awakening,third eye,meditation tamil,sleep music",
        "freq1": 852.0, "freq2": 426.0, "freq3": 284.0,
        "nature": "pink", "nature_vol": 0.04,
        "binaural_beat": 4.5,
        "category": "spiritual",
    },
    "963hz_crown": {
        "title":       "963 Hz — கிரீட சக்கரம் & தெய்வீக இணைப்பு | 3 மணி நேரம்",
        "title_en":    "963 Hz Crown Chakra Activation | Tamil Meditation Music",
        "description": "963 Hz — மிக உயர்ந்த சோல்ஃபெஜியோ அதிர்வெண். கிரீட சக்கரத்தை செயல்படுத்தும், தெய்வீக இணைப்பை உணர்த்தும்.",
        "tags":        "963hz,crown chakra,divine connection,meditation,spiritual music tamil",
        "freq1": 963.0, "freq2": 481.5, "freq3": 321.0,
        "nature": "pink", "nature_vol": 0.03,
        "binaural_beat": 3.0,
        "category": "spiritual",
    },

    # DEITY FREQUENCIES (same as AM bot)
    "murugan_174hz": {
        "title":       "முருகன் 174 Hz — ஆழ்ந்த தூக்கம் & வழிபாடு | 3 மணி நேரம்",
        "title_en":    "Lord Murugan 174 Hz Devotional Sleep Music | 3 Hours",
        "description": "முருகன் வழிபாட்டு அதிர்வெண் 174 Hz — ஆழ்ந்த தூக்கத்தை தரும் தெய்வீக இசை.",
        "tags":        "முருகன்,murugan,devotional sleep music,174hz,tamil god music,பக்தி இசை",
        "freq1": 174.0, "freq2": 348.0, "freq3": 261.0,
        "nature": "pink", "nature_vol": 0.06,
        "binaural_beat": 4.0,
        "category": "devotional",
    },
    "sivan_136hz": {
        "title":       "சிவன் 136.1 Hz OM அதிர்வெண் — தியானம் & தூக்கம் | 3 Hours",
        "title_en":    "Lord Shiva 136Hz OM Frequency | Deep Meditation | 3 Hours",
        "description": "136.1 Hz — பூமியின் OM அதிர்வெண். சிவனின் தியான அதிர்வெண். ஆழ்ந்த மனமெய் அமைதிக்கு.",
        "tags":        "சிவன்,shiva,om frequency,136hz,meditation,deep sleep,devotional",
        "freq1": 136.1, "freq2": 272.2, "freq3": 408.3,
        "nature": "brown", "nature_vol": 0.07,
        "binaural_beat": 3.5,
        "category": "devotional",
    },
    "vinayagar_528hz": {
        "title":       "விநாயகர் 528 Hz — தடைகளை நீக்கும் தூக்க இசை | 3 Hours",
        "title_en":    "Lord Ganesha 528Hz | Remove Obstacles | Tamil Sleep Music",
        "description": "528 Hz — விநாயகருக்கு உகந்த மாற்ற அதிர்வெண். தடைகளை நீக்கும், அதிர்ஷ்டம் தரும்.",
        "tags":        "விநாயகர்,ganesha,528hz,obstacle remover,sleep music,devotional tamil",
        "freq1": 528.0, "freq2": 264.0, "freq3": 396.0,
        "nature": "pink", "nature_vol": 0.04,
        "binaural_beat": 5.0,
        "category": "devotional",
    },

    # NATURE + BINAURAL
    "rain_theta": {
        "title":       "மழை சத்தம் + Theta Waves — படிப்பு Concentration | 3 Hours",
        "title_en":    "Rain Sounds + Theta Binaural Beats | Study Focus | 3 Hours",
        "description": "மழை சத்தம் + 6Hz Theta binaural beats. படிப்பு, வேலை, concentration-க்கு சிறந்தது.",
        "tags":        "rain sounds tamil,study music,theta waves,concentration music,binaural beats",
        "freq1": 200.0, "freq2": 206.0, "freq3": 100.0,
        "nature": "rain", "nature_vol": 0.35,
        "binaural_beat": 6.0,
        "category": "study",
    },
    "river_delta": {
        "title":       "ஆற்று சத்தம் + Delta Waves — ஆழ்ந்த தூக்கம் | 3 Hours",
        "title_en":    "River Sounds + Delta Binaural | Deep Sleep Tamil | 3 Hours",
        "description": "இயற்கை ஆற்று சத்தம் + 2Hz delta binaural beats. இரவு தூக்கத்திற்கு மிகவும் சிறந்தது.",
        "tags":        "river sounds,delta waves,deep sleep tamil,binaural beats,natural sounds",
        "freq1": 150.0, "freq2": 152.0, "freq3": 75.0,
        "nature": "brown", "nature_vol": 0.40,
        "binaural_beat": 2.0,
        "category": "sleep",
    },
    "forest_alpha": {
        "title":       "காடு சத்தம் + Alpha Waves — மன அமைதி & Relaxation | 3 Hours",
        "title_en":    "Forest Sounds + Alpha Waves | Stress Relief Tamil | 3 Hours",
        "description": "காட்டு சத்தம் + 10Hz alpha binaural beats. மன அழுத்தம் குறைக்கும், relaxation தரும்.",
        "tags":        "forest sounds,alpha waves,relaxation music tamil,stress relief,meditation",
        "freq1": 250.0, "freq2": 260.0, "freq3": 125.0,
        "nature": "pink", "nature_vol": 0.25,
        "binaural_beat": 10.0,
        "category": "relaxation",
    },
    "432hz_universal": {
        "title":       "432 Hz — பிரபஞ்சத்தின் அதிர்வெண் | ஆழ்ந்த தூக்கம் | 3 Hours",
        "title_en":    "432 Hz Universal Frequency | Deep Sleep Tamil | 3 Hours",
        "description": "432 Hz — இயற்கையின் அதிர்வெண். 440Hz-ஐ விட அதிக healing power கொண்டது என்று கூறுகிறார்கள்.",
        "tags":        "432hz,universal frequency,deep sleep,healing music tamil,meditation",
        "freq1": 432.0, "freq2": 216.0, "freq3": 648.0,
        "nature": "pink", "nature_vol": 0.05,
        "binaural_beat": 3.0,
        "category": "sleep",
    },
}


def get_todays_profile():
    day_num = datetime.date.today().toordinal()
    keys = list(MUSIC_PROFILES.keys())
    return keys[day_num % len(keys)]



def generate_music(profile_key, profile, duration=SLEEP_VIDEO_DURATION):
    """Generate 3-hour music file using pure FFmpeg math synthesis."""
    cache_file = f"{SLEEP_AUDIO_CACHE_DIR}/{profile_key}_{duration}.mp3"
    if os.path.exists(cache_file):
        log(f"  Using cached audio: {cache_file}")
        return cache_file

    log(f"  Generating {duration//3600}h music: {profile_key}...")
    f1 = profile['freq1']
    f2 = profile['freq2']
    f3 = profile['freq3']
    bb = profile.get('binaural_beat', 4.0)
    nature = profile.get('nature', 'pink')
    nvol  = profile.get('nature_vol', 0.08)

    # Binaural: left ear f1, right ear f1+bb
    f1_left  = f1
    f1_right = f1 + bb

    if nature == 'rain':
        # Rain = bandpass filtered white noise
        nature_filter = f"[3:a]highpass=f=800,lowpass=f=5000,volume={nvol}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=white:r=44100:a=0.5"
    elif nature == 'brown':
        # River/stream = low-pass brown noise
        nature_filter = f"[3:a]lowpass=f=300,volume={nvol*1.5}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=pink:r=44100:a=0.5"
    elif nature == 'white_rain':
        nature_filter = f"[3:a]highpass=f=2000,lowpass=f=8000,volume={nvol}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=white:r=44100:a=0.4"
    else:
        # Pink noise bed (warm, gentle)
        nature_filter = f"[3:a]lowpass=f=600,volume={nvol}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=pink:r=44100:a=0.3"

    cmd = [
        "ffmpeg", "-y",
        # Left binaural channel (f1)
        "-f", "lavfi", "-i", f"sine=frequency={f1_left}:duration={duration}",
        # Right binaural channel (f1 + beat frequency)
        "-f", "lavfi", "-i", f"sine=frequency={f1_right}:duration={duration}",
        # Harmonic overtone
        "-f", "lavfi", "-i", f"sine=frequency={f2}:duration={duration}",
        # Nature sound
        "-f", "lavfi", "-i", nature_input,
        # Third harmonic
        "-f", "lavfi", "-i", f"sine=frequency={f3}:duration={duration}",

        "-filter_complex",
        # Pan for binaural effect
        f"[0:a]volume=0.12,pan=stereo|c0=c0[left];"
        f"[1:a]volume=0.12,pan=stereo|c1=c0[right];"
        f"[left][right]amix=inputs=2:duration=first[binaural];"
        f"[2:a]volume=0.05,afade=t=in:st=0:d=30[h2];"
        f"{nature_filter};"
        f"[4:a]volume=0.03[h3];"
        f"[binaural][h2][nat][h3]amix=inputs=4:duration=first,"
        f"afade=t=in:st=0:d=60,afade=t=out:st={duration-60}:d=60[out]",

        "-map", "[out]",
        "-ar", "44100", "-ac", "2",
        "-codec:a", "libmp3lame", "-b:a", "128k",
        "-q:a", "2",
        cache_file
    ]

    t0 = time.time()
    r = run(cmd, timeout=300)
    if r.returncode == 0:
        size_mb = os.path.getsize(cache_file) / (1024*1024)
        log(f"  ✅ Audio: {cache_file} ({size_mb:.0f}MB, {time.time()-t0:.0f}s)")
        return cache_file
    else:
        log(f"  ❌ Audio generation failed: {r.stderr[-200:]}")
        return None



def generate_sleep_thumbnail(profile_key, profile):
    """Generate a calming thumbnail — dark gradient with frequency text."""
    from PIL import Image, ImageDraw, ImageFont
    import math

    thumb_path = f"{SLEEP_THUMBS_DIR}/{profile_key}.jpg"

    # Color schemes per category
    color_schemes = {
        "sleep":      ((5, 10, 35),  (15, 30, 80),  (100, 150, 255)),
        "healing":    ((5, 25, 15),  (10, 60, 40),  (80, 200, 120)),
        "meditation": ((25, 10, 40), (60, 20, 90),  (180, 100, 255)),
        "devotional": ((35, 15, 5),  (90, 40, 10),  (255, 160, 60)),
        "anxiety":    ((5, 20, 35),  (10, 50, 80),  (60, 160, 220)),
        "spiritual":  ((20, 5, 35),  (50, 10, 80),  (200, 80, 255)),
        "study":      ((5, 25, 35),  (10, 60, 80),  (60, 200, 220)),
        "relaxation": ((5, 30, 20),  (10, 70, 50),  (60, 220, 150)),
    }
    cat  = profile.get('category', 'sleep')
    bg1, bg2, accent = color_schemes.get(cat, color_schemes['sleep'])

    W, H = 1280, 720
    img  = Image.new("RGB", (W, H), bg1)
    draw = ImageDraw.Draw(img)

    # Gradient background
    for y in range(H):
        t = y / H
        r = int(bg1[0] + (bg2[0]-bg1[0]) * t)
        g = int(bg1[1] + (bg2[1]-bg1[1]) * t)
        b = int(bg1[2] + (bg2[2]-bg1[2]) * t)
        draw.line([(0,y),(W,y)], fill=(r,g,b))

    # Concentric circles (sound waves visual)
    cx, cy = W//2, H//2
    for i in range(8):
        r2  = 80 + i*55
        alpha = max(20, 100 - i*12)
        col  = (*accent, alpha)
        draw.ellipse([cx-r2, cy-r2, cx+r2, cy+r2],
                     outline=(*accent,), width=max(1, 3-i//3))

    # Frequency text — large
    freq_text = f"{profile['freq1']:.0f} Hz"
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 120)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf", 42)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        font_lg = font_md = font_sm = ImageFont.load_default()

    # Hz number centered
    bbox = draw.textbbox((0,0), freq_text, font=font_lg)
    tw = bbox[2]-bbox[0]
    draw.text(((W-tw)//2, 140), freq_text, font=font_lg,
              fill=(*accent, 255), stroke_width=2, stroke_fill=(0,0,0,200))

    # Tamil title
    tamil_title = profile['title'].split('|')[0].strip()[:35]
    try:
        bbox2 = draw.textbbox((0,0), tamil_title, font=font_md)
        tw2 = bbox2[2]-bbox2[0]
        draw.text(((W-tw2)//2, 320), tamil_title, font=font_md,
                  fill=(255,255,255,240), stroke_width=1, stroke_fill=(0,0,0))
    except: pass

    # Duration badge
    draw.rounded_rectangle([W-200, H-65, W-20, H-20], radius=10,
                           fill=(*accent, 180))
    draw.text((W-185, H-58), "3 HOURS", font=font_sm, fill=(255,255,255))

    # Channel name
    draw.text((30, H-55), CHANNEL_HANDLE, font=font_sm,
              fill=(200,200,200,200))

    img.save(thumb_path, "JPEG", quality=95)
    log(f"  ✅ Thumbnail: {thumb_path}")
    return thumb_path



def create_sleep_video(audio_path, profile_key, profile):
    """Create video: colour background + 3-hour audio. Uses lavfi to avoid encode timeout."""
    video_path = f"{SLEEP_OUTPUT_DIR}/{profile_key}_{datetime.date.today()}.mp4"
    os.makedirs(SLEEP_OUTPUT_DIR, exist_ok=True)

    cat = profile.get("category", "sleep")
    color_map = {
        "sleep":      "050a23", "healing":    "051909",
        "meditation": "190a28", "devotional": "230f05",
        "spiritual":  "140523", "study":      "051923",
        "relaxation": "051e14", "anxiety":    "051423",
    }
    hex_col = color_map.get(cat, "050a23")

    duration = SLEEP_VIDEO_DURATION
    log(f"  🎬 Creating {duration//3600}h video (lavfi background)...")
    t0 = time.time()

    # KEY FIX: Use lavfi color source instead of encoding a looped image
    # This generates the video stream directly — no libx264 encoding of 3h footage
    # -shortest stops at audio end (~3h), video stream is just a solid colour
    # This completes in under 60s instead of 10+ minutes
    cmd = [
        "ffmpeg", "-y",
        "-f",    "lavfi",
        "-i",    f"color=c=0x{hex_col}:size=1280x720:rate=1",   # 720p not 1080p — faster
        "-i",    audio_path,
        "-c:v",  "libx264", "-preset", "ultrafast", "-crf", "51",  # max compression, tiny file
        "-tune", "stillimage",    # tells x264 it's a static image — MUCH faster
        "-pix_fmt", "yuv420p",
        "-c:a",  "copy",
        "-shortest",
        "-movflags", "+faststart",
        "-t",    str(duration),   # explicit duration cap
        video_path
    ]
    r = run(cmd, timeout=1800)   # 30 min hard timeout (3h video should take <2 min with stillimage)

    if r.returncode == 0:
        size_mb = os.path.getsize(video_path) / (1024*1024)
        log(f"  ✅ Video: {video_path} ({size_mb:.0f}MB, {time.time()-t0:.0f}s)")
        return video_path
    else:
        log(f"  ❌ Video failed: {r.stderr[-300:]}")
        return None



def _save_playlist_id(pid, playlist_file="sleep_playlist_id.txt"):
    """Save playlist ID to file. Workflow step handles git commit+push."""
    try:
        with open(playlist_file, "w") as f:
            f.write(pid)
        log(f"  ✅ Playlist ID written to {playlist_file}: {pid}")
    except Exception as e:
        log(f"  ⚠️ Could not write playlist ID: {e}")


def _get_or_create_sleep_playlist(yt):
    """Get existing sleep playlist ID or auto-create one. Persists to repo."""
    playlist_file = "sleep_playlist_id.txt"

    # 1. Check env secret first
    pid = os.environ.get("SLEEP_PLAYLIST_ID", "").strip()
    if pid:
        log(f"  📋 Using SLEEP_PLAYLIST_ID secret: {pid}")
        return pid

    # 2. Check persisted file from previous run
    if os.path.exists(playlist_file):
        pid = open(playlist_file).read().strip()
        if pid:
            log(f"  📋 Using saved playlist: {pid}")
            return pid

    # 3. Search existing playlists on channel
    try:
        resp = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        for item in resp.get("items", []):
            title_lower = item["snippet"]["title"].lower()
            if any(kw in title_lower for kw in
                   ["தூக்கம்", "sleep", "meditation", "tookam", "aazhn"]):
                pid = item["id"]
                log(f"  📋 Found existing playlist: '{item['snippet']['title']}' → {pid}")
                _save_playlist_id(pid, playlist_file)
                return pid
    except Exception as e:
        log(f"  ⚠️ Playlist search failed: {e}")

    # 4. Create new playlist
    try:
        resp = yt.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title":           "ஆழ்ந்த தூக்கம் — Tamil Sleep & Meditation Music",
                    "description": (
                        "தமிழ் தியான இசை — Solfeggio frequencies, binaural beats, "
                        "deity frequencies & nature sounds.\n\n"
                        "174Hz • 285Hz • 396Hz • 417Hz • 528Hz • 639Hz • 741Hz • 852Hz • 963Hz\n"
                        "முருகன் • சிவன் • விநாயகர் frequencies\n"
                        "Rain • River • Forest soundscapes\n\n"
                        "New video added daily. Use headphones for binaural effect.\n"
                        "Subscribe: @aalayamani"
                    ),
                    "defaultLanguage": "ta",
                },
                "status": {"privacyStatus": "public"}
            }
        ).execute()
        pid = resp["id"]
        log(f"  ✅ Created sleep playlist: {pid}")
        _save_playlist_id(pid, playlist_file)
        return pid
    except Exception as e:
        log(f"  ⚠️ Playlist creation failed: {e}")
        return ""


def upload_sleep_video(video_path, thumb_path, profile):
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    yt = get_authenticated_service()
    if not yt:
        return None

    title       = profile['title'][:100]
    description = (
        f"{profile['description']}\n\n"
        f"🎵 {title}\n\n"
        f"⏰ 0:00 — শুরু (Start)\n"
        f"🔔 Subscribe: {CHANNEL_HANDLE}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"இந்த இசையை தினமும் படுக்கும் முன்பு கேளுங்கள்.\n"
        f"Use headphones for binaural beat effect.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 This music is mathematically generated using healing frequencies. "
        f"No copyright. Free to use.\n\n"
        f"#தூக்கஇசை #MeditationTamil #SleepMusic #{profile['freq1']:.0f}Hz "
        f"#BinauralBeats #HealingFrequency #TamilMeditation"
    )

    try:
        body = {
            "snippet": {
                "title":           title,
                "description":     description[:5000],
                "tags":            profile['tags'].split(','),
                "categoryId":      "22",
                "defaultLanguage": "ta",
            },
            "status": {
                "privacyStatus":           "public",
                "selfDeclaredMadeForKids": False,
                "embeddable":              True,
            }
        }
        media = MediaFileUpload(video_path, mimetype="video/mp4",
                                resumable=True, chunksize=5*1024*1024)
        req   = yt.videos().insert(part="snippet,status", body=body, media_body=media)

        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                log(f"  Upload: {int(status.progress()*100)}%")

        vid_id = resp['id']
        log(f"  ✅ Uploaded: https://youtu.be/{vid_id}")

        # Set thumbnail
        if thumb_path and os.path.exists(thumb_path):
            try:
                yt.thumbnails().set(
                    videoId=vid_id,
                    media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg")
                ).execute()
                log("  ✅ Thumbnail set")
            except: pass

        # Auto-create or find existing sleep playlist
        sleep_playlist_id = _get_or_create_sleep_playlist(yt)
        if vid_id and sleep_playlist_id:
            try:
                yt.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": sleep_playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": vid_id}
                        }
                    }
                ).execute()
                log(f"  ✅ Added to sleep playlist")
            except Exception as pe:
                log(f"  ⚠️ Playlist add failed: {pe}")

        return vid_id

    except HttpError as e:
        log(f"  ❌ Upload failed: {e}")
        return None



def process_sleep_music(upload=False, privacy="public", profile_key=None):
    """Generate and optionally upload today's sleep music video."""
    profile_key = profile_key or get_todays_profile()
    profile     = MUSIC_PROFILES[profile_key]

    log(f"\n🎵 Sleep Music: {profile_key}")
    log(f"   {profile['title'][:60]}...")

    # Ensure dirs
    for d in [SLEEP_OUTPUT_DIR, SLEEP_THUMBS_DIR, SLEEP_AUDIO_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

    # Step 1: Generate music
    audio = generate_music(profile_key, profile, SLEEP_VIDEO_DURATION)
    if not audio:
        log("❌ Sleep music generation failed"); return None

    # Step 2: Thumbnail
    try:
        thumb = generate_sleep_thumbnail(profile_key, profile)
    except Exception as e:
        log(f"  ⚠️ Sleep thumbnail failed: {e}"); thumb = None

    # Step 3: Create video
    video = create_sleep_video(audio, profile_key, profile)
    if not video:
        log("❌ Sleep video creation failed"); return None

    log(f"✅ Sleep video ready: {video}")

    # Step 4: Upload
    if upload:
        log("⬆️ Uploading sleep music...")
        vid_id = upload_sleep_video(video, thumb, profile)
        if vid_id:
            log(f"✅ Sleep music live: https://youtu.be/{vid_id}")
        return vid_id

    return video


def main():
    parser = argparse.ArgumentParser(
        description="ஆலய மணி — Fully Automated Devotional Content Bot v3.0"
    )
    parser.add_argument("--day",        help="Day: monday/tuesday/.../sunday/today/all")
    parser.add_argument("--topic",      help="Custom topic")
    parser.add_argument("--output",     default="custom", help="Output name for custom topic")
    parser.add_argument("--image",      default=IMAGE_FILE, help="Image file (fallback if no Pexels)")
    parser.add_argument("--bgm",        default=BGM_FILE,   help="BGM file")
    parser.add_argument("--bgm-volume", type=float, default=0.20, help="BGM volume")
    parser.add_argument("--upload",     action="store_true", help="Upload to YouTube after creation")
    parser.add_argument("--privacy",    default="public", choices=["public", "unlisted", "private"],
                        help="YouTube privacy setting")
    parser.add_argument("--daemon",         action="store_true", help="Run 24/7 scheduler")
    parser.add_argument("--trending",       action="store_true", help="Generate trending topic video")
    parser.add_argument("--upload-pending", action="store_true", help="Upload all pending")
    parser.add_argument("--sleep",            action="store_true", help="Generate sleep music video")
    parser.add_argument("--sleep-profile",    default=None,        help="Specific sleep profile")
    parser.add_argument("--auth-youtube",     action="store_true")
    parser.add_argument("--check-updates",    action="store_true",
                        help="Check old videos for outdated facts")
    parser.add_argument("--respond-comments", action="store_true",
                        help="Auto-reply to viewer comments")
    parser.add_argument("--analytics",        action="store_true",
                        help="Run analytics feedback loop")
    parser.add_argument("--community-post",   action="store_true",
                        help="Post to Community tab")
    args = parser.parse_args()

    check_prerequisites()
    ensure_dirs()

    print("\n========================================")
    print("  ஆலய மணி — Full Automation v3.0")
    print("  🎭 Deity voices  🪝 Varied hooks")
    print("  📸 Pexels images  📋 8 content formats")
    print("========================================")

    if args.auth_youtube:
        auth_youtube(); return

    if args.sleep:
        process_sleep_music(upload=args.upload, privacy=args.privacy,
                            profile_key=args.sleep_profile)
        return

    if args.check_updates:
        run_update_checks(); return

    if args.respond_comments:
        respond_to_comments(); return

    if args.analytics:
        run_analytics_loop(); return

    if args.community_post:
        post_community_content(); return

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
            "topic":    args.topic,
            "deity":    "",
            "deity_en": "",
            "emoji":    "🙏",
            "hashtags": "#ஆலயமணி #AalayaMani #TamilDevotional",
        }
        print("Fetching Pexels images...")
        images = get_images_for_deity("", args.output)
        if not images:
            images = find_images(args.image)

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
        video = create_video(script, images, args.output, args.bgm, args.bgm_volume)
        if video and args.upload:
            upload_to_youtube(video, metadata, args.privacy)
        return

    if args.day == "today":
        day = datetime.datetime.now().strftime("%A").lower()
        safe_process_day(day, args.image, args.bgm, args.bgm_volume, args.upload, args.privacy)
    elif args.day == "all":
        for day in DAY_DEITY_MAP:
            safe_process_day(day, args.image, args.bgm, args.bgm_volume,
                        args.upload, args.privacy)
    elif args.day in DAY_DEITY_MAP:
        safe_process_day(args.day, args.image, args.bgm, args.bgm_volume,
                    args.upload, args.privacy)
    elif args.day:
        print(f"Unknown day: {args.day}")
        print(f"Valid: {', '.join(DAY_DEITY_MAP.keys())}, today, all")
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




