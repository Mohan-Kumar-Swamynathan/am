#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║            ஆலய மணி — FULLY AUTOMATED BOT v3.0               ║
║  Script + Voice + Video + Trending + YouTube Upload          ║
║  Anti-Monotony: Deity voices, varied hooks, Pexels images    ║
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
GROQ_MODEL      = "llama-3.3-70b-versatile"
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
- குறைந்தது 5000 தமிழ் எழுத்துகள். ஒவ்வொரு பிரிவும் 5-8 விரிவான வாக்கியங்கள்.
- பேச்சு வழக்கில் எழுதுங்கள் — essay இல்லை, conversation.
- தலைப்புகள், அடைப்புக்குறிகள், bullet points வேண்டாம். தொடர் பேச்சு மட்டும்.
- "NO REPETITION" — ஒரு வாக்கியம்கூட முந்தையதை மீண்டும் சொல்ல வேண்டாம்.
- கேட்பவர் "இது என்னக்காகவே செய்யப்பட்டது" என்று உணரவேண்டும்.
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
    random.shuffle(queries)

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
    if not PEXELS_API_KEY:
        print("WARNING: PEXELS_API_KEY not set — will use local images only")
        print("  Get free key: https://www.pexels.com/api/")
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
    for d in [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR, PEXELS_DIR]:
        os.makedirs(d, exist_ok=True)


def call_llm(prompt, max_retries=3):
    """Call LLM: Groq → Gemini fallback. Retries + exponential backoff on failure."""
    errs = []

    # Provider 1: Groq (if key set)
    if GROQ_API_KEY and Groq:
        for attempt in range(max_retries):
            try:
                client = Groq(api_key=GROQ_API_KEY)
                resp = client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=GROQ_MODEL, temperature=0.85, max_tokens=2500,
                )
                return resp.choices[0].message.content
            except Exception as e:
                wait = 10 * (attempt + 1)
                log(f"⏳ Groq error (attempt {attempt+1}/{max_retries}), retry in {wait}s: {str(e)[:80]}")
                errs.append(str(e))
                time.sleep(wait)
        log("⚠️ Groq failed, falling back to Gemini...")

    # Provider 2: Gemini fallback
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

    month_num = now.month
    tamil_month, month_trend = TAMIL_MONTHS.get(month_num, ("", ""))

    day_deity_map = {
        "Monday":    "சிவன் (Shiva)",
        "Tuesday":   "முருகன் (Murugan)",
        "Wednesday": "விநாயகர் (Vinayagar)",
        "Thursday":  "பெருமாள் (Perumal/Guru)",
        "Friday":    "லட்சுமி/அம்மன் (Lakshmi/Amman)",
        "Saturday":  "ஐயப்பன்/சனி (Ayyappan/Shani)",
        "Sunday":    "சூரியன் (Surya/Navagraha)",
    }
    today_deity = day_deity_map.get(day_name, "")

    trends_data = ""
    try:
        trends_data += fetch_google_trends() or ""
        trends_data += fetch_youtube_trending() or ""
        trends_data += fetch_god_temple_news() or ""
    except:
        pass

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

def generate_script(topic, deity=""):
    t0 = time.time()

    # Pick anti-monotony elements randomly
    deity_voice = DEITY_VOICE.get(deity, (
        "இயல்பான, அன்பான, பக்தி மிகுந்த குரலில் பேசுங்கள். "
        "கேட்பவர் ஒரு நேசமான நண்பரிடம் பேசுவதுபோல் உணரட்டும்."
    ))
    hook_style = random.choice(HOOK_STYLES)
    content_struct = random.choice(CONTENT_STRUCTURES)
    closing_style = random.choice(CLOSING_STYLES)

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

    text = call_llm(prompt)

    # Retry once if script is too short
    if len(text) < 3000:
        log(f"  Script too short ({len(text)} chars), retrying...")
        retry_prompt = prompt + (
            f"\n\nமுக்கியம்: உங்கள் முந்தைய பதில் {len(text)} எழுத்துகள் மட்டுமே. "
            "குறைந்தது 5000 எழுத்துகள் எழுதுங்கள். ஒவ்வொரு பிரிவையும் விரிவுபடுத்துங்கள்."
        )
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


def build_video_filter(images, total_frames, fps=25):
    """
    Build ffmpeg filter_complex string for multi-image Ken Burns + crossfade.
    Returns (num_inputs, filter_string, output_label).
    """
    num = len(images)
    seg_frames = total_frames // num
    overlap = int(fps * 0.8)

    filters = []
    for i in range(num):
        z = "if(lte(on,1),1.0,min(1.0+0.0015*on,1.25))"
        filters.append(
            f"[{i}:v]loop=loop=-1:size=1:start=0,"
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='{z}':d={seg_frames}:fps={fps}:s=1920x1080,"
            f"trim=0:{seg_frames / fps:.2f},setpts=PTS-STARTPTS[v{i}]"
        )

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


