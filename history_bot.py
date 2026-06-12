#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         THIS DAY IN HISTORY — Fully Automated Bot v1.0       ║
║  Daily historical content → English narration → YouTube      ║
║  Free stack: LLM waterfall + edge-tts + ffmpeg + Wikimedia   ║
╚═══════════════════════════════════════════════════════════════╝

Credentials needed (GitHub Secrets):
  GEMINI_KEY              — Google Gemini API key (free tier)
  GROQ_API_KEY            — Groq API key (free tier)
  GH_PAT_TOKEN            — GitHub PAT (for GitHub Models fallback)
  CEREBRAS_API_KEY        — Cerebras API key (optional, free tier)
  HISTORY_YT_TOKEN_B64    — YouTube OAuth token (base64 encoded)
  HISTORY_CLIENT_SECRETS  — YouTube client_secrets.json (base64 encoded)
  PEXELS_API_KEY          — Pexels API key (optional, for extra images)

Usage:
  python history_bot.py --today --upload      # Generate + upload today's video
  python history_bot.py --today               # Generate only (no upload)
  python history_bot.py --date 2026-06-21     # Specific date
  python history_bot.py --auth-youtube        # First-time YouTube OAuth
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import pickle
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import requests

try:
    import google.genai as genai
except ImportError:
    print("pip install google-genai"); sys.exit(1)

try:
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("pip install google-api-python-client google-auth-oauthlib"); sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# =============================================
# CONFIGURATION
# =============================================
GEMINI_KEY      = os.environ.get("GEMINI_KEY", "")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
CEREBRAS_KEY    = os.environ.get("CEREBRAS_API_KEY", "")
PEXELS_API_KEY  = os.environ.get("PEXELS_API_KEY", "")

# YouTube — separate secrets from AM channel
YOUTUBE_TOKEN_ENV       = "HISTORY_YT_TOKEN_B64"
YOUTUBE_SECRETS_ENV     = "HISTORY_CLIENT_SECRETS"
YOUTUBE_TOKEN_FILE      = "history_youtube_token.pickle"
YOUTUBE_CLIENT_SECRETS  = "history_client_secrets.json"
YOUTUBE_SCOPES          = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]

# Directories
OUTPUT_DIR      = "history_videos"
SCRIPTS_DIR     = "history_scripts"
METADATA_DIR    = "history_metadata"
THUMBS_DIR      = "history_thumbnails"
IMAGES_DIR      = "history_images"
BGM_FILE        = "history_bgm.mp3"
USED_TOPICS_FILE = "history_used_topics.txt"
UPLOAD_QUEUE_FILE = "history_upload_queue.json"

# Voice — British male for authoritative history narration
TTS_VOICE       = "en-GB-RyanNeural"
TTS_RATE        = "--rate=-5%"
TTS_PITCH       = "--pitch=-2Hz"

# Voice EQ — warm narration, subtle presence boost
NARRATION_EQ = (
    "highpass=f=80,"
    "equalizer=f=200:t=q:w=0.8:g=2,"
    "equalizer=f=1000:t=q:w=1:g=1.5,"
    "equalizer=f=3000:t=q:w=1:g=2,"
    "equalizer=f=7000:t=q:w=1:g=-2,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=50:makeup=3,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)

# Script length: 8-10 min videos (higher watch time = more YPP income)
SCRIPT_TARGET_MIN = 9000
SCRIPT_TARGET_MAX = 13000

# ── LLM Provider configs (same waterfall as AM) ──────────────────────
PROVIDERS = [
    ("groq",     "https://api.groq.com/openai/v1",        GROQ_API_KEY,  "llama-3.3-70b-versatile", "script"),
    ("gemini",   None,                                      GEMINI_KEY,    "gemini-2.5-flash",         "all"),
    ("github",   "https://models.inference.ai.azure.com", GITHUB_TOKEN,  "gpt-4o-mini",              "all"),
    ("cerebras", "https://api.cerebras.ai/v1",             CEREBRAS_KEY,  "llama-3.3-70b",            "all"),
    ("groq_fb",  "https://api.groq.com/openai/v1",        GROQ_API_KEY,  "llama3-8b-8192",           "fallback"),
]


# =============================================
# UTILITIES
# =============================================
def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def run(cmd, timeout=300):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr[:500]}")
    return result


def get_dur(f):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", f],
        capture_output=True, text=True
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def ensure_dirs():
    for d in [OUTPUT_DIR, SCRIPTS_DIR, METADATA_DIR, THUMBS_DIR, IMAGES_DIR]:
        os.makedirs(d, exist_ok=True)


