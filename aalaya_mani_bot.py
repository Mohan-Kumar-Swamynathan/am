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

from utils.health import run_health_checks
from utils.logger import setup_logging
from analytics import (
    get_content_bias_prompt,
    load_analytics_insights as analytics_load_insights,
    run_analytics_loop as _run_analytics_loop,
)
from diversity import get_diversity_engine
from image_quality import validate_image_file
from retention import validate_retention, retention_prompt_rules
from media.image_pipeline import assemble_video_images, ImageAssemblyResult
from media.quality_gate import validate_video_ready
from media.tts_engine import generate_narration_audio, mix_voice_bgm_bell
from thumbnail_engine import generate_thumbnail as render_thumbnail
from thumbnail_engine import extract_thumbnail_text
from shorts_generator import generate_shorts_from_video, queue_shorts_for_upload
from watermark import apply_watermark

setup_logging()

def _topic_key(t):
    """Normalize topic for fuzzy comparison — strips punctuation, lowercase, 45 chars."""
    import re as _r
    return _r.sub(r'[^\w\s]', '', str(t).lower().strip())[:45]

def _is_duplicate_topic(new_topic, recent_topics, threshold=0.75):
    """Return True if new_topic is too similar to any recent topic."""
    new_key = _topic_key(new_topic)
    for old in recent_topics:
        old_key = _topic_key(old)
        if new_key == old_key:
            return True
        new_words = set(new_key.split())
        old_words = set(old_key.split())
        if new_words and old_words:
            overlap = len(new_words & old_words) / max(len(new_words), len(old_words))
            if overlap >= threshold:
                return True
    return False


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
PEXELS_API_KEY  = os.environ.get("PEXELS_API_KEY", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
CEREBRAS_KEY    = os.environ.get("CEREBRAS_API_KEY", "")

# ── 100% FREE LLM stack (no paid APIs, no credit card) ──
FREE_GROQ_MODEL       = "llama-3.3-70b-versatile"
FREE_GROQ_FAST_MODEL  = "llama-3.1-8b-instant"
FREE_GEMINI_MODEL     = "gemini-2.0-flash"
FREE_GEMINI_LITE      = "gemini-2.0-flash-lite"
FREE_GITHUB_MODEL     = "Llama-3.3-70B-Instruct"
FREE_CEREBRAS_MODEL   = "llama-3.3-70b"
GITHUB_MODEL_CANDIDATES = [
    "Llama-3.3-70B-Instruct",
    "gpt-4o-mini",
    "Meta-Llama-3.1-8B-Instruct",
]
GEMINI_MODEL_CANDIDATES = [
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
]

# Session-level: providers that hit quota — skip for rest of this run
_PROVIDER_EXHAUSTED: set = set()

def reset_llm_provider_state():
    """Clear provider cooldowns at the start of each pipeline run."""
    _PROVIDER_EXHAUSTED.clear()


def _mark_provider_exhausted(provider_name: str) -> None:
    """Gemini and Groq variants share quota within their provider family."""
    _PROVIDER_EXHAUSTED.add(provider_name)
    if provider_name in ("gemini", "gemini_fb"):
        _PROVIDER_EXHAUSTED.update({"gemini", "gemini_fb"})
    if provider_name in ("groq", "groq_fb"):
        _PROVIDER_EXHAUSTED.update({"groq", "groq_fb"})
    log(f"  ⏸️ {provider_name}: quota exhausted — skipping for rest of run")


def _is_provider_available(provider_name: str) -> bool:
    return provider_name not in _PROVIDER_EXHAUSTED


def _is_quota_exhausted_error(err_str: str) -> bool:
    lowered = err_str.lower()
    return any(token in lowered for token in [
        "resource_exhausted",
        "quota exceeded",
        "quotaexceeded",
        "exceeded your current quota",
        "rate limit exceeded",
        "too many requests",
    ])


def _select_gemini_models(provider_name: str, task: str | None, max_tokens: int) -> list:
    """Always prefer lite models — same quality for Tamil text, lower quota burn."""
    lite_models = [FREE_GEMINI_LITE, "gemini-2.0-flash"]
    if provider_name == "gemini_fb" or task in ("topic", "metadata", "small") or max_tokens <= 2000:
        return lite_models
    return lite_models

def has_free_llm_credentials():
    """True if at least one free LLM provider key is configured."""
    return bool(GROQ_API_KEY or GEMINI_KEY or GITHUB_TOKEN or CEREBRAS_KEY)

TARGET_MIN = 7000
TARGET_MAX = 10500
MAX_VIDEO_DURATION_SEC = 320
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
SLEEP_PLAYLIST_ID      = os.environ.get("SLEEP_PLAYLIST_ID", "")
SLEEP_MUSIC_ENABLED    = os.environ.get("SLEEP_MUSIC_ENABLED", "false").lower() in ("1", "true", "yes")
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"

# EQ profiles are defined in media/tts_engine.py — used there directly
# FEMALE_HUMANIZE / MALE_HUMANIZE removed from here to avoid stale duplicates

# ═══════════════════════════════════════════════════════════════
# FREE MEDIA: Wikimedia Commons + Pollinations AI
# ═══════════════════════════════════════════════════════════════

AM_WIKIMEDIA_QUERIES = {
    "முருகன்":   ["Palani Murugan temple hill Tamil Nadu", "Tiruchendur Murugan temple sea",
                  "Kartikeya vel spear sculpture bronze", "Kataragama Murugan devotees"],
    "சிவன்":    ["Nataraja bronze sculpture Chola Thanjavur museum", "Brihadisvara Thanjavur Shiva",
                  "Chidambaram Nataraja temple Tamil Nadu", "Shiva lingam abhishekam oil lamps"],
    "விநாயகர்": ["Ganesha Vinayaka Chaturthi festival procession Tamil Nadu",
                  "Ganesha idol decorated marigold flowers", "Pillayar sculpture Tamil Nadu stone"],
    "நடராஜர்":  ["Nataraja Chola bronze statue National Museum India",
                  "Chidambaram Nataraja cosmic dance", "Shiva Nataraja golden sculpture"],
    "ஐயப்பன்":  ["Sabarimala Ayyappa temple Kerala mountains", "Makaravilakku Kerala festival",
                  "Ayyappa devotees black dress pilgrimage crowd"],
    "அம்மன்":   ["Meenakshi Amman temple Madurai gopuram", "Mariamman goddess decorated temple",
                  "Amman goddess festival procession Tamil Nadu colorful"],
    "பெருமாள்": ["Srirangam Ranganathaswamy temple pillar hall", "Tirupati Balaji darshan queue",
                  "Vishnu Perumal temple decorated festival"],
    "கிருஷ்ணர்":["Guruvayur Krishna temple Kerala decorated",
                  "Krishna Janmashtami festival celebration Tamil Nadu", "Radha Krishna sculpture"],
    "லட்சுமி":  ["Lakshmi goddess gold coins lotus India", "Mahalakshmi Kolhapur temple",
                  "Lakshmi puja lamps Diwali India"],
    "சூரியன்":  ["Konark Sun temple chariot wheels UNESCO", "Modhera sun temple Gujarat India",
                  "Surya sunrise temple India ancient"],
    "default":  ["Hindu temple gopuram sunrise Tamil Nadu", "temple oil lamps aarti India",
                  "ancient Dravidian temple stone carvings dramatic"],
}

def fetch_wikimedia_images_am(deity_name, output_dir, count=4):
    """Fetch real temple/deity photos from Wikimedia Commons — truly free CC license."""
    import urllib.parse
    queries = AM_WIKIMEDIA_QUERIES.get(deity_name, AM_WIKIMEDIA_QUERIES["default"])
    images = []
    os.makedirs(output_dir, exist_ok=True)
    for query in queries[:4]:
        try:
            params = {
                "action": "query", "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrlimit": str(count * 4), "prop": "imageinfo",
                "gsroffset": str(__import__("random").randint(0, 10)),
                "iiprop": "url|size|mime", "iiurlwidth": "1920", "format": "json"
            }
            _wikiraw = requests.get("https://commons.wikimedia.org/w/api.php",
                               params=params, timeout=12)
            if _wikiraw.status_code != 200 or not _wikiraw.text.strip():
                continue
            resp = _wikiraw.json()
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
                            for chunk in r.iter_content(8192):
                                f.write(chunk)
                        if validate_image_file(fname):
                            images.append(fname)
                        elif os.path.exists(fname):
                            os.remove(fname)
                        if len(images) >= count:
                            return images
        except Exception as e:
            log(f"  ⚠️ Wikimedia: {e}")
    return images


def fetch_pollinations_image_am(deity_en, topic, output_path):
    """Free AI-generated unique image — no API key, no cost, unique per video."""
    import urllib.parse, random
    _deity_visuals = {
        "Murugan": ["Palani Murugan vel gold shrine close-up dramatic dark background",
                    "Tiruchendur Murugan temple ocean sunset devotees",
                    "Murugan peacock mount vel spear golden rays"],
        "Shiva":   ["Nataraja cosmic dance fire circle dramatic black background",
                    "Shiva lingam abhishekam milk golden lamp temple night",
                    "Annamalai hill fire beacon Tiruvannamalai night sky"],
        "Ganesha": ["Ganesha idol marigold garland close-up vibrant gold red",
                    "Vinayaka modak prasad golden light temple"],
        "Lakshmi": ["Lakshmi goddess lotus coins golden light glow divine",
                    "Mahalakshmi adorned jewelry Diwali lamps"],
        "Ayyappa": ["Sabarimala hill path pilgrims black dress dawn mist",
                    "Makaravilakku golden star light Kerala night"],
        "default": ["Hindu temple gopuram golden sunrise devotees Tamil Nadu",
                    "temple oil lamp flame night sacred India",
                    "ancient stone carvings temple dramatic light"],
    }
    _options = _deity_visuals.get(deity_en, _deity_visuals["default"])
    _style = random.choice(_options)
    prompt = (f"{_style}, photorealistic 8K HDR cinematic, no text no watermark, "
              f"golden hour dramatic lighting, intricate stone carvings, "
              f"devotees worship, cinematic wide shot, photorealistic 8K HDR, "
              f"no text no watermark")

    for attempt in range(2):
        seed = random.randint(1, 99999)
        url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
               f"?width=1920&height=1080&nologo=true&enhance=true&seed={seed}")
        try:
            r = requests.get(url, timeout=25, stream=True)
            if r.status_code == 200:
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                if validate_image_file(output_path):
                    log(f"  🎨 AI image generated: {os.path.basename(output_path)}")
                    return output_path
                if os.path.exists(output_path):
                    os.remove(output_path)
        except Exception as e:
            log(f"  ⚠️ Pollinations attempt {attempt + 1}: {e}")
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


WEEKDAY_UPLOAD_TIMES = [(6, 0), (18, 30)]
WEEKEND_UPLOAD_TIMES = [(7, 0), (19, 30)]

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
# ANTI-MONOTONY: CONTENT STRUCTURES
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
# ANTI-MONOTONY: CLOSING STYLES
# =============================================
CLOSING_STYLES = [
    "மந்திரம் + ஆசி: இந்த கடவுளின் மந்திரத்துடன் முடியுங்கள். கேட்பவருக்கு ஆசி கொடுங்கள். இயல்பாக subscribe சொல்லுங்கள்.",
    "21 நாள் சவால்: கேட்பவரை ஒரு குறிப்பிட்ட செயலை 21 நாட்கள் செய்ய சவால் விடுங்கள். 'நாளை முதல் செய்யுங்கள்...'",
    "NEXT VIDEO TEASE: ஒரு சுவாரஸ்யமான மர்மத்தை ஆரம்பித்துவிட்டு 'அதன் பதில் அடுத்த video-ல்...' என்று விடுங்கள்.",
    "FUTURE VISION: கேட்பவரின் வாழ்க்கை 6 மாதம் கழித்து எப்படி மாறியிருக்கும் என்று விவரியுங்கள் — இன்று இந்த வழிபாட்டை தொடங்கினால்.",
    "COMMUNITY: comments-ல் 'நீங்கள் எந்த பலனை அனுபவித்தீர்கள்?' என்று கேளுங்கள். பக்தர் சமுதாயத்தின் உணர்வை உருவாக்குங்கள்.",
]

# =============================================
# PROMPTS
# =============================================

SCRIPT_PROMPT = """நீங்கள் "ஆலய மணி" YouTube channel-க்கான expert Tamil script writer.
இலக்கியம், விஞ்ஞானம், வரலாறு — எல்லாவற்றையும் பக்தியுடன் கலந்து பேசுகிறீர்கள்.

கடவுள்: {deity} ({deity_en})
தலைப்பு: {topic}
format: {content_structure}
முடிவு style: {closing_style}

━━━━━━━━━━━━━━━━━━━━━━━━━
Script அமைப்பு:

பகுதி 1 — HOOK (0-15 வினாடி): உணர்ச்சி, கேள்வி, அல்லது ஆச்சர்ய உண்மையுடன் தொடங்குங்கள்
  ❌ "வணக்கம்" அல்லது கடவுள் பெயரில் தொடங்க வேண்டாம்
  ✅ "இந்த உண்மை உங்களுக்கு தெரியுமா?" அல்லது "ஒரு ஆச்சர்ய கண்டுபிடிப்பு..."

பகுதி 2 — உள்ளடக்கம் (80%): ஆழமாக, விரிவாக, குறிப்பிட்ட facts உடன்
  - ஒவ்வொரு 45 வினாடிக்கும் ஒரு புதிய angle அல்லது character
  - குறிப்பிட்ட facts: exact mantra counts, கோவில் பெயர்கள், திருவிழா நாட்கள்
  - விஞ்ஞானம் + ஆன்மீகம் இணைப்பு கட்டாயம் (ஒவ்வொரு video-லும்):
    உதாரணங்கள்: விரதம் + intermittent fasting, பூஜை நேரம் + circadian rhythm
  - நடுவில் retention hook (~2:30 mark): "ஆனால் இதில் ஒரு ரகசியம் இருக்கிறது..."

பகுதி 3 — முடிவு + CTA (கடைசி 20%):
  - ஒரு emotional close
  - "இந்த வீடியோ பயனுள்ளதாக இருந்தால் லைக் செய்யுங்கள்"
  - "சந்தா செய்யாதவர்கள் இப்போதே செய்யுங்கள் — மணி சின்னம் அழுத்துங்கள்"
  - கடைசி வரி மட்டும்: "ஆலய மணி சேனலில் தினமும் இதுபோன்ற கதைகள் — சந்தா செய்யுங்கள்"

━━━━━━━━━━━━━━━━━━━━━━━━━
PAUSE MARKERS (கட்டாயம்):
- hook reveal-க்கு பிறகு: [PAUSE_LONG]
- முக்கியமான fact-க்கு பிறகு: [PAUSE_SHORT]
- கேள்விக்கு முன்: [PAUSE_MED]

கட்டாய விதிகள்:
1. 100% தமிழ் மட்டும் — ஒரே ஒரு ஆங்கில வார்த்தை கூட வேண்டாம்
   "subscribe" → "சந்தா செய்யுங்கள்", "like" → "லைக்", "channel" → "சேனல்"
   "scientific" → "விஞ்ஞான", "history" → "வரலாறு", "temple" → "கோவில்"
2. bullet points, numbers, headers, markdown வேண்டாம் — தொடர்ந்த பேச்சு மட்டும்
3. {target_words} தமிழ் வார்த்தைகள்"""

