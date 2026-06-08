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
CEREBRAS_KEY    = os.environ.get("CEREBRAS_API_KEY", "")
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
    "vibrato=f=5.5:d=0.04,"
    "aecho=0.7:0.25:30|50:0.12|0.08,"
    "acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=2,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)

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
- கதையில் கதாபாத்திரங்களுக்கு உண்மையான தமிழ் பெயர்கள் கொடுங்கள் (கோவிந்தன், லக்ஷ்மி, ரமேஷ் போன்றவை).
- உணர்ச்சியான தருணங்களில் "..." பயன்படுத்துங்கள். வேகமான பகுதிகளில் குறுகிய வாக்கியங்கள்.
- கேட்பவர் "இது என்னக்காகவே செய்யப்பட்டது" என்று உணரவேண்டும்.

YOUTUBE RETENTION RULES:
1. HOOK (0-15s): Start with the devotee's emotion, not the deity's name.
   Bad: "இன்று நாம் முருகன் பற்றி பேசுவோம்..."
   Good: "இந்த ஒரு தவறை பண்ணினால் கோயில் போனாலும் பலன் கிடைக்காது..."

2. PATTERN INTERRUPT every 30s: "ஆனால் இதை எத்தனை பேர் தெரிஞ்சுக்கிறோம்?"

3. PERSONAL RELEVANCE: Connect to viewer's daily life.
   "நீங்கள் தினமும் செய்யும் இந்த ஒரு செயல்..." makes them stay.

4. SPECIFIC FACTS: Exact mantra counts, specific festival dates, real temple names.
   "சரியாக 108 முறை" > "பல முறை"

5. EMOTIONAL CLOSE: End with hope/comfort, not instruction.
   "இன்று இரவு தூங்கும்முன் இதை ஒரு முறை சொல்லுங்கள் — நாளை வித்தியாசம் தெரியும்"
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
TAMIL MONTH/FESTIVAL CONTEXT: {festival_context}
RECENTLY USED TOPICS — DO NOT repeat: {recent_topics}

CONTENT CATEGORY ROTATION (rotate through all — never same category 2 days in a row):
1. DEITY STORY — lesser-known story or legend about a specific god/goddess
2. TEMPLE MYSTERY — surprising fact about a famous Tamil Nadu temple
3. FESTIVAL SIGNIFICANCE — why we do THIS ritual exactly, the real meaning
4. MANTRA EXPLANATION — what this mantra actually means, the science behind it
5. SPIRITUAL PRACTICE — how to do a specific pooja correctly, step by step
6. DEVOTIONAL HISTORY — how this tradition started, the historical story behind it

GREAT TOPIC FORMULA = Specific + Surprising + Devotional
Examples:
- "திருவண்ணாமலை கிரிவலம் — ஒரு முறை செய்தால் என்ன நடக்கும்? அறிவியல் விளக்கம்" (Temple mystery)
- "முருகன் வேல் ஏன் கையில் இருக்கு? யாரும் சொல்லாத காரணம்" (Deity story)
- "காலை பூஜை ஏன் சரியாக செய்யணும்? இந்த நேரம் ஏன் முக்கியம்?" (Spiritual practice)
- "நவராத்திரி ஒன்பது நாளும் எந்த தேவியை வழிபட வேண்டும்? ஒவ்வொரு நாளும் பலன்" (Festival)

CHECK today's date: {date}
- Is any major festival upcoming in next 7 days? If yes, cover it.
- Is this a special day for any deity? If yes, prioritise that deity.