def load_used_topics():
    if os.path.exists(USED_TOPICS_FILE):
        with open(USED_TOPICS_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    return []


def save_used_topic(topic):
    topics = load_used_topics()
    topics.append(topic)
    # Keep last 200 to avoid repeats
    topics = topics[-200:]
    with open(USED_TOPICS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(topics))
    # Commit to git so dedup persists across runs
    try:
        run(["git", "config", "user.email", "bot@history.com"])
        run(["git", "config", "user.name", "History Bot"])
        run(["git", "add", USED_TOPICS_FILE])
        run(["git", "commit", "-m", f"chore: used topic {topic[:40]}"])
        run(["git", "push"])
    except Exception:
        pass


# =============================================
# LLM WATERFALL (exact pattern from AM bot)
# =============================================
def _call_provider(name, base_url, api_key, model, prompt, max_tokens=5000):
    """Call a single LLM provider. Returns text or raises."""
    if not api_key:
        raise Exception(f"{name}: no API key")

    if name == "gemini":
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents=prompt)
        return resp.text
    else:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.82,
        )
        return resp.choices[0].message.content


def _is_retryable(err_str):
    return any(c in err_str for c in [
        "429", "503", "502", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
        "high demand", "overloaded", "rate_limit", "tokens per day",
        "TPD", "timeout", "timed out", "ServiceUnavailable",
    ])


def call_llm(prompt, prefer="gemini", max_tokens=5000, max_retries=3):
    """
    5-provider LLM waterfall. Same logic as aalaya_mani_bot.py.
    Tries each provider in order, retries on transient errors.
    """
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
            continue

        for attempt in range(max_retries):
            try:
                result = _call_provider(name, base_url, api_key, model, prompt, max_tokens)
                if result and result.strip():
                    if provider_name != order[0]:
                        log(f"  ✅ LLM: {name}/{model.split('-')[0]}")
                    return result.strip()
            except Exception as e:
                err = str(e)
                last_error = err
                if _is_retryable(err):
                    if any(x in err for x in ["tokens per day", "TPD", "daily"]):
                        log(f"  ⚠️ {name}: daily limit — next provider")
                        break
                    wait = min(10 * (2 ** attempt), 60)
                    log(f"  ⏳ {name} retry {attempt+1}/{max_retries} in {wait}s")
                    time.sleep(wait)
                else:
                    log(f"  ⚠️ {name}: {err[:80]} — skipping")
                    break

    raise Exception(f"All LLM providers failed. Last: {last_error[:150]}")


# =============================================
# CONTENT DISCOVERY
# =============================================
def get_today_date_info(target_date=None):
    """Return structured info about the target date."""
    d = target_date or datetime.date.today()
    return {
        "date": d,
        "day": d.day,
        "month": d.month,
        "month_name": d.strftime("%B"),
        "year": d.year,
        "display": d.strftime("%B %d"),    # e.g. "June 12"
        "ordinal": _ordinal(d.day),         # e.g. "12th"
        "safe_str": d.strftime("%Y-%m-%d"), # e.g. "2026-06-12"
    }


def _ordinal(n):
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10 if n % 100 not in (11, 12, 13) else 0, "th")
    return f"{n}{suffix}"


# Content variety — rotate through hooks
HOOK_STYLES = [
    "shocking_fact",    # Lead with the most surprising element
    "mystery_first",    # Start with a question, reveal answer mid-video
    "modern_parallel",  # Connect historical event to something happening today
    "human_drama",      # Focus on the personal story of key figures
    "counter_narrative",# Challenge the popular version of events
    "numbers_reveal",   # Lead with a striking statistic or scale
    "eyewitness",       # Tell it as if the viewer was there
    "chain_reaction",   # Show how this event triggered something else
]


def pick_hook_style():
    """Pick hook style, tracking usage for variety."""
    usage_file = "history_hook_usage.json"
    try:
        with open(usage_file) as f:
            usage = json.load(f)
    except Exception:
        usage = {}

    counts = {s: usage.get(s, 0) for s in HOOK_STYLES}
    least_used = min(counts, key=counts.get)
    usage[least_used] = usage.get(least_used, 0) + 1

    with open(usage_file, "w") as f:
        json.dump(usage, f)

    return least_used