TRENDING_PROMPT = """நீங்கள் "ஆலய மணி" YouTube channel-க்கான content strategist.
இன்றைய திருவிழா, நட்சத்திரம், மாதம் பார்த்து — மிகவும் பார்வையாளர்களை கவரும் topic தேர்வு செய்யுங்கள்.

இன்று: {date} ({day})
தமிழ் மாதம்: {tamil_month} — {month_trend}
வரவிருக்கும் திருவிழாக்கள்: {festivals}
இன்றைய கடவுள் (நாள் அடிப்படையில்): {today_deity}

உங்கள் வேலை:
1. இன்று அல்லது 2 நாட்களில் பெரிய திருவிழா இருந்தால் — அந்த திருவிழா topic
2. 3-7 நாட்களில் திருவிழா இருந்தால் — preparation/preview topic
3. ஜோதிட நிகழ்வு இருந்தால் — அந்த topic
4. எதுவும் இல்லையென்றால் — proven viral topic

கட்டாய விதிகள்:
- Topic specific-ஆக இருக்கவேண்டும், generic இல்லை
- YouTube title-ல் பார்க்கப்படும் style-ல் இருக்கவேண்டும்
- 100% தமிழில் இருக்கவேண்டும் (proper nouns மட்டும் English)
- ஒரு எண் சேர்த்தால் நல்லது (7 பலன்கள், 5 ரகசியங்கள்)
- "N நாட்கள் செய்தால் பலன்கள்" — இந்த template வேண்டவே வேண்டாம்

Topic string மட்டும் return செய்யுங்கள், வேறு எதுவும் வேண்டாம்."""

DAILY_TOPIC_PROMPT = """நீங்கள் "ஆலய மணி" YouTube channel-க்கான content strategist.
பக்தி content — கோவில் கதைகள், கடவுள் legends, விஞ்ஞானம், வரலாறு — தருகிறீர்கள்.

இன்று: {date} | {day}
திருவிழா: {festival_context}
சமீபத்தில் பயன்படுத்தியவை (இவற்றை தவிருங்கள்): {recent_topics}

━━━━━━━━━━━━━━━━━━━━━━━━━
இன்றைய வகை (இதை கட்டாயம் பின்பற்றுங்கள்):
{category_today}
━━━━━━━━━━━━━━━━━━━━━━━━━

வகைகள்:
1. கோவில் மர்மம் — ஒரு குறிப்பிட்ட கோவிலின் acoustic/architecture/விஞ்ஞான அதிசயம்
   உதாரணம்: "சிதம்பரம் கோவிலில் 432Hz frequency — விஞ்ஞானிகள் ஆச்சர்யப்பட்டது ஏன்?"

2. கடவுள் கதை — கடவுளின் வாழ்க்கையில் குறிப்பிட்ட ஒரு நிகழ்வு, பெயர் கொண்ட characters உடன்
   உதாரணம்: "முருகன் திருப்பரங்குன்றம் வந்தது ஏன்? சூரன் தோல்வியின் உண்மை கதை"

3. திருவிழா விஞ்ஞானம் — திருவிழாவின் பின்னே உள்ள விஞ்ஞான அல்லது வரலாற்று காரணம்
   உதாரணம்: "தைப்பூசம் ஏன் ஜனவரியில் மட்டும்? சூரியன்-சந்திரன் alignment ரகசியம்"

4. நம்பிக்கை vs உண்மை — பொதுவான நம்பிக்கை vs ஆராய்ச்சி உண்மை
   உதாரணம்: "கோலம் 'lucky' என்பது வெறும் நம்பிக்கையா? IITM ஆராய்ச்சி சொல்வது வேறு"

5. கோவில் வரலாறு — குறிப்பிட்ட கோவில் யார் கட்டினார்? எப்போது? ஏன்?
   உதாரணம்: "ராஜராஜ சோழன் தஞ்சை கோவில் கட்ட 30,000 பேர் — 16 வருடங்கள்"

6. ஆன்மீக விஞ்ஞானம் — பூஜை/மந்திரம்/எண்களின் விஞ்ஞான அடிப்படை
   உதாரணம்: "108 என்ற எண் ஏன்? சூரியன் விட்டம் ÷ சூரியன்-பூமி தூரம் = 108"

7. சித்தர் கதை — Tamil சித்தர் அல்லது saint-ன் குறிப்பிட்ட வாழ்க்கை நிகழ்வு
   உதாரணம்: "திருநாவுக்கரசர் சிறையில் பட்ட 10 நாட்கள் — அவரை காத்தது யார்?"

8. மறைக்கப்பட்ட கோவில் — குறைவாக அறியப்பட்ட, வரலாற்று முக்கியத்துவம் உள்ள கோவில்
   உதாரணம்: "கர்நாடகாவில் 1200 வருட பழமையான தமிழ் கோவில் — யாரும் போகாதது ஏன்?"

━━━━━━━━━━━━━━━━━━━━━━━━━
🚫 கட்டாயம் தவிர்க்க வேண்டியவை:
- "X-க்கு N நாட்கள் Y செய்தால் பலன்கள்" — இந்த வடிவம் வேண்டவே வேண்டாம்
- "N தடவை அபிஷேகம் செய்தால்" — வேண்டாம்
- "இன்றைய சிறப்பு பலன்கள்" — வேண்டாம்
- நேற்றைய கடவுளின் அதே topic

✅ கட்டாயம் சேர்க்க வேண்டியவை:
- ஒரு குறிப்பிட்ட verifiable fact (எண், தேதி, இடம், நபர் பெயர்)
- ஒரு "ஏன்?" கேள்வி அல்லது ஆச்சர்ய கோணம்

JSON மட்டும் return செய்யுங்கள்:
{{"topic": "...", "category": 1-8, "deity": "...", "deity_en": "...", "reason": "..."}}"""

TITLE_PROMPT = """ஒரு தமிழ் பக்தி YouTube வீடியோவிற்கு தலைப்பு உருவாக்குக.
தலைப்பு: {topic}
கடவுள்: {deity} ({deity_en})
Emoji: {emoji}

வடிவம்: [தமிழ் நாள் + கடவுள் + முக்கிய பலன்] {emoji} [கவர்ச்சியான வரி] | ஆலய மணி

உதாரணம்: செவ்வாய் முருகன் விரதம் 7 பலன்கள் 🔱 வாழ்க்கையே மாறும் | ஆலய மணி

விதிகள்:
- தலைப்பு முழுவதும் தமிழில் மட்டும் — ஆங்கில வார்த்தைகள் வேண்டாம்
- 60 எழுத்துகளுக்கு குறைவாக இருக்கவேண்டும்
- கவர்ச்சியாகவும் பக்தி உணர்வோடும் இருக்கட்டும்
- "| ஆலய மணி" என்று முடிக்கவும்

Example: செவ்வாய் முருகன் விரதம் 7 பலன்கள் 🔱 வாழ்க்கையே மாறும் | ஆலய மணி

Give ONLY the title, nothing else."""

DESC_PROMPT = """Tamil devotional YouTube video-க்கு description உருவாக்குங்கள்.
கடவுள்: {deity} ({deity_en})
தலைப்பு: {topic}
hashtags: {hashtags}

இந்த அமைப்பில் எழுதுங்கள்:
- முதல் வரி: hook கேள்வி அல்லது ஆச்சர்ய உண்மை (YouTube search-ல் காட்டப்படும்)
- emoji உடன் topic வரி
- 5 நன்மைகள் (தமிழில், emoji உடன்)
- "தினமும்/வாரம் கேளுங்கள்" அறிவுரை
- லைக், சந்தா, கருத்து CTA (தமிழில் மட்டும்)
- Email: aalayamani.official@gmail.com
- chapters timestamps
- hashtags: #ஆலயமணி #AalayaMani {hashtags} #தமிழ்பக்தி

100% தமிழ் மட்டும். "subscribe", "like", "share", "comment" ஆங்கிலத்தில் வேண்டாம்.
"சந்தா செய்யுங்கள்", "லைக் செய்யுங்கள்", "பகிருங்கள்", "கருத்து சொல்லுங்கள்" என்று எழுதுங்கள்."""

TAGS_PROMPT = """Generate YouTube tags (comma separated) for Tamil devotional video.
Topic: {topic}
Deity: {deity} ({deity_en})

CRITICAL: ALL tags MUST be ASCII English ONLY. YouTube API rejects Tamil script tags with HTTP 400.
Generate 25-30 English transliteration tags.

Include: deity name transliteration, temple name, day of week, benefit, aalaya mani, tamil devotional 2026, worship.
Example: murugan, thaipusam, palani temple, tuesday puja, tamil devotional, kavadi, vel murugan

Give ONLY comma-separated ASCII tags. Zero Tamil script."""

PINNED_PROMPT = """Tamil devotional YouTube video-க்கு pinned comment உருவாக்குங்கள்.
கடவுள்: {deity} | தலைப்பு: {topic}

அமைப்பு:
- பார்வையாளர்களிடம் கேளுங்கள்: "இந்த {deity} ஆசி உங்களுக்கு தேவையா?" (1-5 options தமிழில்)
- "உங்கள் விருப்பத்தை கீழே comment பண்ணுங்கள்" என்று கேளுங்கள்
- சந்தா CTA: "சந்தா செய்யாதவர்கள் மணி சின்னம் 🔔 அழுத்துங்கள்"
- கடைசியில் ஒரு mantra அல்லது blessing

500 எழுத்துகளுக்கு குறைவாக இருக்கட்டும். 100% தமிழ் மட்டும்."""


# =============================================
# PEXELS IMAGE FETCHING
# =============================================

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
    queries = list(queries)
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
                img_url = photo["src"]["large2x"]
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
                        if validate_image_file(fname):
                            downloaded.append(fname)
                            log(f"  📸 Downloaded: {os.path.basename(fname)} ({query})")
                        elif os.path.exists(fname):
                            os.remove(fname)
                except Exception as e:
                    log(f"  ⚠️ Image download failed: {e}")

        except Exception as e:
            log(f"  ⚠️ Pexels query failed ({query}): {e}")

    log(f"  ✅ Pexels: {len(downloaded)} images fetched for {deity or 'generic'}")
    return downloaded


def get_images_for_deity(deity, day_or_name):
    """Returns a list of image paths for video creation."""
    pexels_dir = os.path.join(PEXELS_DIR, day_or_name)
    images = fetch_pexels_images(deity, pexels_dir, count=6)

    if images:
        return images

    if os.path.isdir("images"):
        exts = (".png", ".jpg", ".jpeg", ".webp")
        local = [os.path.join("images", f) for f in sorted(os.listdir("images"))
                 if f.lower().endswith(exts)]
        if local:
            log(f"  📁 Using {len(local)} local images from images/")
            return local[:6]

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


def get_dur_float(media_path):
    """Return media duration in seconds (float)."""
    result = run(["ffprobe", "-v", "error", "-show_entries",
                  "format=duration", "-of", "csv=p=0", media_path])
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _ffmpeg_timeout(duration_sec, multiplier=2.5, floor=600, ceiling=3600):
    """Scale ffmpeg subprocess timeout with media duration."""
    return min(ceiling, max(floor, int(float(duration_sec) * multiplier)))


def _trim_audio_if_too_long(audio_path, output_name, max_seconds=MAX_VIDEO_DURATION_SEC):
    """Cap narration length so CI encode stays within workflow timeout."""
    duration = get_dur_float(audio_path)
    if duration <= max_seconds:
        return audio_path, duration

    trimmed_path = f"/tmp/{output_name}_trimmed.mp3"
    trim_result = run([
        "ffmpeg", "-y", "-i", audio_path,
        "-t", f"{max_seconds:.3f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        trimmed_path,
    ], timeout=120)
    if trim_result.returncode == 0 and os.path.exists(trimmed_path):
        log(f"  ✂️ Trimmed audio {duration:.0f}s → {max_seconds}s (5-min target)")
        return trimmed_path, float(max_seconds)
    log(f"  ⚠️ Audio trim failed — using full {duration:.0f}s")
    return audio_path, duration


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
    run_health_checks(require_llm=True)
    if not PEXELS_API_KEY:
        log("WARNING: PEXELS_API_KEY not set — Wikimedia/scenes used as primary")
    ensure_images()
    ensure_bgm()


DEITY_BGM_FREQ = {
    "சிவன்":    ("136.1", "272.2"),
    "முருகன்":  ("174.0", "348.0"),
    "விநாயகர்": ("528.0", "264.0"),
    "பெருமாள்": ("432.0", "216.0"),
    "லட்சுமி":  ("417.0", "208.5"),
    "ஐயப்பன்":  ("396.0", "198.0"),
    "சூரியன்":   ("285.0", "570.0"),
    "நடராஜர்":  ("136.1", "272.2"),
    "கிருஷ்ணர்": ("528.0", "264.0"),
    "அம்மன்":   ("417.0", "208.5"),
    "":          ("174.0", "348.0"),
}