def create_video(script_text, images_input, output_name, bgm, bgm_vol=0.20):
    ensure_dirs()

    script_file = f"/tmp/{output_name}_script.txt"
    voice_file  = f"/tmp/{output_name}_voice.mp3"
    human_file  = f"/tmp/{output_name}_human.mp3"
    mixed_file  = f"/tmp/{output_name}_mixed.mp3"
    video_file  = f"{OUTPUT_DIR}/{output_name}_video.mp4"
    short_file  = f"{SHORTS_DIR}/{output_name}_short.mp4"

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
        fo  = max(0, dur - 3)
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

    log("🎬 Step 4/5 Video (Ken Burns + slideshow)...")
    t0 = time.time()

    # Resolve images — support list or path string
    if isinstance(images_input, list):
        images = [f for f in images_input if os.path.exists(f)]
    else:
        images = find_images(images_input)

    if not images:
        log(f"❌ No images found")
        return None

    log(f"🖼️ Using {len(images)} images: {[os.path.basename(i)[:20] for i in images]}")

    fps = 25
    total_frames = max(int(dur * fps), 25)
    num_inputs, vfilter, vlabel = build_video_filter(images, total_frames, fps)

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

    log("📱 Step 5/5 Short...")
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
    env_creds = get_token_from_env()
    if env_creds:
        creds = env_creds

    if not creds and os.path.exists(YOUTUBE_TOKEN_FILE):
        with open(YOUTUBE_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log(f"⚠️ YouTube token refresh failed: {e}")
                log("   Re-run --auth-youtube locally and update YOUTUBE_TOKEN_BASE64 secret")
                return None
        else:
            if not os.path.exists(YOUTUBE_CLIENT_SECRETS):
                log(f"⚠️ {YOUTUBE_CLIENT_SECRETS} not found — skipping upload")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    YOUTUBE_CLIENT_SECRETS, YOUTUBE_SCOPES)
                creds = flow.run_local_server(port=8080)
            except Exception as e:
                log(f"⚠️ YouTube OAuth flow failed: {e}")
                return None
        try:
            with open(YOUTUBE_TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)
        except Exception:
            pass

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"⚠️ YouTube service build failed: {e}")
        return None


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
            "categoryId": "27",
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
    """Full pipeline for one day: Pexels fetch + script + metadata (parallel) → video → upload."""
    bgm = bgm or BGM_FILE
    config = dict(DAY_CONFIG[day])
    t_start = datetime.datetime.now()

    topic    = config["topic"]
    emoji    = config["emoji"]
    deity    = config["deity"]
    deity_en = config["deity_en"]

    log(f"{'='*50}")
    log(f"{emoji} {deity} — {deity_en}")
    log(f"{'='*50}")

    festival = get_festivals_today()
    if festival:
        enhanced_topic = f"{festival} - {topic}"
        log(f"📅 Festival today: {festival}")
        config["topic"] = enhanced_topic

    # Fetch Pexels images for this deity
    log("📸 Fetching Pexels images...")
    images = get_images_for_deity(deity, day)
    if image and not images:
        images = find_images(image)

    # Run script & metadata generation in parallel
    log("🤖 Generating script + YouTube metadata (parallel)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sf = pool.submit(generate_script, config["topic"], deity)
        mf = pool.submit(generate_metadata, config)
        script   = sf.result()
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
    video = create_video(script, images, day, bgm, bgm_vol)

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    if video:
        log(f"✅ VIDEO: {video}")
        log(f"✅ SHORT: {SHORTS_DIR}/{day}_short.mp4")
        log(f"📺 {metadata['title']}")

        if upload:
            log("⬆️ Uploading to YouTube...")
            try:
                vid = upload_to_youtube(video, metadata, privacy)
                if vid:
                    log("✅ Upload complete")
                else:
                    log("⚠️ Upload skipped (auth issue) — video saved locally")
            except Exception as e:
                log(f"⚠️ Upload failed (non-fatal): {e}")
    else:
        log("❌ Video creation failed")

    log(f"⏱️ Total: {elapsed:.0f}s")
    return video


def process_trending(image=None, bgm=None, bgm_vol=0.20, upload=False, privacy="public"):
    """Trending topic based video generation."""
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
        "topic":    topic,
        "deity":    "",
        "deity_en": "",
        "emoji":    "🙏",
        "hashtags": "#தமிழ்பக்தி #ஆலயமணி #AalayaMani #TrendingDevotional",
    }

    # Fetch generic devotional images from Pexels
    log("📸 Fetching Pexels images for trending topic...")
    images = get_images_for_deity("", f"trending_{safe_name}")
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
            "metadata":   metadata,
            "day":        day,
            "created":    datetime.datetime.now().isoformat(),
            "status":     "pending",
        })
        save_queue(queue)
        print(f"  ✅ Queued for upload: {os.path.basename(video)}")

    if trending_topic and DAY_CONFIG[day]["topic"] not in trending_topic:
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
    parser.add_argument("--auth-youtube",   action="store_true", help="Authenticate YouTube OAuth")
    args = parser.parse_args()

    check_prerequisites()
    ensure_dirs()

    print("\n========================================")
    print("  ஆலய மணி — Full Automation v3.0")
    print("  🎭 Deity voices  🪝 Varied hooks")
    print("  📸 Pexels images  📋 8 content formats")
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