def discover_events(date_info, used_topics):
    """
    Ask LLM to pick 3 historical events for this date.
    Returns best event as a dict.
    """
    used_str = "\n".join(used_topics[-30:]) if used_topics else "none yet"
    hook_style = pick_hook_style()

    prompt = f"""You are a content strategist for a "This Day in History" YouTube channel.

Today is {date_info['month_name']} {date_info['ordinal']}.

Task: Pick the SINGLE BEST historical event that happened on {date_info['month_name']} {date_info['day']} (any year) to make a compelling 8-10 minute YouTube video.

Requirements:
- Must be globally significant (not obscure local news)
- Must have rich human drama, surprising facts, or modern relevance
- Must NOT be from this list of recently covered topics:
{used_str}

Consider events from: wars, discoveries, inventions, political revolutions, disasters, space exploration, scientific breakthroughs, assassinations, treaties, famous births/deaths

Return ONLY a JSON object (no markdown, no explanation):
{{
  "event_title": "Short punchy title for the event",
  "year": 1969,
  "event_summary": "2-3 sentence summary of what happened",
  "why_compelling": "Why this makes a great video — the hook angle",
  "hook_style": "{hook_style}",
  "search_keywords": ["keyword1", "keyword2", "keyword3"],
  "category": "space|war|science|politics|disaster|culture|revolution|discovery"
}}"""

    try:
        raw = call_llm(prompt, prefer="gemini", max_tokens=800)
        # Strip any markdown fences
        raw = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(raw)
        log(f"  📅 Event: {data['event_title']} ({data['year']})")
        return data
    except Exception as e:
        log(f"  ⚠️ Event discovery failed: {e}")
        # Hard fallback — use date to seed a deterministic evergreen event
        return _fallback_event(date_info)


def _fallback_event(date_info):
    """Deterministic fallback when LLM fails."""
    fallbacks = [
        {"event_title": "The First Moon Landing", "year": 1969,
         "event_summary": "Apollo 11 touched down on the lunar surface on July 20, 1969. Neil Armstrong became the first human to walk on the Moon.",
         "why_compelling": "Greatest human achievement — shock, danger, triumph",
         "hook_style": "human_drama", "search_keywords": ["Apollo 11", "moon landing", "NASA"],
         "category": "space"},
        {"event_title": "The Fall of the Berlin Wall", "year": 1989,
         "event_summary": "On November 9, 1989, crowds began dismantling the Berlin Wall, ending 28 years of division.",
         "why_compelling": "Sudden collapse of an era — crowds, freedom, disbelief",
         "hook_style": "shocking_fact", "search_keywords": ["Berlin Wall", "Cold War", "Germany"],
         "category": "politics"},
        {"event_title": "The Chernobyl Disaster", "year": 1986,
         "event_summary": "On April 26, 1986, reactor No. 4 at the Chernobyl nuclear power plant exploded, triggering the worst nuclear disaster in history.",
         "why_compelling": "Chain reaction of lies, heroism, and catastrophe",
         "hook_style": "chain_reaction", "search_keywords": ["Chernobyl", "nuclear disaster", "USSR"],
         "category": "disaster"},
    ]
    # Pick deterministically by date
    idx = (date_info["day"] + date_info["month"]) % len(fallbacks)
    return fallbacks[idx]


# =============================================
# SCRIPT GENERATION
# =============================================

SCRIPT_FORMAT_PROMPTS = {
    "shocking_fact": "Open with the most shocking, counterintuitive, or little-known fact about this event. Make the viewer feel they've been lied to by textbooks.",
    "mystery_first": "Open with a mysterious question that can only be answered by understanding the full story. Reveal the answer dramatically in the final act.",
    "modern_parallel": "Constantly draw parallels between this historical event and something happening in today's world. Make history feel urgent and relevant.",
    "human_drama": "Focus on the raw human emotions — fear, ambition, love, betrayal. Tell it through the eyes of key people involved.",
    "counter_narrative": "Challenge the mainstream version. What did official accounts leave out? Who benefits from the popular narrative?",
    "numbers_reveal": "Use striking numbers and scale to hammer home the magnitude. How many people, how much money, how many years — make the scope visceral.",
    "eyewitness": "Write as if the viewer is a time traveler witnessing events in real time. Present tense, vivid sensory details, building tension.",
    "chain_reaction": "Show the butterfly effect — how this one event triggered a cascade of consequences that still shape our world today.",
}


def generate_script(event, date_info):
    """Generate full narration script for the video."""
    hook_style = event.get("hook_style", "human_drama")
    format_instruction = SCRIPT_FORMAT_PROMPTS.get(hook_style, SCRIPT_FORMAT_PROMPTS["human_drama"])

    prompt = f"""You are a world-class YouTube scriptwriter for a history channel with 5 million subscribers.

Write a complete narration script for this video:

EVENT: {event['event_title']} ({event['year']})
SUMMARY: {event['event_summary']}
DATE: {date_info['month_name']} {date_info['ordinal']}
HOOK STYLE: {hook_style}
HOOK INSTRUCTION: {format_instruction}

Script requirements:
- Length: 8-10 minutes when read aloud (approximately 1100-1400 words)
- English only, natural spoken language (not formal/academic)
- NO headers, NO section labels, NO [PAUSE] markers, NO stage directions
- Just the pure narration text, paragraph by paragraph
- Start with a GRIPPING hook that makes viewers stay (first 30 seconds are critical)
- Tell the full story: build-up → the event → consequences → modern relevance
- End with a thought-provoking question or call to action for comments
- Write for AUDIO — short sentences, rhetorical questions, vivid imagery
- Do NOT start with "Today in history" or "Welcome back" — be original

Write the full script now:"""

    log("  ✍️ Generating script...")
    try:
        script = call_llm(prompt, prefer="groq", max_tokens=5000)
        # Validate length
        if len(script) < SCRIPT_TARGET_MIN:
            log(f"  ⚠️ Script too short ({len(script)} chars) — expanding...")
            script = _expand_script(script, event, date_info)
        log(f"  ✅ Script: {len(script)} chars (~{len(script)//145} min)")
        return script
    except Exception as e:
        log(f"  ❌ Script generation failed: {e}")
        return None