def ensure_bgm(deity=""):
    """Generate deity-specific copyright-free BGM if not found."""
    bgm_path = f"bgm_{deity or 'generic'}.mp3" if deity else BGM_FILE
    if os.path.exists(bgm_path):
        return bgm_path
    if deity and os.path.exists(BGM_FILE):
        pass
    log(f"🎵 Generating devotional BGM for {deity or 'generic'}...")
    freq1, freq2 = DEITY_BGM_FREQ.get(deity, DEITY_BGM_FREQ[""])
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
        run(["ffmpeg", "-y", "-f", "lavfi",
             "-i", "anoisesrc=d=360:c=pink:r=44100:a=0.015",
             "-af", "lowpass=f=500,volume=0.2", bgm_path], timeout=60)
        return bgm_path if os.path.exists(bgm_path) else BGM_FILE


def ensure_dirs():
    for d in [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR, PEXELS_DIR]:
        os.makedirs(d, exist_ok=True)


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


CROSS_PROMO = (
    "\n\n📺 எங்கள் மற்ற சேனல்கள்:\n"
    "🌱 உண்மை கதைகள் → youtube.com/@thulirstories\n"
    "💰 நிதி உதவி → youtube.com/@nidhineethi"
)


def load_recent_topics(n=60):
    """Load recently used topics — rolling 60-topic window for rotation.
    Topics older than 60 entries are eligible to reuse (prevents exhaustion).
    """
    topics = []
    if os.path.exists(USED_TOPICS_FILE):
        with open(USED_TOPICS_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        # Keep only last 60 — topics before that can rotate back
        topics = lines[-n:]
    return topics


def deduplicate_topic(topic):
    """Semantic dedup — reject if too similar to recent topics."""
    import re as _re
    used = load_recent_topics(60)

    # Exact match → reject
    if topic in used:
        log(f"  🚫 Exact duplicate rejected: {topic[:60]}")
        return None

    # Semantic match — check Tamil keyword overlap
    STOP_WORDS = {
        'ஏற்படும்', 'பலன்கள்', 'செய்தால்', 'நன்மைகள்', 'வழிபாடு',
        'கோவிலில்', 'தினமும்', 'ஆகும்', 'என்பது', 'சிறப்பு',
        'முக்கியம்', 'தெரியும்', 'வேண்டும்', 'உள்ளது', 'இந்த'
    }
    topic_kw = set(_re.findall(r'[\u0B80-\u0BFF]{4,}', topic)) - STOP_WORDS

    for used_topic in used[-30:]:
        used_kw = set(_re.findall(r'[\u0B80-\u0BFF]{4,}', used_topic)) - STOP_WORDS
        overlap = topic_kw & used_kw
        if len(overlap) >= 3:
            log(f"  🚫 Semantic duplicate (overlap={list(overlap)[:3]}): {topic[:50]}")
            return None

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
    LLM decides BOTH deity AND topic based on day, festivals, trends.
    Returns a full config dict ready for safe_process_day().
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

    # Category rotation — day of year mod 8 so we cycle all 8 types
    _categories = [
        "1. TEMPLE MYSTERY — கோவிலின் acoustic/architecture/science அதிசயம்",
        "2. DEITY LEGEND — கடவுளின் வாழ்க்கையில் குறிப்பிட்ட ஒரு நிகழ்வு (named characters)",
        "3. FESTIVAL SCIENCE — திருவிழாவின் scientific அல்லது historical reason",
        "4. MYTH BUSTING — பொதுவான நம்பிக்கை vs ஆராய்ச்சி உண்மை",
        "5. TEMPLE HISTORY — குறிப்பிட்ட கோவில் கட்டிய வரலாறு (ruler + year + reason)",
        "6. SPIRITUAL SCIENCE — பூஜை/மந்திரம்/எண்கள்-ன் scientific basis",
        "7. SAINT STORY — Tamil saint/siddhar-ன் குறிப்பிட்ட வாழ்க்கை நிகழ்வு",
        "8. HIDDEN TEMPLE — குறைவாக அறியப்பட்ட historical significance உள்ள கோவில்",
    ]
    _cat = _categories[now.timetuple().tm_yday % 8]

    prompt = DAILY_TOPIC_PROMPT.format(
        festival_context=festival_ctx,
        date=now.strftime("%Y-%m-%d"),
        day=day_name,
        recent_topics=", ".join(recent_topics[-10:]) if recent_topics else "None yet",
        category_today=_cat,
    )
    if recent_topics:
        prompt += (
            f"\n\nRECENTLY USED TOPICS (avoid repeating): "
            + ", ".join(recent_topics[-5:])
        )

    analytics_bias = get_content_bias_prompt()
    if analytics_bias:
        prompt += f"\n\n{analytics_bias}"

    try:
        raw = call_llm_free(prompt, task="topic", max_tokens=1000)
    except Exception as llm_error:
        log(f"  ⚠️ LLM topic failed ({str(llm_error)[:120]}) — using day default")
        raw = json.dumps({
            "deity": default["deity"],
            "deity_en": default["deity_en"],
            "topic": f"{default['deity']} வழிபாடு — இன்றைய சிறப்பு பலன்கள்",
            "reason": "offline fallback",
        })

    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"): clean = clean[4:]
        data = json.loads(clean.strip())
        deity    = data.get("deity", default["deity"])
        deity_en = data.get("deity_en", default["deity_en"])
        _raw_topic = data.get("topic", "")
        topic    = deduplicate_topic(_raw_topic)
        if topic is None:
            log(f"  🔄 Semantic duplicate — using day-specific variant")
            topic = f"{deity} — {data.get('deity_en', deity)} {now.strftime('%d %b')} சிறப்பு"
        reason   = data.get("reason", "")
        log(f"  🎯 Deity: {deity} ({deity_en})")
        log(f"  📌 Topic: {topic}")
        log(f"  💡 Reason: {reason}")
    except Exception as e:
        log(f"  ⚠️ JSON parse failed ({e}) — using day default")
        deity    = default["deity"]
        deity_en = default["deity_en"]
        # Category-aware fallback (no generic date topics)
        _cat2 = now.timetuple().tm_yday % 8
        _fallback2_map = [
            f"{deity} கோவிலின் மர்மம் — விஞ்ஞானிகள் ஆச்சர்யப்பட்டது",
            f"{deity}-ன் அவதார கதையில் மறைக்கப்பட்ட நிகழ்வு",
            f"{deity} திருவிழாவின் scientific reason",
            f"{deity} பூஜையில் இந்த தவறு செய்கிறீர்களா?",
            f"{deity} கோவில் கட்ட எத்தனை வருடம் ஆனது?",
            f"108 விளக்கு ஏற்றுவதன் உண்மையான அர்த்தம்",
            f"திருஞானசம்பந்தர் {deity} பற்றி பாடிய கதை",
            f"தமிழ்நாட்டில் இந்த {deity} கோவில் மட்டும் ஏன் தனித்துவமானது?",
        ]
        _raw = _fallback2_map[_cat2]
        topic = deduplicate_topic(_raw) or _raw

    diversity_engine = get_diversity_engine()
    if not diversity_engine.is_topic_allowed(topic):
        # Use category-aware fallback — never generic date topic
        _fallback_topics = {
            0: f"{deity} கோவிலின் acoustic அதிசயம் — விஞ்ஞானம் என்ன சொல்கிறது?",
            1: f"{deity}-ன் வாழ்க்கையில் இந்த நிகழ்வு யாரும் அறியாதது",
            2: f"{deity} திருவிழாவின் பின்னே உள்ள historical reason",
            3: f"{deity} வழிபாட்டில் இந்த நம்பிக்கை தவறு — உண்மை என்ன?",
            4: f"{deity} கோவில் யார் கட்டினார்? உண்மை வரலாறு",
            5: f"108 முறை {deity} நாமம் சொல்வதன் scientific basis என்ன?",
            6: f"{deity}-ஐ கண்ட saint-ன் அந்த தருணம் — உண்மை கதை",
            7: f"தமிழ்நாட்டில் யாரும் போகாத {deity} கோவில் — ஏன் சிறப்பு?",
        }
        _cat_idx = now.timetuple().tm_yday % 8
        _raw3 = _fallback_topics.get(_cat_idx, f"{deity} — {deity_en} unique story")
        topic = deduplicate_topic(_raw3) or _raw3
        log(f"  🔁 Topic rotated for diversity: {topic[:70]}")

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
LAST_GENERATED_HOOK_STYLE = ""
LAST_GENERATED_FORMAT_NAME = ""


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


def generate_script(topic, deity=""):
    t0 = time.time()

    deity_voice = DEITY_VOICE.get(deity, (
        "இயல்பான, அன்பான, பக்தி மிகுந்த குரலில் பேசுங்கள். "
        "கேட்பவர் ஒரு நேசமான நண்பரிடம் பேசுவதுபோல் உணரட்டும்."
    ))
    hook_usage   = load_usage(HOOK_USAGE_FILE)
    format_usage = load_usage(FORMAT_USAGE_FILE)
    diversity_engine = get_diversity_engine()
    hook_style,   hook_usage   = diversity_engine.pick_least_used(HOOK_STYLES, hook_usage, lambda x: x.split(':')[0])
    content_struct, format_usage = diversity_engine.pick_least_used(CONTENT_STRUCTURES, format_usage, lambda x: x['name'])
    closing_style = random.choice(CLOSING_STYLES)
    save_usage(HOOK_USAGE_FILE,   hook_usage)
    save_usage(FORMAT_USAGE_FILE, format_usage)

    log(f"  🎭 Deity voice: {deity or 'generic'}")
    log(f"  🪝 Hook style: {hook_style.split(':')[0]}")
    log(f"  📋 Content structure: {content_struct['name']}")
    log(f"  🎬 Closing style: {closing_style.split(':')[0]}")

    def build_prompt(attempt=0, shortfall=0):
        note = ""
        if attempt > 0:
            note = (
                f"\n\n⚠️ ATTEMPT {attempt + 1} — CRITICAL LENGTH REQUIREMENT:\n"
                f"Previous response was only ~{shortfall} chars. You MUST write at least 7000 Tamil characters "
                f"(approximately 1400-1600 Tamil words) for a 5-minute video.\n"
                "Write LONG, detailed sections — 5-6 full sentences per topic point. "
                "Do NOT summarize. Do NOT stop early. Include [PAUSE_LONG], [PAUSE_MED], [PAUSE_SHORT] throughout.\n"
                "NO வணக்கம்/வரவேற்பு in the first 2 sentences — start with a curiosity hook."
            )
        return SCRIPT_PROMPT.format(
            topic=topic,
            deity_voice=deity_voice,
            hook_style=hook_style,
            content_structure=content_struct["instruction"],
            closing_style=closing_style,
        ) + note + "\n\n" + retention_prompt_rules()

    def _finalize_script(raw_text):
        trimmed = raw_text.strip()
        if len(trimmed) > TARGET_MAX:
            log(f"  Script too long ({len(trimmed)} chars) — trimming to 5 min...")
            cut = trimmed[:TARGET_MAX]
            for punct in [".\n", ". ", "\n\n"]:
                idx = cut.rfind(punct)
                if idx > TARGET_MIN:
                    cut = cut[:idx + 1]
                    break
            trimmed = cut
            log(f"  Trimmed to {len(trimmed)} chars")
        return trimmed

    text = ""
    hook_key = hook_style.split(":")[0]
    llm_failed = False
    script_max_tokens = 8192

    for attempt in range(2):
        try:
            resp = call_llm_free(
                build_prompt(attempt, shortfall=len(text) if text else 0),
                task="script",
                max_tokens=script_max_tokens,
                prefer="github",
            )
        except Exception as llm_error:
            llm_failed = True
            log(f"  ⚠️ LLM script failed (attempt {attempt + 1}): {str(llm_error)[:120]}")
            break

        candidate = resp.strip()
        chars = len(candidate)
        log(f"  Attempt {attempt+1}: {chars} chars")

        if chars < TARGET_MIN:
            text = candidate
            log(f"  Too short ({chars} < {TARGET_MIN}) — need longer script")
            if attempt < 1:
                time.sleep(8)
            continue

        retention_report = validate_retention(candidate)
        log(f"  Retention score: {retention_report.score}/100")
        for warning in retention_report.warnings:
            log(f"  ⚠ Retention: {warning}")
        if not retention_report.passed:
            for failure in retention_report.failures:
                log(f"  ❌ Retention: {failure}")
            if attempt < 1:
                log("  Retention weak but length OK — regenerating once...")
                time.sleep(8)
                continue

        text = candidate
        if not retention_report.passed:
            log("  ⚠️ Retention below target — using script anyway (length OK)")
        break

    if len(text.strip()) < TARGET_MIN and not llm_failed and _any_llm_provider_available(task="script"):
        log("  ⚠️ Trying two-part script generation (better for token limits)...")
        try:
            split_script = _generate_script_in_two_parts(topic, deity)
            split_chars = len(split_script.strip())
            log(f"  Two-part result: {split_chars} chars")
            if split_chars >= TARGET_MIN:
                text = split_script.strip()
                log(f"  ✅ Two-part script accepted")
            else:
                log(f"  Two-part too short ({split_chars} < {TARGET_MIN})")
        except Exception as split_error:
            log(f"  ⚠️ Two-part script failed: {str(split_error)[:120]}")

    if len(text.strip()) < TARGET_MIN and not llm_failed and _any_llm_provider_available(task="script"):
        log("  ⚠️ Trying compact script prompt (smaller context)...")
        try:
            compact = call_llm_free(
                _build_compact_script_prompt(
                    topic, deity, deity_voice, hook_style,
                    content_struct["instruction"], closing_style,
                ),
                task="script",
                max_tokens=8192,
                prefer="github",
            )
            if len(compact.strip()) >= TARGET_MIN:
                text = compact.strip()
                log(f"  ✅ Compact prompt script: {len(text)} chars")
            else:
                log(f"  Compact too short: {len(compact.strip())} chars")
        except Exception as compact_error:
            log(f"  ⚠️ Compact script failed: {str(compact_error)[:120]}")

    if len(text.strip()) < TARGET_MIN and GITHUB_TOKEN and _is_provider_available("github"):
        log("  ⚠️ Trying GitHub Models dedicated script fallback...")
        try:
            github_script = call_llm_free(
                build_prompt(0, shortfall=len(text)),
                task="script",
                max_tokens=script_max_tokens,
                prefer="github",
            )
            github_chars = len(github_script.strip())
            log(f"  GitHub fallback: {github_chars} chars")
            if github_chars >= TARGET_MIN:
                text = github_script.strip()
                log(f"  ✅ GitHub Models script accepted")
        except Exception as github_error:
            log(f"  ⚠️ GitHub script fallback failed: {str(github_error)[:120]}")

    if len(text.strip()) < TARGET_MIN:
        log("  ⚠️ LLM scripts too short — using offline fallback script")
        text = _build_fallback_script(topic, deity)

    text = _finalize_script(text)

    if len(text.strip()) < 100:
        log("  ❌ Script generation failed — all attempts returned empty")
        return ""
    log(f"  Script generated ({len(text)} chars) in {time.time()-t0:.0f}s")
    global LAST_GENERATED_HOOK_STYLE, LAST_GENERATED_FORMAT_NAME
    LAST_GENERATED_HOOK_STYLE = hook_key
    LAST_GENERATED_FORMAT_NAME = content_struct["name"]
    return text


def _build_compact_script_prompt(topic, deity, deity_voice, hook_style, content_structure, closing_style):
    """Shorter prompt for providers with smaller context windows."""
    return (
        f"ஆலய மணி YouTube — 5 நிமிட Tamil devotional script.\n"
        f"Topic: {topic}\nDeity: {deity or 'கடவுள்'}\n"
        f"Voice: {deity_voice[:200]}\nHook: {hook_style[:120]}\n"
        f"Structure: {content_structure[:200]}\nClosing: {closing_style[:120]}\n\n"
        "Write 1400-1600 Tamil words (minimum 7000 characters). Natural speech. "
        "Use [PAUSE_LONG], [PAUSE_MED], [PAUSE_SHORT]. "
        "NO வணக்கம்/வரவேற்பு in first 2 sentences. "
        "No bullets/markdown. Real temple names. End with subscribe CTA.\n\n"
        + retention_prompt_rules()
    )


def _generate_script_in_two_parts(topic, deity):
    """Split script generation to stay within provider output token limits."""
    deity_label = deity or "கடவுள்"
    part_one_prompt = (
        f"ஆலய மணி YouTube — Tamil devotional script PART 1 of 2.\n"
        f"Topic: {topic}\nDeity: {deity_label}\n\n"
        "Write PART 1 ONLY — minimum 3500 Tamil characters (~700-800 words):\n"
        "- Strong curiosity hook (NO வணக்கம்/வரவேற்பு in first 2 sentences)\n"
        "- Background, temple context, first half of main story\n"
        "- Use [PAUSE_LONG], [PAUSE_MED], [PAUSE_SHORT] every few sentences\n"
        "- Natural spoken Tamil, no bullets/headers\n"
        "Do NOT write the ending or subscribe CTA yet."
    )
    first_half = call_llm_free(part_one_prompt, task="script_part", max_tokens=4500, prefer="github")
    part_two_prompt = (
        f"ஆலய மணி YouTube — Tamil devotional script PART 2 of 2.\n"
        f"Topic: {topic}\nDeity: {deity_label}\n\n"
        "Continue PART 2 ONLY — minimum 3500 Tamil characters (~700-800 words):\n"
        "- Do NOT repeat part 1 — continue the narrative\n"
        "- Pariharam steps, benefits, devotee experiences\n"
        "- Emotional close + subscribe CTA in final 20%\n"
        "- Use [PAUSE_LONG], [PAUSE_MED], [PAUSE_SHORT] throughout\n"
        f"Context from part 1 (do not rewrite): {first_half.strip()[:400]}..."
    )
    second_half = call_llm_free(part_two_prompt, task="script_part", max_tokens=4500, prefer="github")
    return f"{first_half.strip()}\n\n{second_half.strip()}".strip()


def _sanitize_llm_json(raw_text: str) -> str:
    """Strip markdown fences and control chars that break json.loads."""
    import re as _re

    clean = raw_text.strip()
    for fence in ["```json", "```JSON", "```"]:
        if clean.startswith(fence):
            clean = clean[len(fence):]
            break
    if "```" in clean:
        clean = clean[:clean.rfind("```")]
    clean = clean.strip()
    if not clean.startswith("{"):
        match = _re.search(r"\{[\s\S]+\}", clean)
        if match:
            clean = match.group(0)
    return _re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", clean)


def _build_fallback_script(topic, deity=""):
    """Offline Tamil script when every LLM provider is unavailable."""
    deity_label = deity or "கடவுள்"
    paragraphs = [
        f"நமஸ்காரம்! [PAUSE_MED] இன்று {deity_label} பற்றிய {topic} — இந்த வீடியோ உங்கள் வாழ்க்கையில் நிஜமான மாற்றத்தை கொண்டுவரும். [PAUSE_LONG]",
        f"பலர் {deity_label} அருளை பெற விரும்புகிறார்கள், ஆனால் சரியான வழிபாடு முறை தெரியாமல் கவலைப்படுகிறார்கள். [PAUSE_MED] இன்று அந்த குழப்பம் நீங்கும்.",
        f"பழங்கால Tamil Nadu கோயில்களில் {deity_label} bhaktas கடைப்பிடித்த ஒரு மறைபொருள் இருக்கிறது. [PAUSE_SHORT] அது வெறும் ritual அல்ல — உங்கள் mind, body, family-க்கு நேரடி connection.",
        f"Madurai Meenakshi Amman, Palani, Tiruchendur, Chidambaram போன்ற புனித sthalangal-ல் இன்றும் அதே முறை follow செய்யப்படுகிறது. [PAUSE_MED] அந்த tradition-ஐ புரிந்து செய்தால் பலன் தவிர்க்க முடியாது.",
        f"முதல் step: காலை 5-6 AM-க்குள் குளித்து, clean dress, mind-ஐ அமைதியாக்குங்கள். [PAUSE_SHORT] {deity_label} name-ஐ மனதில் வைத்து 11 முறை சொல்லுங்கள்.",
        f"இரண்டாவது step: lamp ஏற்றி, fresh flowers, தேங்காய்/fruit naivedyam செய்யுங்கள். [PAUSE_MED] devotion sincerity தான் முக்கியம் — expensive items அல்ல.",
        f"மூன்றாவது step: {topic} தொடர்பான specific mantra-ஐ daily 27/54/108 times சொல்லுங்கள். [PAUSE_LONG] 21 நாட்கள் consistent-ஆக செய்தால் mental clarity வரும்.",
        f"நான்காவது step: Friday/Special day fasting optional — ஆனால் sattvic food, lie-இல்லாத speech, anger control important. [PAUSE_MED] {deity_label} grace shy persons-க்கும் வரும்.",
        f"ஐந்தாவது step: poor-க்கு annadhanam, elderly-க்கு help, temple-க்கு voluntary service — இது pariharam-ஐ multiply செய்யும். [PAUSE_SHORT] Give without expecting return.",
        f"ஆராய்ச்சி scholars சொல்வது: devotional listening 5 minutes daily brain-ஐ calm செய்கிறது. [PAUSE_MED] Stress, insomnia, family conflict — gradual-ஆ reduce ஆகும்.",
        f"Real story: Salem-ல் ஒரு family years-ஆ struggle. [PAUSE_SHORT] {deity_label} vratam + sincere puja start பண்ணினார்கள் — business, health, peace slowly improved.",
        f"Another story: Coimbatore-ல் young couple child blessing prayer. [PAUSE_MED] Temple tradition follow + selfless service — after months they felt deep peace & new hope.",
        f"Myths vs truth: 'Only archakas can worship correctly' — false. [PAUSE_SHORT] Bhakti from heart is enough if method is sincere.",
        f"Myth: 'One mistake ruins everything' — {deity_label} is karunai kadavul. [PAUSE_MED] Restart with humility; grace continues.",
        f"Today action plan: [PAUSE_LONG] Tonight before sleep, 5 minutes {deity_label} naamam. Tomorrow morning lamp. This week one temple visit or home altar cleanup.",
        f"Benefits devotees report: courage, clarity, debt relief, marriage harmony, job opportunities, health stability. [PAUSE_MED] Timing differs — patience with faith.",
        f"Pariharam for obstacles: light sesame lamp on Saturday, offer black cloth at Amman/Murugan temple if guided, chant 108 times with focus not speed.",
        f"Children in family: teach simple slokam, bring them to festival days — values pass to next generation. [PAUSE_SHORT] Culture survives through home practice.",
        f"For working professionals: even 2 minutes office break prayer counts. [PAUSE_MED] {deity_label} sees intention, not only duration.",
        f"Closing: [PAUSE_LONG] {topic} — இது theory அல்ல, daily practice. Start small, stay consistent 21 days.",
        f"ஆலய மணி channel-ல subscribe பண்ணுங்கள் — daily temple wisdom, pariharam, sthala puranam. [PAUSE_MED] Bell icon press பண்ணுங்கள்.",
        f"Comment-ல உங்கள் native place temple name எழுதுங்கள் 👇 [PAUSE_SHORT] Next video-ல அந்த sthalam special secrets பார்க்கலாம்.",
        f"Share this with family WhatsApp group — together bhakti grows. [PAUSE_MED] {deity_label} thiruvadiyil nammudaiyal.",
    ]
    text = "\n\n".join(paragraphs)
    while len(text) < TARGET_MIN:
        text += (
            f"\n\n{deity_label} bhakti path-ல patience மிக முக்கியம். [PAUSE_MED] "
            f"{topic} daily remembrance-ஆ mind-ஐ strong ஆக்கும். Trust the process."
        )
    return text[:TARGET_MAX]


COMBINED_META_PROMPT = """Tamil devotional video-க்கு YouTube metadata உருவாக்குங்கள்.
கட்டாயம் valid JSON மட்டும் return செய்யுங்கள் — வேறு எதுவும் வேண்டாம்.

கடவுள்: {deity} ({deity_en}) | தலைப்பு: {topic} | hashtags: {hashtags}

இந்த exact JSON:
{{
  "title": "[100% தமிழ் YouTube title — 60 chars இல், click-worthy]",
  "description": "[முதல் வரி: hook கேள்வி | benefits | chapters | CTA — 100% தமிழ்]",
  "tags": "[20-25 ASCII English tags only — murugan, tamil devotional 2026, palani temple, etc]",
  "pinned_comment": "[100% தமிழ் — 500 chars கீழே — viewers engage கேள்வி + சந்தா CTA]"
}}

TAGS: கட்டாயம் ASCII English மட்டும் — YouTube API தமிழ் script accept செய்யாது (HTTP 400 error).
TITLE/DESCRIPTION/PINNED: 100% தமிழ் மட்டும்.
  "subscribe" → "சந்தா", "like" → "லைக்", "share" → "பகிர்", "channel" → "சேனல்"

CHAPTERS (description-ல் கட்டாயம்):
00:00 தொடக்கம்
01:00 பின்னணி
02:30 முக்கிய தகவல்
05:30 🔔 சந்தா செய்யுங்கள்"""

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
        f"🙏 {deity} வழிபாடு\n\n"
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
        f"{deity_en}, tamil devotional {year}, aalaya mani, "
        f"tamil god songs, temple stories tamil, {deity_en.lower()} songs, "
        f"devotional tamil, spiritual tamil, aalaya mani tamil"
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
    try:
        raw = call_llm_free(prompt, task="metadata", max_retries=3, max_tokens=2000)
    except Exception as llm_error:
        log(f"  ⚠️ LLM metadata failed ({llm_error}) — using fallback metadata")
        return _build_fallback_metadata(config, year)

    try:
        clean = _sanitize_llm_json(raw)

        data = json.loads(clean)

        desc = data.get("description", "")
        if desc.strip().startswith("{") or desc.strip().startswith("["):
            log("  ⚠️ Description was JSON — rebuilding...")
            data["description"] = _build_description(config, data)

        log(f"  Metadata complete ({time.time()-t0:.0f}s)")
        return data

    except Exception as e:
        log(f"  ⚠️ JSON parse failed ({e}) — using fallback metadata")

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


KB_PRESETS = [
    ("min(1.0+0.0008*on,1.20)", "iw/2-(iw/zoom/2)+on*0.3", "ih/2-(ih/zoom/2)", "zoom-in pan-right"),
    ("min(1.0+0.0008*on,1.20)", "iw/2-(iw/zoom/2)-on*0.3", "ih/2-(ih/zoom/2)", "zoom-in pan-left"),
    ("max(1.25-0.0008*on,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", "zoom-out center"),
    ("min(1.0+0.0008*on,1.15)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)+on*0.2", "zoom-in pan-up"),
    ("max(1.20-0.0007*on,1.0)", "iw/2-(iw/zoom/2)+on*0.25", "ih/2-(ih/zoom/2)", "zoom-out pan-right"),
    ("min(1.0+0.0004*on,1.08)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", "slow-zoom"),
]

XFADE_TRANSITIONS = ["fade", "dissolve", "wipeleft", "wiperight", "slideleft"]


def build_video_filter(images, total_duration_sec, fps=25, seed=None):
    """Build ffmpeg filter_complex: Ken Burns segments matched to audio length."""
    num = len(images)
    xfade_dur = 1.0
    total_duration_sec = max(float(total_duration_sec), 5.0)

    if num <= 1:
        segment_duration_sec = total_duration_sec
    else:
        segment_duration_sec = (total_duration_sec + (num - 1) * xfade_dur) / num

    segment_frames = max(int(segment_duration_sec * fps), fps * 2)

    filters = []
    for index in range(num):
        preset = KB_PRESETS[index % len(KB_PRESETS)]
        zoom_expr, x_expr, y_expr, label = preset
        log(f"    Image {index + 1}: {label} ({segment_duration_sec:.1f}s)")
        filters.append(
            f"[{index}:v]loop=loop=-1:size=1:start=0,"
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={segment_frames}:fps={fps}:s=1920x1080,"
            f"trim=0:{segment_duration_sec:.3f},setpts=PTS-STARTPTS[v{index}]"
        )

    previous_label = "v0"
    for index in range(1, num):
        transition = XFADE_TRANSITIONS[index % len(XFADE_TRANSITIONS)]
        offset_sec = index * segment_duration_sec - index * xfade_dur
        output_label = f"x{index}"
        filters.append(
            f"[{previous_label}][v{index}]xfade=transition={transition}:"
            f"duration={xfade_dur}:offset={max(0.1, offset_sec):.3f}[{output_label}]"
        )
        previous_label = output_label

    return num, ";".join(filters), previous_label


def _encode_simple_slideshow(images, audio_path, total_duration_sec, video_raw, fps=25):
    """Reliable full-length fallback — concat equal segments, one per image."""
    image_count = len(images)
    if image_count == 0:
        return False

    total_duration_sec = max(float(total_duration_sec), 5.0)
    segment_duration_sec = total_duration_sec / image_count

    command = ["ffmpeg", "-y"]
    for image_path in images:
        command.extend(["-loop", "1", "-t", f"{segment_duration_sec:.3f}", "-i", image_path])
    command.extend(["-i", audio_path])

    scale_filters = []
    for index in range(image_count):
        scale_filters.append(
            f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,fps={fps},setpts=PTS-STARTPTS[v{index}]"
        )
    concat_inputs = "".join(f"[v{index}]" for index in range(image_count))
    filter_complex = (
        ";".join(scale_filters)
        + f";{concat_inputs}concat=n={image_count}:v=1:a=0[vout]"
    )

    encode_timeout = max(900, int(total_duration_sec * 4))
    command.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", f"{image_count}:a",
        "-t", f"{total_duration_sec:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        video_raw,
    ])
    result = run(command, timeout=encode_timeout)
    if result.returncode != 0:
        log(f"  ❌ Simple slideshow failed: {result.stderr[-300:]}")
        return False
    return os.path.exists(video_raw)