Return ONLY valid JSON:
{{
  "topic": "<specific devotional topic with a surprising or lesser-known angle>",
  "deity": "<சிவன்|முருகன்|விநாயகர்|பெருமாள்|லட்சுமி|ஐயப்பன்|அம்மன்|நடராஜர்|கிருஷ்ணர்|generic>",
  "category_number": <1-6>,
  "hook_angle": "<the most surprising or spiritually significant fact>",
  "reason": "<why this is different from recent topics>"
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
            "categoryId": "22",
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

    # Fetch Pexels images for this deity
    log("📸 Fetching Pexels images...")
    images = get_images_for_deity(deity, day)
    if image and not images:
        images = find_images(image)

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

    log("🎬 Creating video...")
    title_short = metadata.get("title", "")[:50]
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
    """Create video: static gradient image + 3-hour audio."""
    video_path = f"{SLEEP_OUTPUT_DIR}/{profile_key}_{datetime.date.today()}.mp4"

    # Create background image
    bg_path = f"/tmp/sleep_bg_{profile_key}.jpg"
    cat = profile.get('category', 'sleep')
    color_map = {
        "sleep": "5,10,35", "healing": "5,25,15",
        "meditation": "25,10,40", "devotional": "35,15,5",
        "spiritual": "20,5,35", "study": "5,25,35",
        "relaxation": "5,30,20", "anxiety": "5,20,35",
    }
    rgb = color_map.get(cat, "5,10,35")
    r,g,b = rgb.split(',')

    # Generate background with FFmpeg lavfi
    bg_cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=#{int(r):02x}{int(g):02x}{int(b):02x}:size=1920x1080:rate=1",
        "-vframes", "1", bg_path
    ]
    run(bg_cmd, timeout=30)

    if not os.path.exists(bg_path):
        # Fallback solid color
        run(["ffmpeg", "-y", "-f", "lavfi",
             "-i", "color=c=0x050a23:size=1920x1080:rate=1",
             "-vframes", "1", bg_path], timeout=30)

    duration = SLEEP_VIDEO_DURATION
    log(f"  🎬 Creating {duration//3600}h video...")
    t0 = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", bg_path,
        "-i", audio_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-shortest",
        "-movflags", "+faststart",
        video_path
    ]
    r = run(cmd, timeout=600)

    try: os.remove(bg_path)
    except: pass

    if r.returncode == 0:
        size_mb = os.path.getsize(video_path) / (1024*1024)
        log(f"  ✅ Video: {video_path} ({size_mb:.0f}MB, {time.time()-t0:.0f}s)")
        return video_path
    else:
        log(f"  ❌ Video failed: {r.stderr[-200:]}")
        return None



def _get_or_create_sleep_playlist(yt):
    """Get existing playlist ID or create new one. Persists to file."""
    playlist_file = "sleep_playlist_id.txt"

    # 1. Check env secret first
    pid = os.environ.get("SLEEP_PLAYLIST_ID", "").strip()
    if pid:
        return pid

    # 2. Check local file (persisted from previous run via git)
    if os.path.exists(playlist_file):
        pid = open(playlist_file).read().strip()
        if pid:
            log(f"  📋 Using saved playlist: {pid}")
            return pid

    # 3. Search existing playlists for our channel
    try:
        resp = yt.playlists().list(
            part="snippet", mine=True, maxResults=50).execute()
        for item in resp.get("items", []):
            if "தூக்கம்" in item["snippet"]["title"] or                "sleep" in item["snippet"]["title"].lower() or                "aazhn" in item["snippet"]["title"].lower():
                pid = item["id"]
                log(f"  📋 Found existing playlist: {pid}")
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
                    "title":       "ஆழ்ந்த தூக்கம் — தமிழ் தியான இசை",
                    "description": (
                        "Tamil sleep & meditation music — Solfeggio frequencies, "
                        "binaural beats, deity frequencies, nature sounds.\n"
                        "174Hz • 285Hz • 396Hz • 417Hz • 528Hz • 639Hz • 741Hz • 852Hz • 963Hz\n"
                        "Murugan • Sivan • Vinayagar frequencies\n"
                        "Rain • River • Forest soundscapes\n\n"
                        "Subscribe: @aalayamani"
                    ),
                    "defaultLanguage": "ta",
                },
                "status": {"privacyStatus": "public"}
            }
        ).execute()
        pid = resp["id"]
        log(f"  ✅ Created new playlist: {pid}")
        _save_playlist_id(pid, playlist_file)
        return pid
    except Exception as e:
        log(f"  ⚠️ Playlist creation failed: {e}")
        return ""


def _save_playlist_id(pid, playlist_file):
    """Save playlist ID to file and commit to git for persistence."""
    try:
        with open(playlist_file, "w") as f:
            f.write(pid)
        import subprocess as _sp
        _sp.run(["git", "config", "user.email", "bot@aalayamani.com"], capture_output=True)
        _sp.run(["git", "config", "user.name",  "Aalaya Mani Bot"],    capture_output=True)
        _sp.run(["git", "add", playlist_file], capture_output=True)
        _sp.run(["git", "commit", "-m", f"chore: save sleep playlist id {pid[:8]}"],
                capture_output=True)
        _sp.run(["git", "push"], capture_output=True)
        log(f"  ✅ Playlist ID saved to {playlist_file}")
    except Exception as e:
        log(f"  ⚠️ Could not save playlist ID: {e}")


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

        # Auto-create sleep playlist on first run, reuse after
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
                log(f"  ✅ Added to sleep playlist ({sleep_playlist_id})")
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