def _expand_script(script, event, date_info):
    """Expand a short script with more historical context."""
    prompt = f"""The following history narration script is too short (under 8 minutes).
Expand it to 8-10 minutes by adding:
- More historical context and background
- Personal stories of key figures involved  
- Surprising details and lesser-known facts
- The long-term impact and modern relevance

Keep the same tone and style. Do NOT add headers or stage directions.

Original script:
{script}

Expanded script:"""
    try:
        return call_llm(prompt, prefer="groq", max_tokens=6000)
    except Exception:
        return script


# =============================================
# METADATA GENERATION
# =============================================
def generate_metadata(event, date_info, script):
    """Generate YouTube title, description, tags, thumbnail text."""
    prompt = f"""Generate YouTube metadata for a history video.

EVENT: {event['event_title']} ({event['year']})
DATE: {date_info['month_name']} {date_info['ordinal']}
CATEGORY: {event.get('category', 'history')}
HOOK: {event.get('hook_style', 'human_drama')}

Return ONLY a JSON object (no markdown, no explanation):
{{
  "title": "Compelling YouTube title under 70 characters — make it clickable",
  "description": "Full description 800-1000 characters. First 2 lines must hook viewers. Include the date, what happened, why it matters. End with a CTA to subscribe and comment.",
  "tags": "history,this day in history,{date_info['month_name']} {date_info['day']},{event['event_title'][:30]},historical events,world history,shocking history,true history",
  "pinned_comment": "Engaging question to spark comments — 1 sentence",
  "thumbnail_title": "3-6 WORD UPPERCASE TEXT for thumbnail — dramatic and punchy",
  "thumbnail_year": "{event['year']}"
}}"""

    try:
        raw = call_llm(prompt, prefer="gemini", max_tokens=1200)
        raw = re.sub(r"```json|```", "", raw).strip()
        meta = json.loads(raw)
        # Ensure required fields
        if "title" not in meta:
            meta["title"] = f"This Day in History: {event['event_title']} ({event['year']})"
        return meta
    except Exception as e:
        log(f"  ⚠️ Metadata generation failed: {e} — using fallback")
        return {
            "title": f"This Day in History: {event['event_title']} ({event['year']})",
            "description": f"On {date_info['month_name']} {date_info['ordinal']}, {event['year']}: {event['event_summary']}\n\nSubscribe for daily history videos!",
            "tags": f"history,this day in history,{date_info['month_name']} {date_info['day']},world history",
            "pinned_comment": "What do you think was the most shocking part of this story?",
            "thumbnail_title": event['event_title'].upper()[:20],
            "thumbnail_year": str(event['year']),
        }


# =============================================
# IMAGE SOURCING (Wikimedia Commons — free)
# =============================================
def fetch_wikimedia_images(keywords, output_dir, count=5):
    """
    Fetch historical images from Wikimedia Commons.
    Completely free, no API key required.
    """
    os.makedirs(output_dir, exist_ok=True)
    images = []

    for keyword in keywords[:3]:
        if len(images) >= count:
            break
        try:
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {keyword}",
                "gsrnamespace": "6",
                "gsrlimit": "5",
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "format": "json",
            }
            url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())

            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                ii = page.get("imageinfo", [{}])[0]
                mime = ii.get("mime", "")
                img_url = ii.get("url", "")
                if not img_url or mime not in ("image/jpeg", "image/png"):
                    continue
                # Min size filter (avoid tiny icons)
                if ii.get("width", 0) < 400:
                    continue

                out_path = os.path.join(output_dir, f"wiki_{len(images):03d}.jpg")
                try:
                    req = urllib.request.Request(img_url, headers={"User-Agent": "HistoryBot/1.0"})
                    with urllib.request.urlopen(req, timeout=15) as r2:
                        data_bytes = r2.read()
                    with open(out_path, "wb") as f:
                        f.write(data_bytes)
                    images.append(out_path)
                    if len(images) >= count:
                        break
                except Exception:
                    continue

        except Exception as e:
            log(f"  ⚠️ Wikimedia fetch failed for '{keyword}': {e}")
            continue

    # Pexels fallback if Wikimedia returned too few
    if len(images) < 3 and PEXELS_API_KEY:
        images += _fetch_pexels_images(keywords[0] if keywords else "history", output_dir, count - len(images))

    # Solid color fallback if all else fails
    if not images:
        images = [_make_fallback_image(output_dir)]

    log(f"  🖼️ Images: {len(images)} fetched")
    return images