def make_intro_bell(output_path, duration=2.5):
    """Generate a temple bell ding."""
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
    """Build drawtext filter for video overlay."""
    safe = lambda s: s.replace("'", "").replace(":", "-").replace('"', "")
    channel = safe("ஆலய மணி")
    deity   = safe(deity_name) if deity_name else safe(deity_en)
    title   = safe(title_short[:50]) if title_short else ""

    overlays = []

    overlays.append(
        f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf:text='{channel}':fontsize=28:fontcolor=white@0.75:"
        f"x=30:y=30:shadowcolor=black@0.8:shadowx=2:shadowy=2"
    )

    if deity:
        overlays.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf:text='{deity}':fontsize=52:fontcolor=gold@1.0:"
            f"x=(w-text_w)/2:y=60:"
            f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
            f"alpha='if(lt(t,0.5),0,if(lt(t,2),(t-0.5)/1.5,if(lt(t,5),1,if(lt(t,6),(6-t),0))))':"
            f"enable='between(t,0,6)'"
        )

    if title:
        overlays.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf:text='{title}':fontsize=34:fontcolor=white@0.9:"
            f"x=(w-text_w)/2:y=h-80:"
            f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
            f"alpha='if(lt(t,1),0,if(lt(t,2.5),(t-1)/1.5,if(lt(t,8),1,if(lt(t,9),(9-t),0))))':"
            f"enable='between(t,0,9)'"
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

    script_text = script_text.strip()
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_text)

    log("🔊 Step 1/6 Voice (edge-tts)...")
    t0 = time.time()
    if not generate_narration_audio(script_text, human_file, deity_name=deity_name, run_fn=run):
        log("❌ Voice generation failed")
        return None
    dur = get_dur_float(human_file)
    log(f"  Voice: {dur:.0f}s ({time.time()-t0:.0f}s generation, {len(script_text)} chars)")
    if len(script_text) > 5000 and dur < 120:
        log(f"  ⚠️ Voice too short for script length — narration may be incomplete")

    make_intro_bell(bell_file)

    if os.path.exists(bgm):
        log("🎵 Step 3/6 BGM + bell mixing...")
        if mix_voice_bgm_bell(human_file, bgm, bell_file, mixed_file, bgm_volume=bgm_vol, run_fn=run):
            audio = mixed_file
            log("  ✅ Voice + BGM mixed (sidechain ducking)")
        else:
            audio = human_file
            log("  ⚠️ BGM mix failed — using voice only")
    else:
        audio = human_file

    audio, total_dur = _trim_audio_if_too_long(audio, output_name)
    if total_dur < 30:
        log(f"  ❌ Audio too short ({total_dur:.1f}s) — cannot build full video")
        return None

    log("🎬 Step 4/6 Video (Ken Burns + slideshow)...")
    t0 = time.time()

    if isinstance(images_input, list):
        images = [f for f in images_input if os.path.exists(f)]
    else:
        images = find_images(images_input)

    if not images:
        log(f"❌ No images found")
        return None

    images = _clamp_slide_count(images, int(total_dur))
    log(f"🖼️ Using {len(images)} images: {[os.path.basename(i)[:20] for i in images]}")

    fps = 25
    num_inputs, vfilter, vlabel = build_video_filter(images, total_dur, fps=fps)

    encode_timeout = max(900, int(total_dur * 4))
    command = ["ffmpeg", "-y"]
    for image_path in images:
        command.extend(["-loop", "1", "-t", f"{total_dur + 2:.3f}", "-i", image_path])
    command.extend(["-i", audio, "-filter_complex", vfilter,
                    "-map", f"[{vlabel}]", "-map", f"{num_inputs}:a",
                    "-t", f"{total_dur:.3f}",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-ar", "44100", "-ac", "2",
                    "-movflags", "+faststart",
                    "-avoid_negative_ts", "make_zero", video_raw])

    log(f"  Encoding {num_inputs} images × {total_dur:.0f}s @ {fps}fps...")
    encode_result = run(command, timeout=encode_timeout)
    if encode_result.returncode != 0:
        log(f"  ⚠️ Ken Burns slideshow failed — using simple full-length fallback...")
        log(f"  ffmpeg: {encode_result.stderr[-250:]}")
        if not _encode_simple_slideshow(images, audio, total_dur, video_raw, fps=fps):
            log("  ⚠️ Simple slideshow failed — single-image full-length fallback...")
            single_image = images[0]
            single_result = run([
                "ffmpeg", "-y", "-loop", "1", "-i", single_image, "-i", audio,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                       "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                "-t", f"{total_dur:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart",
                video_raw,
            ], timeout=encode_timeout)
            if single_result.returncode != 0:
                log(f"  ❌ Video error: {single_result.stderr[-200:]}")
                return None

    log("✍️ Step 5/6 Text overlays...")
    text_filter = build_text_overlay(deity_name, deity_en, title_short, total_dur)
    overlay_timeout = _ffmpeg_timeout(total_dur, multiplier=2.0, floor=600)
    try:
        r3 = run(["ffmpeg", "-y", "-i", video_raw,
                   "-vf", text_filter,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-c:a", "copy", "-movflags", "+faststart", video_file],
                 timeout=overlay_timeout)
    except subprocess.TimeoutExpired:
        log(f"  ⚠️ Text overlay timed out after {overlay_timeout}s — using raw video")
        shutil.copy(video_raw, video_file)
        r3 = subprocess.CompletedProcess([], 1)
    if r3.returncode != 0:
        log("  ⚠️ Text overlay failed — using raw video")
        shutil.copy(video_raw, video_file)
    else:
        log("  ✅ Overlays: channel name + deity + title")

    log("🏷️ Applying channel watermark...")
    watermarked_path = f"/tmp/{output_name}_watermarked.mp4"
    if apply_watermark(video_file, watermarked_path, encode_timeout=_ffmpeg_timeout(total_dur)):
        shutil.move(watermarked_path, video_file)

    mb = os.path.getsize(video_file) / (1024 * 1024)
    final_dur = get_dur_float(video_file)
    log(f"  Video: {mb:.1f}MB, {final_dur:.0f}s target {total_dur:.0f}s ({time.time()-t0:.0f}s encode)")
    if final_dur < total_dur * 0.85:
        log(f"  ⚠️ Video shorter than audio — check ffmpeg pipeline")

    for f in [script_file, voice_file, human_file, bell_file, mixed_file, video_raw]:
        try:
            if os.path.exists(f): os.remove(f)
        except: pass

    return video_file


# =============================================
# YOUTUBE UPLOAD
# =============================================

MCQ_PROMPT = """ஆலய மணி சேனலுக்கு ஒரு devotional quiz கேள்வி உருவாக்குங்கள்.
Script: {key_fact}

விதிகள்: 100% தமிழில், 4 options, 3 வரிகளுக்கு மேல் வேண்டாம்
கடைசியில்: "சரியான விடையை கீழே comment பண்ணுங்கள் 👇"

Format:
[கேள்வி]?
அ) [option]  ஆ) [option]
இ) [option]  ஈ) [option]
சரியான விடையை கீழே comment பண்ணுங்கள் 👇

Quiz text மட்டும் return செய்யுங்கள்."""


def generate_mcq(topic, script_text, deity=""):
    try:
        raw = call_llm_free(MCQ_PROMPT.format(
            topic=topic, deity=deity or "கடவுள்",
            key_fact=script_text[:400]), task="small", max_tokens=500)
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
COMMENT_RESPONSE_PROMPT = """ஆலய மணி சேனலுக்கு viewer comment-க்கு reply எழுதுங்கள்.
Video: {topic} | கடவுள்: {deity}
Comment: {comment}

150 எழுத்துகளுக்கு குறைவாக, 100% தமிழில், அன்பான பெரியவர் தொனியில் பதில் எழுதுங்கள்.
Bot என்று சொல்லவே கூடாது."""

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
                    reply = call_llm_free(COMMENT_RESPONSE_PROMPT.format(
                        topic=topic, deity=deity, comment=text[:200]),
                        task="small", max_tokens=200).strip()
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
    def _git_commit():
        run(["git", "add", "data/tracking/performance.json", ANALYTICS_FILE])
        run(["git", "commit", "-m", "chore: analytics update"])
        run(["git", "push"])

    _run_analytics_loop(get_authenticated_service, git_commit_fn=_git_commit)


def load_analytics_insights():
    return analytics_load_insights()

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
            raw = call_llm_free(
                f"Tamil devotional video topic: {topic}\nDate: {date}\nToday: {today}\n"
                f"Does any festival date or ritual procedure need updating? "
                f'Return JSON: {{"needs_update":true/false,"update_comment":"<Tamil <200 chars if needed>","reason":"<English>"}}',
                task="small", max_tokens=400)
            try:
                import json as _json
                result = _json.loads(raw.strip())
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
        raw = call_llm_free(
            f"Tamil devotional community post for 'ஆலய மணி'. "
            f"Day: {now.strftime('%A')}. Recent topic: {recent}. "
            f"Monday=poll, Wednesday=tip, Friday=fact, Sunday=quiz. "
            f'Return JSON: {{"type":"poll"or"post","text":"<Tamil<500chars>","options":["opt1","opt2","opt3","opt4"]}}',
            task="small", max_tokens=600)
        import json as _json
        data = _json.loads(raw.strip())
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

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log("  ✅ Token refreshed")
        except Exception as e:
            log(f"  ⚠️ Token refresh failed: {e}")
            return None

    token_scopes = set(getattr(creds, "scopes", []) or [])
    missing = REQUIRED_SCOPES - token_scopes
    if "https://www.googleapis.com/auth/youtube.force-ssl" in missing:
        log("  ℹ️ Token missing youtube.force-ssl — run setup_youtube_secrets.py locally to re-auth")

    if not creds.valid:
        log("  ⚠️ Token invalid and cannot be refreshed — re-run auth setup")
        return None

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"  ⚠️ YouTube API build failed: {e}")
        return None


def validate_script(text, lang="tamil"):
    """Quality check on generated script."""
    import re
    if not text or len(text) < 500:
        return False, text, "too short"

    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"```[^`]*```", "", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

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
    """YouTube tags: ASCII only — non-ASCII causes HTTP 400."""
    import re as _re
    tags = [t.strip() for t in str(tags_str).split(",") if t.strip()]
    cleaned = []
    for tag in tags:
        tag = tag.encode("ascii", errors="ignore").decode("ascii")
        for ch in list('<>"\'#@!'):
            tag = tag.replace(ch, "")
        tag = _re.sub(r"\s+", " ", tag).strip()
        if len(tag) >= 2:
            cleaned.append(tag[:100].strip())
    result, total = [], 0
    for tag in cleaned[:30]:
        if total + len(tag) + 1 <= 490:
            result.append(tag)
            total += len(tag) + 1
        else:
            break
    if not result:
        result = ["temple tamil","devotional tamil","hindu temple","tamil devotion"]
    return ", ".join(result)


TAMIL_BOLD_FONT = "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf"
ENG_BOLD_FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def generate_thumbnail(title, deity_name, output_name, deity_en="", bg_image_path=None):
    """Dynamic thumbnail — delegates to thumbnail_engine module."""
    thumb_text = extract_thumbnail_text(title, deity_name)
    diversity_engine = get_diversity_engine()
    if not diversity_engine.is_thumbnail_text_allowed(thumb_text):
        thumb_text = extract_thumbnail_text(f"{deity_name} ரகசியம்", deity_name)
    return render_thumbnail(
        title=title,
        deity_name=deity_name,
        output_name=output_name,
        deity_en=deity_en,
        bg_image_path=bg_image_path,
        thumbnail_text=thumb_text,
    )


# ═══════════════════════════════════════════════════════════════════
# FREE LLM ROUTER — Groq / Gemini / GitHub Models / Cerebras (all $0)
# ═══════════════════════════════════════════════════════════════════

PROVIDERS = [
    ("groq",      "https://api.groq.com/openai/v1",        GROQ_API_KEY,  FREE_GROQ_MODEL,      "script"),
    ("github",    "https://models.inference.ai.azure.com", GITHUB_TOKEN,  FREE_GITHUB_MODEL,    "all"),
    ("gemini",    None,                                    GEMINI_KEY,    FREE_GEMINI_MODEL,    "all"),
    ("cerebras",  "https://api.cerebras.ai/v1",            CEREBRAS_KEY,  FREE_CEREBRAS_MODEL,  "all"),
    ("groq_fb",   "https://api.groq.com/openai/v1",        GROQ_API_KEY,  FREE_GROQ_FAST_MODEL, "fallback"),
    ("gemini_fb", None,                                    GEMINI_KEY,    FREE_GEMINI_LITE,     "fallback"),
]

FREE_LLM_TASK_ORDER = {
    # GitHub Models primary — Groq/Gemini as fallbacks only
    "topic":       ["github", "groq", "groq_fb", "gemini_fb"],
    "script":      ["github", "groq", "cerebras", "gemini_fb"],
    "script_part": ["github", "groq", "gemini_fb"],
    "metadata":    ["github", "groq", "groq_fb", "gemini_fb"],
    "small":       ["github", "groq_fb", "gemini_fb"],
}
DEFAULT_FREE_ORDER = ["github", "groq", "groq_fb", "cerebras", "gemini_fb"]

def _call_groq_native(api_key, model, prompt, max_tokens=4000):
    """Groq via official SDK — no openai package required."""
    if Groq is None:
        raise ImportError("groq package not installed")
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.85,
    )
    return resp.choices[0].message.content


def _call_openai_compatible(base_url, api_key, model, prompt, max_tokens=4000):
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.85,
    )
    return resp.choices[0].message.content


def _call_gemini(api_key, models, prompt):
    """Try Gemini models in order until one works."""
    client = genai.Client(api_key=api_key)
    last_error = ""
    for model_name in models:
        try:
            resp = client.models.generate_content(model=model_name, contents=prompt)
            if resp.text and resp.text.strip():
                return resp.text
        except Exception as exc:
            last_error = str(exc)
            if _is_quota_exhausted_error(last_error):
                raise
            if _is_skip_error(last_error):
                log(f"  ⚠️ gemini/{model_name}: {last_error[:60]} — next model")
                continue
            raise
    raise Exception(last_error or "gemini: all models failed")


def _call_github_models(api_key, base_url, models, prompt, max_tokens=4000):
    """Try GitHub Models with fallback model IDs."""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
    last_error = ""
    for model_name in models:
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.85,
            )
            content = resp.choices[0].message.content
            if content and content.strip():
                return content
        except Exception as exc:
            last_error = str(exc)
            if _is_skip_error(last_error) or "unknown_model" in last_error.lower():
                log(f"  ⚠️ github/{model_name}: {last_error[:60]} — next model")
                continue
            raise
    raise Exception(last_error or "github: all models failed")