def _fetch_pexels_images(query, output_dir, count=3):
    """Optional Pexels fallback."""
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": count, "orientation": "landscape"},
            timeout=15,
        )
        images = []
        for photo in resp.json().get("photos", []):
            url = photo["src"]["large"]
            out = os.path.join(output_dir, f"pexels_{photo['id']}.jpg")
            img_data = requests.get(url, timeout=15).content
            with open(out, "wb") as f:
                f.write(img_data)
            images.append(out)
        return images
    except Exception:
        return []


def _make_fallback_image(output_dir):
    """Generate a dark, cinematic fallback image."""
    out = os.path.join(output_dir, "fallback.jpg")
    if HAS_PILLOW:
        img = Image.new("RGB", (1920, 1080), (15, 10, 30))
        d = ImageDraw.Draw(img)
        # Vignette effect
        for i in range(200):
            alpha = int(i * 0.8)
            d.rectangle([i, i, 1920-i, 1080-i], outline=(15+i//10, 10+i//10, 30+i//8))
        img.save(out, quality=90)
    else:
        # ffmpeg fallback
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=0x0f0a1e:size=1920x1080:r=1",
            "-frames:v", "1", out
        ], capture_output=True)
    return out


# =============================================
# THUMBNAIL GENERATION
# =============================================
def generate_thumbnail(event, date_info, output_name, bg_image=None):
    """Generate a dramatic thumbnail using Pillow."""
    out_path = os.path.join(THUMBS_DIR, f"{output_name}.jpg")

    if not HAS_PILLOW:
        log("  ⚠️ Pillow not available — skipping thumbnail")
        return None

    try:
        W, H = 1280, 720

        # Background
        if bg_image and os.path.exists(bg_image):
            img = Image.open(bg_image).convert("RGB").resize((W, H), Image.LANCZOS)
        else:
            img = Image.new("RGB", (W, H), (10, 8, 25))

        draw = ImageDraw.Draw(img)

        # Dark gradient overlay (bottom 60%)
        for y in range(int(H * 0.35), H):
            alpha = int(220 * ((y - H * 0.35) / (H * 0.65)))
            draw.rectangle([0, y, W, y + 1], fill=(5, 5, 15, alpha))

        # Red accent bar (left edge — drama)
        draw.rectangle([0, 0, 8, H], fill=(200, 30, 30))

        # "THIS DAY IN HISTORY" label
        label = f"THIS DAY IN HISTORY · {date_info['month_name'].upper()} {date_info['day']}"
        _draw_text_safe(draw, label, W // 2, int(H * 0.62), size=28,
                        color=(200, 160, 50), anchor="mm")

        # Main event title (large)
        thumb_title = event.get("thumbnail_title", event["event_title"].upper()[:25])
        _draw_text_safe(draw, thumb_title, W // 2, int(H * 0.75), size=58,
                        color=(255, 255, 255), anchor="mm", bold=True)

        # Year badge (bottom right)
        year_text = str(event.get("year", ""))
        _draw_text_safe(draw, year_text, W - 40, H - 40, size=44,
                        color=(200, 50, 50), anchor="rb", bold=True)

        img.save(out_path, quality=95)
        log(f"  🖼️ Thumbnail: {out_path}")
        return out_path

    except Exception as e:
        log(f"  ⚠️ Thumbnail generation failed: {e}")
        return None


def _draw_text_safe(draw, text, x, y, size=40, color=(255, 255, 255), anchor="mm", bold=False):
    """Draw text with system font fallback chain."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    font = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, size)
                break
            except Exception:
                continue

    if font is None:
        font = ImageFont.load_default()

    # Shadow for readability
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180), anchor=anchor)
    draw.text((x, y), text, font=font, fill=color, anchor=anchor)


# =============================================
# VIDEO CREATION
# =============================================
def ensure_bgm():
    """Generate BGM if missing — cinematic ambient drone."""
    if os.path.exists(BGM_FILE):
        return BGM_FILE
    log("  🎵 Generating ambient BGM...")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anoisesrc=d=900:c=brown:r=44100:a=0.015",
            "-af", (
                "lowpass=f=200,"
                "equalizer=f=60:t=q:w=0.5:g=8,"
                "aecho=0.7:0.4:200|400:0.2|0.1,"
                "volume=0.25,"
                "loudnorm=I=-24:TP=-3:LRA=6"
            ),
            BGM_FILE
        ], capture_output=True, check=True)
        log(f"  ✅ BGM created: {BGM_FILE}")
    except Exception as e:
        log(f"  ⚠️ BGM generation failed: {e}")
        # Try simpler fallback
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "anoisesrc=d=900:c=pink:r=44100:a=0.01",
                "-af", "lowpass=f=300,volume=0.2",
                BGM_FILE
            ], capture_output=True)
        except Exception:
            pass
    return BGM_FILE if os.path.exists(BGM_FILE) else None


def create_tts_audio(script, output_name):
    """Generate TTS audio with humanization EQ."""
    raw_audio = f"history_tts_raw_{output_name}.mp3"
    final_audio = f"history_tts_{output_name}.mp3"

    log("  🎙️ Generating TTS audio...")

    # Write script to temp file (avoid shell escaping issues)
    script_tmp = f"history_script_tmp_{output_name}.txt"
    with open(script_tmp, "w", encoding="utf-8") as f:
        f.write(script)

    try:
        run([
            "edge-tts",
            f"--voice={TTS_VOICE}",
            TTS_RATE,
            TTS_PITCH,
            "--file", script_tmp,
            "--write-media", raw_audio,
        ], timeout=180)
    except Exception as e:
        log(f"  ❌ TTS failed: {e}")
        try:
            os.remove(script_tmp)
        except Exception:
            pass
        return None
    finally:
        try:
            os.remove(script_tmp)
        except Exception:
            pass

    # Apply humanization EQ
    try:
        run([
            "ffmpeg", "-y", "-i", raw_audio,
            "-af", NARRATION_EQ,
            "-ar", "44100", final_audio,
        ], timeout=120)
        os.remove(raw_audio)
        log(f"  ✅ TTS: {get_dur(final_audio):.0f}s audio")
        return final_audio
    except Exception as e:
        log(f"  ⚠️ TTS EQ failed: {e} — using raw audio")
        if os.path.exists(raw_audio):
            shutil.move(raw_audio, final_audio)
        return final_audio if os.path.exists(final_audio) else None


def build_video_filter(images, total_dur, fps=25):
    """
    Ken Burns effect filter for images.
    Each image gets equal time. Smooth transitions.
    """
    n = len(images)
    seg_dur = total_dur / n
    seg_frames = int(seg_dur * fps)

    filters = []
    for i in range(n):
        # Alternate zoom in / zoom out
        if i % 2 == 0:
            zoom = f"'min(zoom+0.0003,1.08)'"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        else:
            zoom = f"'if(eq(on,1),1.08,max(zoom-0.0003,1.0))'"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"

        filters.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z={zoom}:x={x}:y={y}:d={seg_frames}:s=1920x1080:fps={fps},"
            f"trim=duration={seg_dur:.3f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    filters.append(f"{concat_inputs}concat=n={n}:v=1:a=0[vout]")
    return ";".join(filters)


def create_video(script, images, audio_file, output_name, bgm_vol=0.12):
    """
    Assemble final video: Ken Burns images + narration + BGM.
    Returns output video path or None.
    """
    log("  🎬 Assembling video...")

    if not audio_file or not os.path.exists(audio_file):
        log("  ❌ No audio file")
        return None

    audio_dur = get_dur(audio_file)
    if audio_dur < 30:
        log(f"  ❌ Audio too short: {audio_dur:.1f}s")
        return None

    if not images:
        log("  ❌ No images")
        return None

    output_path = os.path.join(OUTPUT_DIR, f"{output_name}.mp4")
    bgm = ensure_bgm()

    # Build ffmpeg command
    img_inputs = []
    for img in images:
        img_inputs += ["-loop", "1", "-i", img]

    cmd = [
        "ffmpeg", "-y",
        *img_inputs,
        "-i", audio_file,
    ]

    if bgm and os.path.exists(bgm):
        cmd += ["-stream_loop", "-1", "-i", bgm]
        has_bgm = True
        bgm_idx = len(images) + 1
    else:
        has_bgm = False

    # Build filter graph
    vf = build_video_filter(images, audio_dur + 0.5)

    if has_bgm:
        audio_filter = (
            f"[{len(images)}:a]aformat=fltp:44100:stereo[voice];"
            f"[{bgm_idx}:a]aformat=fltp:44100:stereo,"
            f"atrim=duration={audio_dur + 0.5},volume={bgm_vol}[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first[aout]"
        )
        cmd += [
            "-filter_complex", f"{vf};{audio_filter}",
            "-map", "[vout]",
            "-map", "[aout]",
        ]
    else:
        cmd += [
            "-filter_complex", vf,
            "-map", "[vout]",
            "-map", f"{len(images)}:a",
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-t", str(audio_dur + 0.5),
        output_path,
    ]

    try:
        run(cmd, timeout=600)
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        log(f"  ✅ Video: {output_path} ({size_mb:.1f} MB, {get_dur(output_path):.0f}s)")
        return output_path
    except Exception as e:
        log(f"  ❌ Video assembly failed: {e}")
        return None


# =============================================
# YOUTUBE UPLOAD
# =============================================
def get_authenticated_service():
    """
    YouTube auth. Reads from HISTORY_YT_TOKEN_B64 env var first (CI),
    then falls back to local history_youtube_token.pickle.
    Uses separate credentials from AM channel.
    """
    creds = None

    b64 = os.environ.get(YOUTUBE_TOKEN_ENV, "")
    if b64:
        try:
            creds = pickle.loads(base64.b64decode(b64))
            log("  🔑 YouTube token loaded from env")
        except Exception as e:
            log(f"  ⚠️ Token decode failed: {e}")
            return None

    if not creds and os.path.exists(YOUTUBE_TOKEN_FILE):
        with open(YOUTUBE_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds:
        log("  ⚠️ No YouTube credentials — upload skipped")
        return None

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log("  ✅ Token refreshed")
        except Exception as e:
            log(f"  ⚠️ Token refresh failed: {e}")
            return None

    if not creds.valid:
        log("  ⚠️ Token invalid — re-run --auth-youtube locally")
        return None

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"  ⚠️ YouTube API build failed: {e}")
        return None


def auth_youtube():
    """Run OAuth flow locally. Save token to file."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Restore client secrets from env if available
    b64 = os.environ.get(YOUTUBE_SECRETS_ENV, "")
    if b64:
        try:
            with open(YOUTUBE_CLIENT_SECRETS, "wb") as f:
                f.write(base64.b64decode(b64))
        except Exception as e:
            print(f"Could not restore client_secrets: {e}")

    if not os.path.exists(YOUTUBE_CLIENT_SECRETS):
        print(f"ERROR: {YOUTUBE_CLIENT_SECRETS} not found.")
        print("Download from Google Cloud Console → OAuth 2.0 credentials.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(YOUTUBE_CLIENT_SECRETS, YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=0)

    with open(YOUTUBE_TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)
    print(f"✅ Auth successful! Token saved to: {YOUTUBE_TOKEN_FILE}")
    print()
    print("Now encode it for GitHub Secrets:")
    print(f"  python3 -c \"import base64; print(base64.b64encode(open('{YOUTUBE_TOKEN_FILE}','rb').read()).decode())\"")
    print(f"Add as GitHub Secret: HISTORY_YT_TOKEN_B64")


def upload_to_youtube(video_path, metadata, thumbnail_path=None, privacy="public"):
    """Upload video to YouTube. Returns video ID or None."""
    log(f"  ⬆️ Uploading: {os.path.basename(video_path)}")

    if not os.path.exists(video_path):
        log(f"  ❌ Video not found: {video_path}")
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
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
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
        log(f"  ✅ Uploaded: https://youtu.be/{video_id} ({time.time()-t0:.0f}s)")

        # Upload thumbnail
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
                ).execute()
                log("  ✅ Thumbnail uploaded")
            except Exception as e:
                log(f"  ⚠️ Thumbnail upload: {e}")

        # Pinned comment
        if metadata.get("pinned_comment"):
            try:
                youtube.commentThreads().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "topLevelComment": {
                                "snippet": {"textOriginal": metadata["pinned_comment"]}
                            },
                        }
                    },
                ).execute()
                log("  ✅ Pinned comment set")
            except Exception as e:
                log(f"  ⚠️ Comment failed: {e}")

        return video_id

    except Exception as e:
        err = str(e).lower()
        if "quota" in err or "quotaexceeded" in err:
            log(f"  ⚠️ YouTube quota exceeded — queuing for retry")
            _queue_upload(video_path, metadata, thumbnail_path, privacy)
        else:
            log(f"  ❌ Upload failed: {e}")
        return None