def _call_provider(name, base_url, api_key, model, prompt, max_tokens=4000, task=None):
    """Call a single provider. Returns text or raises."""
    if not api_key:
        raise Exception(f"{name}: no API key")

    if name in ("gemini", "gemini_fb"):
        models = _select_gemini_models(name, task, max_tokens)
        return _call_gemini(api_key, models, prompt)
    if name in ("groq", "groq_fb"):
        return _call_groq_native(api_key, model, prompt, max_tokens)
    if name == "github":
        return _call_github_models(api_key, base_url, GITHUB_MODEL_CANDIDATES, prompt, max_tokens)
    return _call_openai_compatible(base_url, api_key, model, prompt, max_tokens)


def _is_skip_error(err_str):
    """Non-retryable provider/model errors — move to next provider immediately."""
    lowered = err_str.lower()
    return any(token in lowered for token in [
        "404", "400", "413", "not_found", "unknown_model",
        "request too large", "invalid model", "is not found",
        "not supported for generatecontent",
    ])


def _is_retryable(err_str):
    """True if the error is transient (rate limit / server overload)."""
    if _is_skip_error(err_str):
        return False
    return any(c in err_str for c in [
        "429", "503", "502", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
        "high demand", "overloaded", "ServiceUnavailable",
        "rate_limit", "Internal", "timeout", "timed out",
    ])


def _any_llm_provider_available(task=None) -> bool:
    """True if at least one provider with a key is not quota-blocked."""
    provider_map = {p[0]: p for p in PROVIDERS}
    for provider_name in _provider_order(task=task):
        if not _is_provider_available(provider_name):
            continue
        _, _, api_key, _, _ = provider_map.get(provider_name, (None, None, None, None, None))
        if api_key:
            return True
    return False


def _provider_order(prefer=None, task=None):
    if task and task in FREE_LLM_TASK_ORDER:
        order = FREE_LLM_TASK_ORDER[task]
    elif prefer == "groq":
        order = ["groq", "github", "groq_fb", "cerebras", "gemini_fb"]
    elif prefer == "github":
        order = ["github", "groq", "groq_fb", "cerebras", "gemini_fb"]
    elif prefer == "gemini":
        order = ["gemini_fb", "github", "groq", "groq_fb"]
    else:
        order = DEFAULT_FREE_ORDER
    return [p for p in order if _is_provider_available(p)]


def _gemini_max_retries(err_str: str) -> int:
    """Gemini free tier: one short retry on RPM blip, none on quota exhaustion."""
    if _is_quota_exhausted_error(err_str):
        return 0
    return 1


def call_llm(prompt, max_retries=3, prefer="groq", max_tokens=4000, task=None):
    """Resilient multi-provider router — free tier only."""
    order = _provider_order(prefer=prefer, task=task)

    provider_map = {p[0]: p for p in PROVIDERS}
    last_error = ""

    for provider_name in order:
        if provider_name not in provider_map:
            continue
        if not _is_provider_available(provider_name):
            continue
        name, base_url, api_key, model, _ = provider_map[provider_name]
        if not api_key:
            continue

        provider_retries = 2 if name in ("gemini", "gemini_fb") else max_retries
        for attempt in range(provider_retries):
            try:
                result = _call_provider(
                    name, base_url, api_key, model, prompt, max_tokens, task=task,
                )
                if result and result.strip():
                    if attempt > 0 or provider_name != order[0]:
                        log(f"  ✅ LLM: {name}/{model.split('-')[0]}")
                    return result.strip()
            except Exception as e:
                err = str(e)
                last_error = err
                if _is_quota_exhausted_error(err):
                    _mark_provider_exhausted(name)
                    break
                if _is_retryable(err):
                    if "tokens per day" in err or "TPD" in err or "daily" in err.lower():
                        _mark_provider_exhausted(name)
                        log(f"  ⚠️ {name}: daily limit — trying next provider")
                        break
                    if name in ("gemini", "gemini_fb") and attempt >= _gemini_max_retries(err):
                        log(f"  ⚠️ {name}: rate limited — trying next provider")
                        break
                    wait = 5 if name in ("gemini", "gemini_fb") else min(10 * (2 ** attempt), 60)
                    log(f"  ⏳ {name} retry {attempt+1}/{provider_retries} in {wait}s ({err[:60]})")
                    time.sleep(wait)
                else:
                    log(f"  ⚠️ {name}: {err[:80]} — skipping")
                    break

    raise Exception(f"All free LLM providers failed. Last: {last_error[:150]}")


def call_llm_free(prompt, task="general", max_retries=3, max_tokens=4000, prefer=None):
    """Route LLM calls across free providers to spread daily quota."""
    return call_llm(
        prompt,
        max_retries=max_retries,
        task=task,
        max_tokens=max_tokens,
        prefer=prefer or "github",
    )


def call_llm_groq(prompt, max_retries=3):
    """Script generation — Groq first, all free providers as fallback."""
    return call_llm_free(prompt, task="script", max_retries=max_retries, max_tokens=4000)


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


def fix_chapter_timestamps(description, duration_seconds):
    """Scale chapter timestamps to fit actual video duration."""
    import re as _re
    lines = description.split("\n")
    chapter_lines = [(i, l) for i, l in enumerate(lines)
                     if _re.match(r"^\d+:\d+", l.strip())]
    if not chapter_lines or duration_seconds < 30:
        return description
    def ts_to_sec(ts):
        p = ts.strip().split(":"); return int(p[0])*60+int(p[1]) if len(p)==2 else 0
    def sec_to_ts(s): return f"{int(s)//60}:{int(s)%60:02d}"
    last_ts = max((ts_to_sec(_re.match(r"^(\d+:\d+)", l.strip()).group(1))
                   for _, l in chapter_lines if _re.match(r"^(\d+:\d+)", l.strip())), default=0)
    if last_ts == 0: return description
    scale = (duration_seconds - 5) / last_ts
    new_lines = list(lines)
    for i, l in chapter_lines:
        m = _re.match(r"^(\d+:\d+)(.*)", l.strip())
        if m:
            new_sec = min(int(ts_to_sec(m.group(1)) * scale), duration_seconds - 3)
            new_lines[i] = sec_to_ts(new_sec) + m.group(2)
    return "\n".join(new_lines)


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
            "tags": [t.strip() for t in
                     validate_tags(metadata.get("tags","")).split(",") if t.strip()][:30],
            "categoryId": "27",
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
# ═══════════════════════════════════════════════════════════════════════