def _queue_upload(video_path, metadata, thumbnail_path, privacy):
    """Save failed upload to queue."""
    try:
        queue = []
        if os.path.exists(UPLOAD_QUEUE_FILE):
            with open(UPLOAD_QUEUE_FILE) as f:
                queue = json.load(f)
        queue.append({
            "video_path": video_path,
            "metadata": metadata,
            "thumbnail_path": thumbnail_path,
            "privacy": privacy,
            "queued_at": datetime.datetime.now().isoformat(),
        })
        with open(UPLOAD_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
        log(f"  📋 Queued for retry: {os.path.basename(video_path)}")
        # Commit queue
        try:
            run(["git", "add", UPLOAD_QUEUE_FILE])
            run(["git", "commit", "-m", "chore: history upload queue"])
            run(["git", "push"])
        except Exception:
            pass
    except Exception as e:
        log(f"  ⚠️ Queue save failed: {e}")


def upload_pending():
    """Re-attempt any queued uploads."""
    if not os.path.exists(UPLOAD_QUEUE_FILE):
        return
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
        vid = upload_to_youtube(
            item["video_path"], item["metadata"],
            item.get("thumbnail_path"), item.get("privacy", "public")
        )
        if not vid:
            remaining.append(item)

    with open(UPLOAD_QUEUE_FILE, "w") as f:
        json.dump(remaining, f, indent=2)


# =============================================
# MAIN PIPELINE
# =============================================
def run_pipeline(target_date=None, upload=False, privacy="public"):
    """
    Full pipeline: discover → script → metadata → images → audio → video → upload.
    """
    print()
    print("=" * 55)
    print("  THIS DAY IN HISTORY — Automation Bot v1.0")
    print("=" * 55)

    date_info = get_today_date_info(target_date)
    log(f"📅 Target date: {date_info['display']}")

    ensure_dirs()

    # Step 1: Discover event
    log("🔍 Discovering event...")
    used_topics = load_used_topics()
    event = discover_events(date_info, used_topics)

    safe_name = re.sub(r"[^a-z0-9_]", "_", event["event_title"].lower())[:40]
    output_name = f"history_{date_info['safe_str']}_{safe_name}"

    log(f"✅ Event: {event['event_title']} ({event['year']})")

    # Step 2: Generate script
    script = generate_script(event, date_info)
    if not script:
        log("❌ Script generation failed — aborting")
        return False

    script_path = os.path.join(SCRIPTS_DIR, f"{output_name}.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    log(f"  📝 Script saved: {script_path}")

    # Step 3: Generate metadata
    log("📋 Generating metadata...")
    metadata = generate_metadata(event, date_info, script)
    meta_path = os.path.join(METADATA_DIR, f"{output_name}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    log(f"  📝 Title: {metadata['title']}")

    # Step 4: Fetch images
    log("🖼️ Fetching images...")
    img_dir = os.path.join(IMAGES_DIR, output_name)
    keywords = event.get("search_keywords", [event["event_title"]]) + [
        f"{event['event_title']} history",
        f"{date_info['year'] if 'year' not in event else event['year']} historical",
    ]
    images = fetch_wikimedia_images(keywords, img_dir, count=8)

    # Step 5: Generate thumbnail
    log("🎨 Generating thumbnail...")
    thumb_bg = images[0] if images else None
    thumbnail_path = generate_thumbnail(event, date_info, output_name, thumb_bg)
    if thumbnail_path:
        metadata["thumbnail_path"] = thumbnail_path

    # Step 6: Generate TTS audio
    log("🎙️ Generating narration audio...")
    audio_file = create_tts_audio(script, output_name)
    if not audio_file:
        log("❌ Audio generation failed — aborting")
        return False

    # Step 7: Assemble video
    log("🎬 Creating video...")
    video_path = create_video(script, images, audio_file, output_name)

    # Cleanup temp audio
    try:
        os.remove(audio_file)
    except Exception:
        pass

    if not video_path:
        log("❌ Video creation failed")
        return False

    # Mark topic as used
    save_used_topic(event["event_title"])

    # Step 8: Upload
    if upload:
        log("⬆️ Uploading to YouTube...")
        video_id = upload_to_youtube(video_path, metadata, thumbnail_path, privacy)
        if video_id:
            log(f"✅ Live: https://youtu.be/{video_id}")
        else:
            log("⚠️ Upload failed — video saved locally")
    else:
        log(f"✅ Video ready (no upload): {video_path}")

    print()
    print("=" * 55)
    print("  DONE!")
    print(f"  Event: {event['event_title']} ({event['year']})")
    print(f"  Video: {video_path}")
    print("=" * 55)
    return True


# =============================================
# ENTRY POINT
# =============================================
def main():
    parser = argparse.ArgumentParser(description="This Day in History — Automated YouTube Bot")
    parser.add_argument("--today", action="store_true", help="Run for today's date")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--upload", action="store_true", help="Upload to YouTube after creation")
    parser.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    parser.add_argument("--upload-pending", action="store_true", help="Retry queued uploads")
    parser.add_argument("--auth-youtube", action="store_true", help="Run YouTube OAuth (local only)")
    args = parser.parse_args()

    if args.auth_youtube:
        auth_youtube()
        return

    if args.upload_pending:
        upload_pending()
        return

    # Determine target date
    target_date = None
    if args.date:
        try:
            target_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"Invalid date format: {args.date} (use YYYY-MM-DD)")
            sys.exit(1)

    success = run_pipeline(
        target_date=target_date,
        upload=args.upload,
        privacy=args.privacy,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