def generate_video_scenes(output_name, topic="", scene_type="default",
                          num_scenes=6, channel="generic"):
    """Generate rich animated scene images. Pure Pillow — no network needed."""
    from PIL import Image, ImageDraw, ImageFont
    import os, math, random, hashlib

    seed = int(hashlib.md5((output_name + topic).encode()).hexdigest()[:8], 16)
    random.seed(seed)
    W, H = 1920, 1080

    scene_dir = os.path.join(PEXELS_DIR, output_name)
    os.makedirs(scene_dir, exist_ok=True)

    def sf(size, bold=True):
        try:
            p = ENG_BOLD_FONT if bold else ENG_BOLD_FONT
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
    else:
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
        rs = seed + i * 6547
        random.seed(rs)

        img = Image.new("RGB", (W,H), c1)
        d   = ImageDraw.Draw(img)
        grad(d, c1, c2)

        if scene_name == "hero":
            cx, cy = W//2, H//2
            glow(d, cx, cy, 500, acc, 20)
            for angle in range(0, 360, 12):
                rad = math.radians(angle + rs%30)
                length = random.randint(300, 700)
                x2 = cx + int(math.cos(rad)*length)
                y2 = cy + int(math.sin(rad)*length)
                d.line([(cx,cy),(x2,y2)], fill=(*acc,6+random.randint(0,8)), width=1)
            glow(d, cx, cy, 200, acc, 12)
            if channel == "am":
                try: d.text((cx,cy-40), "ॐ", font=sf(220), fill=(*acc,60), anchor="mm")
                except: pass
            elif channel == "nn":
                try: d.text((cx,cy-30), "₹", font=sf(260), fill=(*acc,50), anchor="mm")
                except: pass
            else:
                s = 1.8
                body = [(cx-int(120*s),cy+int(25*s)),(cx-int(122*s),cy-int(8*s)),
                        (cx-int(95*s),cy-int(35*s)),(cx-int(30*s),cy-int(62*s)),
                        (cx+int(45*s),cy-int(62*s)),(cx+int(105*s),cy-int(30*s)),
                        (cx+int(122*s),cy-int(8*s)),(cx+int(124*s),cy+int(25*s))]
                d.polygon(body, fill=(22,25,38))
                d.polygon(body, outline=acc, width=2)

        elif scene_name == "ambient":
            for _ in range(120):
                px = random.randint(0,W); py = random.randint(0,H)
                r = random.choice([1,1,1,2,2,3])
                a = random.randint(40,160)
                d.ellipse([px-r,py-r,px+r,py+r], fill=(*acc,a))
            for _ in range(30):
                y2 = random.randint(0,H)
                ln = random.randint(50,400)
                x2 = random.randint(0,W)
                a = random.randint(15,50)
                d.line([(x2,y2),(x2+ln,y2)], fill=(*acc,a), width=1)
            glow(d, W//2+random.randint(-200,200), H//2+random.randint(-100,100), 300, acc, 8)

        elif scene_name == "detail":
            for x in range(0,W,90):
                a = max(8, 30 - abs(x-W//2)//30)
                d.line([(x,0),(x,H)], fill=(*acc,a), width=1)
            for y in range(0,H,90):
                a = max(8, 30 - abs(y-H//2)//20)
                d.line([(0,y),(W,y)], fill=(*acc,a), width=1)
            cx2 = W//2 + random.randint(-200,200)
            cy2 = H//2 + random.randint(-80,80)
            glow(d, cx2, cy2, 280, acc, 15)
            for r in [200,160,120,80]:
                d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r], outline=(*acc,40+r//10), width=1)

        elif scene_name == "wide":
            num_layers = random.randint(4,7)
            for layer in range(num_layers):
                t = layer/num_layers
                y1 = int(H*t); y2 = int(H*(t+1/num_layers))+2
                darkness = 0.6 + t*0.4
                col = tuple(int(c1[j]*darkness + acc[j]*(1-darkness)*0.15) for j in range(3))
                d.rectangle([0,y1,W,y2], fill=col)
            hy = H//2 + random.randint(-50,50)
            for r in range(H//3, 0, -H//60):
                t = 1-r/(H//3)
                a = int(t*12)
                d.ellipse([W//2-r*2,hy-r//2,W//2+r*2,hy+r//2], fill=(*acc,a))

        elif scene_name == "close":
            for i in range(-H, W+H, 80):
                a = random.randint(5,18)
                d.polygon([(i,0),(i+60,0),(i+60+H,H),(i+H,H)], fill=(*acc,a))
            zx, zy = random.randint(W//4,W*3//4), random.randint(H//4,H*3//4)
            for _ in range(80):
                px = zx + random.randint(-250,250)
                py = zy + random.randint(-150,150)
                r = random.randint(2,6)
                a = random.randint(60,200)
                d.ellipse([px-r,py-r,px+r,py+r], fill=(*acc,a))

        elif scene_name == "atmosphere":
            for layer in range(8):
                t = layer/8
                y_base = H - int(layer * H//10)
                for y in range(max(0,y_base-80), min(H,y_base+80)):
                    tt = 1-abs(y-y_base)/80
                    a = int(tt * (20+layer*5))
                    col = tuple(min(255,c+a) for c in c1)
                    d.line([(0,y),(W,y)], fill=col)
            for y in range(H//4):
                t = 1-y/(H//4)
                col = tuple(int(c*t*0.8) for c in c1)
                d.line([(0,y),(W,y)], fill=col)
            for _ in range(5):
                ox = random.randint(100,W-100)
                oy = random.randint(H//4,H*3//4)
                r = random.randint(30,80)
                glow(d, ox, oy, r*3, acc, 6)

        elif scene_name == "texture":
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
            cx3, cy3 = W//2+random.randint(-100,100), H//2+random.randint(-50,50)
            for r in range(600, 0, -20):
                t = 1-r/600; a = int(t*15)
                ratio = 0.6 + t*0.4
                d.ellipse([cx3-int(r*ratio),cy3-int(r*0.6),
                           cx3+int(r*ratio),cy3+int(r*0.6)],
                          outline=(*acc,a), width=1)
            glow(d, cx3, cy3, 120, acc, 10)
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


def _clamp_slide_count(images, duration_seconds):
    """Keep 4-10 slides (~20-30 seconds each)."""
    target = max(4, min(10, int(duration_seconds / 25)))
    return images[:target] if len(images) > target else images


def _assemble_pipeline_images(deity, deity_en, topic, day, fallback_image=None):
    """Central image assembly — real photos first, scenes last."""
    img_dir = f"/tmp/am_imgs_{day}"
    return assemble_video_images(
        deity=deity,
        deity_en=deity_en,
        topic=topic,
        day=day,
        img_dir=img_dir,
        fetch_wikimedia_fn=fetch_wikimedia_images_am,
        fetch_pollinations_fn=fetch_pollinations_image_am,
        fetch_pexels_deity_fn=lambda deity_name, day_name: fetch_pexels_images(
            deity_name, os.path.join(PEXELS_DIR, day_name), count=6
        ),
        generate_scenes_fn=generate_video_scenes,
        pexels_api_key=PEXELS_API_KEY,
        fallback_image=fallback_image or IMAGE_FILE,
        log_fn=log,
    )


def cleanup_old_artifacts(max_age_hours=24):
    """Delete generated artifacts older than max_age_hours to keep runner disk clean."""
    import time as _t
    now = _t.time()
    cutoff = now - (max_age_hours * 3600)
    removed = 0
    dirs_to_clean = [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR,
                     PEXELS_DIR, "/tmp"]
    extensions_to_clean = {".mp4", ".mp3", ".jpg", ".jpeg", ".png",
                            ".srt", ".txt", ".json"}
    for d in dirs_to_clean:
        if not os.path.exists(d):
            continue
        for root, dirs, files in os.walk(d):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if (os.path.splitext(fname)[1].lower() in extensions_to_clean
                            and os.path.getmtime(fpath) < cutoff):
                        os.remove(fpath)
                        removed += 1
                except Exception:
                    pass
    log(f"🧹 Cleanup: removed {removed} artifacts older than {max_age_hours}h")


def safe_process_day(day, image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Full pipeline: LLM picks best deity+topic → Pexels → script+metadata → video → upload."""
    reset_llm_provider_state()
    cleanup_old_artifacts(max_age_hours=24)
    bgm = bgm or BGM_FILE
    t_start = datetime.datetime.now()

    config = discover_daily_config(day)
    topic    = config["topic"]
    emoji    = config["emoji"]
    deity    = config["deity"]
    deity_en = config["deity_en"]

    log(f"{'='*50}")
    log(f"{emoji} {deity} — {deity_en}")
    log(f"📌 {topic}")
    log(f"{'='*50}")

    # ── PARALLEL PHASE 1: Images + BGM + Script simultaneously ────────
    log("🚀 Phase 1: Images + BGM + Script in parallel...")

    def fetch_images_and_bgm():
        result = _assemble_pipeline_images(deity, deity_en, topic, day, fallback_image=image)
        bgm_path = ensure_bgm(deity)
        return result, bgm_path

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        media_future  = pool.submit(fetch_images_and_bgm)
        script_future = pool.submit(generate_script, config["topic"], deity)

        (image_result, deity_bgm) = media_future.result()
        script = script_future.result()

    images = image_result.image_paths
    thumb_bg = image_result.thumb_bg or image_result.best_real_photo
    real_photo_count = image_result.real_photo_count  # used by quality gate

    if not images and image:
        images = find_images(image)

    if deity_bgm and os.path.exists(deity_bgm):
        bgm = deity_bgm
        log(f"🎵 Using deity BGM: {deity_bgm}")

    log(f"  📦 Total images for video: {len(images)} (real_photos={real_photo_count})")

    if not script or len(script.strip()) < 100:
        log("  ❌ Script empty — aborting pipeline")
        return None

    log(f"✅ Script: {len(script)} chars")

    # ── PARALLEL PHASE 2: Metadata + Thumbnail simultaneously ────────
    # generate_script (Phase 1) used GitHub Models (task=script)
    # generate_metadata must use Groq to avoid hammering GitHub in parallel
    log("🚀 Phase 2: Metadata (Groq) + Thumbnail in parallel...")

    def generate_metadata_groq_first(cfg):
        """Metadata call that prefers Groq — avoids GitHub race with script."""
        year = datetime.datetime.now().year
        prompt = COMBINED_META_PROMPT.format(**cfg, year=year)
        log("  Generating all metadata in one call...")
        try:
            raw = call_llm(prompt, task="metadata", prefer="groq", max_retries=3, max_tokens=2000)
            clean = _sanitize_llm_json(raw)
            data = json.loads(clean)
            desc = data.get("description", "")
            if desc.strip().startswith("{") or desc.strip().startswith("["):
                data["description"] = _build_description(cfg, data)
            return data
        except Exception as e:
            log(f"  ⚠️ Metadata failed ({e}) — using fallback")
            return _build_fallback_metadata(cfg, year)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        metadata_future = pool.submit(generate_metadata_groq_first, config)
        thumb_future    = pool.submit(
            generate_thumbnail,
            config.get("topic", topic), deity, day,
            deity_en, thumb_bg
        )
        metadata   = metadata_future.result()
        thumb_path = thumb_future.result()

    log(f"  Title: {metadata.get('title','')[:60]}...")
    metadata["topic"]          = config["topic"]
    metadata["deity"]          = deity
    metadata["script_preview"] = script[:500]
    metadata["hook_style"]     = LAST_GENERATED_HOOK_STYLE
    metadata["format"]         = LAST_GENERATED_FORMAT_NAME

    if thumb_path:
        metadata["thumbnail_path"] = thumb_path
        log(f"  ✅ Thumbnail: {os.path.basename(thumb_path)}")

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
        f.write(f"TOPIC: {config['topic']}\n")
        f.write(f"HOOK: {metadata.get('hook_style', '')}\n")
        f.write(f"FORMAT: {metadata.get('format', '')}\n")
        f.write(f"CREATED: {datetime.datetime.now().isoformat()}\n")

    # ── SEQUENTIAL: Main video (cannot parallelise — needs all inputs) ─
    log("🎬 Creating video...")
    title_short = metadata.get("title", "")[:50]
    video = create_video(script, images, day, bgm, bgm_vol,
                         deity_name=deity, deity_en=deity_en, title_short=title_short)

    _dur = 360
    if video and os.path.exists(video):
        try:
            import subprocess as _sp
            _r = _sp.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1", video],
                         capture_output=True, text=True, timeout=10)
            _dur = max(30, int(float(_r.stdout.strip() or "360")))
        except Exception:
            pass
    metadata["duration_seconds"] = _dur
    log(f"  ⏱️ Duration: {_dur}s")

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    if video:
        log(f"✅ VIDEO: {video}")

        thumb_text = extract_thumbnail_text(metadata.get("title", topic), deity)
        diversity_engine = get_diversity_engine()
        diversity_engine.register_pattern(
            topic=config["topic"],
            hook=metadata.get("hook_style", "default"),
            fmt=metadata.get("format", "default"),
            thumbnail_text=thumb_text,
            deity=deity,
        )

        log("📱 Generating 3 Shorts clips...")
        generated_shorts = generate_shorts_from_video(
            source_video=video,
            output_name=day,
            base_metadata=metadata,
            deity_name=deity,
            bg_image_path=thumb_bg,
        )
        if generated_shorts:
            log(f"✅ SHORTS: {len(generated_shorts)} clips ready")
            queue_shorts_for_upload(generated_shorts, privacy=privacy)
        else:
            log("⚠️ Shorts generation produced no clips")

        log(f"📺 {metadata['title']}")
        save_used_topic(topic)

        if upload:
            retention_report = validate_retention(script)
            quality_report = validate_video_ready(
                video_path=video,
                script=script,
                retention_score=retention_report.score,
                real_photo_count=real_photo_count,
            )
            quality_report.log_summary(log)
            if not quality_report.passed:
                log("⚠️ Quality gate failed — upload skipped, video saved locally")
                upload = False

        if upload:
            if "description" in metadata and metadata.get("duration_seconds", 0) > 30:
                metadata["description"] = fix_chapter_timestamps(
                    metadata["description"], metadata["duration_seconds"])
            log("⬆️ Uploading to YouTube...")
            try:
                vid = upload_to_youtube(video, metadata, privacy)
                if vid:
                    log(f"✅ Uploaded: https://youtu.be/{vid}")
                    with open(f"{METADATA_DIR}/{day}.txt", "a", encoding="utf-8") as meta_append:
                        meta_append.write(f"VIDEO_ID: {vid}\n")
                    upload_pending_from_queue()
                    # Track in video_series.json (same as TT/NN for visibility)
                    try:
                        import datetime as _dt, json as _json
                        _series_path = "video_series.json"
                        _series = {}
                        if os.path.exists(_series_path):
                            with open(_series_path, encoding="utf-8") as _sf:
                                _series = _json.load(_sf)
                        _deity_key = deity_en.lower().replace(" ", "_") if deity_en else "general"
                        if _deity_key not in _series:
                            _series[_deity_key] = []
                        _series[_deity_key].append({
                            "part": len(_series[_deity_key]) + 1,
                            "topic": topic,
                            "video_id": vid,
                            "date": _dt.datetime.now().isoformat()
                        })
                        with open(_series_path, "w", encoding="utf-8") as _sf:
                            _json.dump(_series, _sf, ensure_ascii=False, indent=2)
                        log(f"  📊 Tracked in video_series.json")
                    except Exception as _track_err:
                        log(f"  ⚠️ Series tracking failed: {_track_err}")
                else:
                    log("⚠️ Upload skipped (auth issue) — video saved locally")
            except Exception as e:
                if is_quota_exceeded(e):
                    log(f"⚠️ YouTube quota exceeded — queued for next run")
                    queue_for_retry(video, metadata, privacy)
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
    deity_en = config.get("deity_en", "")
    log("📸 Fetching images...")
    image_result = _assemble_pipeline_images(
        deity, deity_en, topic, f"trending_{safe_name}", fallback_image=image
    )
    images = image_result.image_paths
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
    metadata = {}

    if video:
        metadata_path = f"{METADATA_DIR}/{day}.txt"
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
# SLEEP MUSIC MODULE
# ═══════════════════════════════════════════════

SLEEP_VIDEO_DURATION  = 10800  # 3 hours
CHANNEL_HANDLE        = "@aalayamani"
SLEEP_OUTPUT_DIR      = "sleep_videos"
SLEEP_THUMBS_DIR      = "sleep_thumbnails"
SLEEP_AUDIO_CACHE_DIR = "sleep_audio_cache"

MUSIC_PROFILES = {
    "174hz_pain_relief": {
        "title":       "174 Hz — வலி நிவாரணம் & ஆழ்ந்த தூக்கம் | 3 மணி நேர இசை",
        "title_en":    "174 Hz Solfeggio | Pain Relief Deep Sleep | 3 Hours",
        "description": "174 Hz — அடிப்படை சோல்ஃபெஜியோ அதிர்வெண். இந்த இசை உடல் வலியை குறைக்கும், ஆழமான தூக்கத்தை தரும்.",
        "tags":        "174hz,solfeggio,deep sleep tamil,pain relief,தூக்க இசை,meditation music tamil",
        "freq1": 174.0, "freq2": 87.0,  "freq3": 261.0,
        "nature": "pink", "nature_vol": 0.06,
        "binaural_beat": 3.5,
        "category": "sleep",
    },
    "285hz_healing": {
        "title":       "285 Hz — செல் குணமாதல் & தியானம் | 3 மணி நேர இசை",
        "title_en":    "285 Hz Healing Frequency | Tamil Meditation | 3 Hours",
        "description": "285 Hz — உடல் செல்களை குணப்படுத்தும் அதிர்வெண்.",
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
        "binaural_beat": 6.0,
        "category": "anxiety",
    },
    "417hz_change": {
        "title":       "417 Hz — மாற்றம் & எதிர்மறையை அகற்றும் இசை | 3 Hours",
        "title_en":    "417 Hz | Undoing Situations | Tamil Sleep Music",
        "description": "417 Hz — பழைய பாதங்களை அழிக்கும், மாற்றத்தை ஏற்படுத்தும் அதிர்வெண்.",
        "tags":        "417hz,change frequency,sleep tamil,negative energy,meditation",
        "freq1": 417.0, "freq2": 208.5, "freq3": 625.5,
        "nature": "pink", "nature_vol": 0.05,
        "binaural_beat": 5.0,
        "category": "sleep",
    },
    "528hz_dna": {
        "title":       "528 Hz — DNA சரிசெய்யும் இசை & ஆழ்ந்த தூக்கம் | 3 Hours",
        "title_en":    "528 Hz DNA Repair | Love Frequency | Tamil Sleep Music",
        "description": "528 Hz — 'அன்பின் அதிர்வெண்'. DNA சரிசெய்யும், மன அமைதி தரும்.",
        "tags":        "528hz,dna repair,love frequency,sleep music tamil,healing,தூக்க இசை",
        "freq1": 528.0, "freq2": 264.0, "freq3": 792.0,
        "nature": "pink", "nature_vol": 0.04,
        "binaural_beat": 3.0,
        "category": "healing",
    },
    "639hz_relationships": {
        "title":       "639 Hz — உறவுகளை சரிசெய்யும் இசை | தியானம் | 3 Hours",
        "title_en":    "639 Hz Harmonizing Relationships | Tamil Meditation Music",
        "description": "639 Hz — குடும்ப உறவுகள், நட்பு, அன்பை மேம்படுத்தும் அதிர்வெண்.",
        "tags":        "639hz,relationship healing,heart chakra,meditation tamil,harmony",
        "freq1": 639.0, "freq2": 319.5, "freq3": 958.5,
        "nature": "pink", "nature_vol": 0.05,
        "binaural_beat": 7.0,
        "category": "meditation",
    },
    "741hz_intuition": {
        "title":       "741 Hz — உள்ளுணர்வை விழிப்படுத்தும் இசை | 3 மணி நேரம்",
        "title_en":    "741 Hz Awakening Intuition | Tamil Meditation | 3 Hours",
        "description": "741 Hz — ஆறாவது புலன், உள்ளுணர்வை விழிப்படுத்தும் அதிர்வெண்.",
        "tags":        "741hz,intuition,sixth sense,meditation music tamil,chakra healing",
        "freq1": 741.0, "freq2": 370.5, "freq3": 247.0,
        "nature": "white_rain", "nature_vol": 0.06,
        "binaural_beat": 8.0,
        "category": "meditation",
    },
    "852hz_spiritual": {
        "title":       "852 Hz — ஆன்மீக ஒழுங்கை மீட்டெடுக்கும் இசை | 3 Hours",
        "title_en":    "852 Hz Return to Spiritual Order | Tamil Sleep Music",
        "description": "852 Hz — ஆன்மீக விழிப்புணர்வை அதிகரிக்கும் அதிர்வெண்.",
        "tags":        "852hz,spiritual awakening,third eye,meditation tamil,sleep music",
        "freq1": 852.0, "freq2": 426.0, "freq3": 284.0,
        "nature": "pink", "nature_vol": 0.04,
        "binaural_beat": 4.5,
        "category": "spiritual",
    },
    "963hz_crown": {
        "title":       "963 Hz — கிரீட சக்கரம் & தெய்வீக இணைப்பு | 3 மணி நேரம்",
        "title_en":    "963 Hz Crown Chakra Activation | Tamil Meditation Music",
        "description": "963 Hz — மிக உயர்ந்த சோல்ஃபெஜியோ அதிர்வெண்.",
        "tags":        "963hz,crown chakra,divine connection,meditation,spiritual music tamil",
        "freq1": 963.0, "freq2": 481.5, "freq3": 321.0,
        "nature": "pink", "nature_vol": 0.03,
        "binaural_beat": 3.0,
        "category": "spiritual",
    },
    "murugan_174hz": {
        "title":       "முருகன் 174 Hz — ஆழ்ந்த தூக்கம் & வழிபாடு | 3 மணி நேரம்",
        "title_en":    "Lord Murugan 174 Hz Devotional Sleep Music | 3 Hours",
        "description": "முருகன் வழிபாட்டு அதிர்வெண் 174 Hz — ஆழ்ந்த தூக்கத்தை தரும் தெய்வீக இசை.",
        "tags":        "murugan,devotional sleep music,174hz,tamil god music",
        "freq1": 174.0, "freq2": 348.0, "freq3": 261.0,
        "nature": "pink", "nature_vol": 0.06,
        "binaural_beat": 4.0,
        "category": "devotional",
    },
    "sivan_136hz": {
        "title":       "சிவன் 136.1 Hz OM அதிர்வெண் — தியானம் & தூக்கம் | 3 Hours",
        "title_en":    "Lord Shiva 136Hz OM Frequency | Deep Meditation | 3 Hours",
        "description": "136.1 Hz — பூமியின் OM அதிர்வெண். சிவனின் தியான அதிர்வெண்.",
        "tags":        "shiva,om frequency,136hz,meditation,deep sleep,devotional",
        "freq1": 136.1, "freq2": 272.2, "freq3": 408.3,
        "nature": "brown", "nature_vol": 0.07,
        "binaural_beat": 3.5,
        "category": "devotional",
    },
    "vinayagar_528hz": {
        "title":       "விநாயகர் 528 Hz — தடைகளை நீக்கும் தூக்க இசை | 3 Hours",
        "title_en":    "Lord Ganesha 528Hz | Remove Obstacles | Tamil Sleep Music",
        "description": "528 Hz — விநாயகருக்கு உகந்த மாற்ற அதிர்வெண். தடைகளை நீக்கும்.",
        "tags":        "ganesha,528hz,obstacle remover,sleep music,devotional tamil",
        "freq1": 528.0, "freq2": 264.0, "freq3": 396.0,
        "nature": "pink", "nature_vol": 0.04,
        "binaural_beat": 5.0,
        "category": "devotional",
    },
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
        "description": "இயற்கை ஆற்று சத்தம் + 2Hz delta binaural beats.",
        "tags":        "river sounds,delta waves,deep sleep tamil,binaural beats,natural sounds",
        "freq1": 150.0, "freq2": 152.0, "freq3": 75.0,
        "nature": "brown", "nature_vol": 0.40,
        "binaural_beat": 2.0,
        "category": "sleep",
    },
    "forest_alpha": {
        "title":       "காடு சத்தம் + Alpha Waves — மன அமைதி & Relaxation | 3 Hours",
        "title_en":    "Forest Sounds + Alpha Waves | Stress Relief Tamil | 3 Hours",
        "description": "காட்டு சத்தம் + 10Hz alpha binaural beats. மன அழுத்தம் குறைக்கும்.",
        "tags":        "forest sounds,alpha waves,relaxation music tamil,stress relief,meditation",
        "freq1": 250.0, "freq2": 260.0, "freq3": 125.0,
        "nature": "pink", "nature_vol": 0.25,
        "binaural_beat": 10.0,
        "category": "relaxation",
    },
    "432hz_universal": {
        "title":       "432 Hz — பிரபஞ்சத்தின் அதிர்வெண் | ஆழ்ந்த தூக்கம் | 3 Hours",
        "title_en":    "432 Hz Universal Frequency | Deep Sleep Tamil | 3 Hours",
        "description": "432 Hz — இயற்கையின் அதிர்வெண்.",
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

    f1_left  = f1
    f1_right = f1 + bb

    if nature == 'rain':
        nature_filter = f"[3:a]highpass=f=800,lowpass=f=5000,volume={nvol}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=white:r=44100:a=0.5"
    elif nature == 'brown':
        nature_filter = f"[3:a]lowpass=f=300,volume={nvol*1.5}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=pink:r=44100:a=0.5"
    elif nature == 'white_rain':
        nature_filter = f"[3:a]highpass=f=2000,lowpass=f=8000,volume={nvol}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=white:r=44100:a=0.4"
    else:
        nature_filter = f"[3:a]lowpass=f=600,volume={nvol}[nat]"
        nature_input  = f"anoisesrc=d={duration}:c=pink:r=44100:a=0.3"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={f1_left}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={f1_right}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={f2}:duration={duration}",
        "-f", "lavfi", "-i", nature_input,
        "-f", "lavfi", "-i", f"sine=frequency={f3}:duration={duration}",
        "-filter_complex",
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

    for y in range(H):
        t = y / H
        r = int(bg1[0] + (bg2[0]-bg1[0]) * t)
        g = int(bg1[1] + (bg2[1]-bg1[1]) * t)
        b = int(bg1[2] + (bg2[2]-bg1[2]) * t)
        draw.line([(0,y),(W,y)], fill=(r,g,b))

    cx, cy = W//2, H//2
    for i in range(8):
        r2  = 80 + i*55
        draw.ellipse([cx-r2, cy-r2, cx+r2, cy+r2],
                     outline=(*accent,), width=max(1, 3-i//3))

    freq_text = f"{profile['freq1']:.0f} Hz"
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 120)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf", 42)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        font_lg = font_md = font_sm = ImageFont.load_default()

    bbox = draw.textbbox((0,0), freq_text, font=font_lg)
    tw = bbox[2]-bbox[0]
    draw.text(((W-tw)//2, 140), freq_text, font=font_lg,
              fill=(*accent, 255), stroke_width=2, stroke_fill=(0,0,0,200))

    tamil_title = profile['title'].split('|')[0].strip()[:35]
    try:
        bbox2 = draw.textbbox((0,0), tamil_title, font=font_md)
        tw2 = bbox2[2]-bbox2[0]
        draw.text(((W-tw2)//2, 320), tamil_title, font=font_md,
                  fill=(255,255,255,240), stroke_width=1, stroke_fill=(0,0,0))
    except: pass

    draw.rounded_rectangle([W-200, H-65, W-20, H-20], radius=10,
                           fill=(*accent, 180))
    draw.text((W-185, H-58), "3 HOURS", font=font_sm, fill=(255,255,255))
    draw.text((30, H-55), CHANNEL_HANDLE, font=font_sm, fill=(200,200,200,200))

    img.save(thumb_path, "JPEG", quality=95)
    log(f"  ✅ Thumbnail: {thumb_path}")
    return thumb_path


def create_sleep_video(audio_path, profile_key, profile):
    """Create video: colour background + 3-hour audio."""
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

    cmd = [
        "ffmpeg", "-y",
        "-f",    "lavfi",
        "-i",    f"color=c=0x{hex_col}:size=1280x720:rate=1",
        "-i",    audio_path,
        "-c:v",  "libx264", "-preset", "ultrafast", "-crf", "51",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-c:a",  "copy",
        "-shortest",
        "-movflags", "+faststart",
        "-t",    str(duration),
        video_path
    ]
    r = run(cmd, timeout=1800)

    if r.returncode == 0:
        size_mb = os.path.getsize(video_path) / (1024*1024)
        log(f"  ✅ Video: {video_path} ({size_mb:.0f}MB, {time.time()-t0:.0f}s)")
        return video_path
    else:
        log(f"  ❌ Video failed: {r.stderr[-300:]}")
        return None


def _save_playlist_id(pid, playlist_file="sleep_playlist_id.txt"):
    try:
        with open(playlist_file, "w") as f:
            f.write(pid)
        log(f"  ✅ Playlist ID written to {playlist_file}: {pid}")
    except Exception as e:
        log(f"  ⚠️ Could not write playlist ID: {e}")


def _get_or_create_sleep_playlist(yt):
    """Get existing sleep playlist ID or auto-create one."""
    playlist_file = "sleep_playlist_id.txt"

    pid = os.environ.get("SLEEP_PLAYLIST_ID", "").strip()
    if pid:
        log(f"  📋 Using SLEEP_PLAYLIST_ID secret: {pid}")
        return pid

    if os.path.exists(playlist_file):
        pid = open(playlist_file).read().strip()
        if pid:
            log(f"  📋 Using saved playlist: {pid}")
            return pid

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

    try:
        resp = yt.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title":           "ஆழ்ந்த தூக்கம் — Tamil Sleep & Meditation Music",
                    "description": (
                        "தமிழ் தியான இசை — Solfeggio frequencies, binaural beats, "
                        "deity frequencies & nature sounds.\n\n"
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
        f"🔔 Subscribe: {CHANNEL_HANDLE}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"இந்த இசையை தினமும் படுக்கும் முன்பு கேளுங்கள்.\n"
        f"Use headphones for binaural beat effect.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
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

        if thumb_path and os.path.exists(thumb_path):
            try:
                yt.thumbnails().set(
                    videoId=vid_id,
                    media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg")
                ).execute()
                log("  ✅ Thumbnail set")
            except: pass

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
    if not SLEEP_MUSIC_ENABLED:
        log("⏸️ Sleep music disabled (set SLEEP_MUSIC_ENABLED=true to re-enable)")
        return None

    profile_key = profile_key or get_todays_profile()
    profile     = MUSIC_PROFILES[profile_key]

    log(f"\n🎵 Sleep Music: {profile_key}")
    log(f"   {profile['title'][:60]}...")

    for d in [SLEEP_OUTPUT_DIR, SLEEP_THUMBS_DIR, SLEEP_AUDIO_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

    audio = generate_music(profile_key, profile, SLEEP_VIDEO_DURATION)
    if not audio:
        log("❌ Sleep music generation failed"); return None

    try:
        thumb = generate_sleep_thumbnail(profile_key, profile)
    except Exception as e:
        log(f"  ⚠️ Sleep thumbnail failed: {e}"); thumb = None

    video = create_sleep_video(audio, profile_key, profile)
    if not video:
        log("❌ Sleep video creation failed"); return None

    log(f"✅ Sleep video ready: {video}")

    if upload:
        log("⬆️ Uploading sleep music...")
        vid_id = upload_sleep_video(video, thumb, profile)
        if vid_id:
            log(f"✅ Sleep music live: https://youtu.be/{vid_id}")
        return vid_id

    return video


def main():
    parser = argparse.ArgumentParser(
        description="ஆலய மணி — Fully Automated Devotional Content Bot v5.1"
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
    print("  ஆலய மணி — Full Automation v5.1")
    print("  🆓 Free LLM stack (Groq/Gemini/GitHub/Cerebras)")
    print("  🎭 Deity voices  🪝 Varied hooks")
    print("  📸 Free images (Wikimedia/Pollinations/scenes)")
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
        config = {
            "topic":    args.topic,
            "deity":    "",
            "deity_en": "",
            "emoji":    "🙏",
            "hashtags": "#ஆலயமணி #AalayaMani #TamilDevotional",
        }
        print("Fetching images...")
        image_result = _assemble_pipeline_images("", "", args.topic, args.output, fallback_image=args.image)
        images = image_result.image_paths
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
        if day not in DAY_DEITY_MAP:
            log(f"  ⚠️ {day} not in DAY_DEITY_MAP — using sunday default")
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

