#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         THIS DAY IN HISTORY — PRODUCTION AUTOMATION BOT v2    ║
║  A comprehensive, production-grade automated YouTube engine   ║
║  Single-File Architecture | Headless CI-CD Ready | Free Stack ║
╚═══════════════════════════════════════════════════════════════╝

Architecture Blueprint:
- Free Core Services: Wikipedia/Wikimedia APIs, Gemini/Groq LLMs, edge-tts, FFmpeg, Pillow
- Advanced Pipeline: Verification -> Scoring -> Scripting -> Shorts -> Imaging -> Rendering -> YT
- Execution Model: Fully headless, crash-resilient, deterministic state recovery, zero-allocation caching.
"""

import argparse
import base64
import datetime
import hashlib
import json
import logging
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ==============================================================================
# CONFIGURATION AND CONSTANTS
# ==============================================================================
@dataclass(frozen=True)
class AppConfig:
    # API tokens and pipeline keys
    GEMINI_KEY: str = os.environ.get("GEMINI_KEY", "")
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    GH_PAT_TOKEN: str = os.environ.get("GH_PAT_TOKEN", "")
    CEREBRAS_API_KEY: str = os.environ.get("CEREBRAS_API_KEY", "")
    PEXELS_API_KEY: str = os.environ.get("PEXELS_API_KEY", "")

    # Storage Infrastructure directories
    OUTPUT_DIR: Path = Path("history_videos")
    SCRIPTS_DIR: Path = Path("history_scripts")
    METADATA_DIR: Path = Path("history_metadata")
    THUMBS_DIR: Path = Path("history_thumbnails")
    IMAGES_DIR: Path = Path("history_images")
    CACHE_DIR: Path = Path("history_cache")

    # Local Engine Database Files
    USED_TOPICS_FILE: Path = Path("history_used_topics.txt")
    UPLOAD_QUEUE_FILE: Path = Path("history_upload_queue.json")
    ANALYTICS_FILE: Path = Path("analytics.json")
    CATEGORY_HISTORY_FILE: Path = Path("category_history.json")

    # Narration & TTS Audio Profile Configurations
    TTS_VOICE: str = "en-GB-RyanNeural"
    TTS_RATE: str = "--rate=-4%"
    TTS_PITCH: str = "--pitch=-1Hz"
    BGM_FILE: Path = Path("history_bgm.mp3")

    # Target Script Length Constraints
    SCRIPT_TARGET_MIN_WORDS: int = 1200
    SCRIPT_TARGET_MAX_WORDS: int = 1500

    # YouTube Specific Integration Environment Identifiers
    YT_TOKEN_ENV: str = "HISTORY_YT_TOKEN_B64"
    YT_SECRETS_ENV: str = "HISTORY_CLIENT_SECRETS"
    YT_TOKEN_FILE: Path = Path("history_youtube_token.pickle")
    YT_CLIENT_SECRETS: Path = Path("history_client_secrets.json")
    YT_SCOPES: List[str] = field(default_factory=lambda: [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.upload"
    ])

CONFIG = AppConfig()

# System Supported Content Archetypes / Video Categories
CATEGORIES: List[str] = [
    "War", "Science", "Discovery", "Politics", "Space",
    "Ancient History", "Disaster", "Mystery", "Revolution", "Culture"
]

# Audio Normalization Matrix & Production Level Mastering Equalization Chain Filters
NARRATION_EQ_FILTERS: str = (
    "highpass=f=75,"
    "equalizer=f=250:t=q:w=0.7:g=1.5,"
    "equalizer=f=1200:t=q:w=1.0:g=1.2,"
    "equalizer=f=3500:t=q:w=0.8:g=2.0,"
    "equalizer=f=8000:t=q:w=1.2:g=-1.5,"
    "acompressor=threshold=-16dB:ratio=3.5:attack=8:release=65:makeup=2.5,"
    "loudnorm=I=-14:TP=-1.5:LRA=8"
)

# LLM Fallback Chain Matrix Setup
LLM_PROVIDERS: List[Dict[str, Any]] = [
    {"name": "gemini", "url": None, "key": CONFIG.GEMINI_KEY, "model": "gemini-2.5-flash"},
    {"name": "groq", "url": "https://api.groq.com/openai/v1", "key": CONFIG.GROQ_API_KEY, "model": "llama-3.3-70b-versatile"},
    {"name": "github", "url": "https://models.inference.ai.azure.com", "key": CONFIG.GH_PAT_TOKEN, "model": "gpt-4o-mini"},
    {"name": "cerebras", "url": "https://api.cerebras.ai/v1", "key": CONFIG.CEREBRAS_API_KEY, "model": "llama3.1-70b"},
    {"name": "groq_fallback", "url": "https://api.groq.com/openai/v1", "key": CONFIG.GROQ_API_KEY, "model": "llama3-8b-8192"}
]

# ==============================================================================
# INITIALIZATION AND LOGGING CORE
# ==============================================================================
def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d]: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

setup_logging()

def bootstrap_environment() -> None:
    """Guarantees directory tree health across local operations filesystems."""
    for path in [CONFIG.OUTPUT_DIR, CONFIG.SCRIPTS_DIR, CONFIG.METADATA_DIR, CONFIG.THUMBS_DIR, CONFIG.IMAGES_DIR, CONFIG.CACHE_DIR]:
        path.mkdir(parents=True, exist_ok=True)

# ==============================================================================
# DATA STRUCTURES & SCHEMAS
# ==============================================================================
@dataclass
class VerifiedFact:
    fact_statement: str
    historical_context: str
    confidence_score: float

@dataclass
class HistoricalEvent:
    event_title: str
    year: int
    category: str
    event_summary: str
    raw_wikipedia_dump: str
    verified_facts: List[Dict[str, Any]] = field(default_factory=list)
    selection_score: float = 0.0

@dataclass
class PipelineMetadata:
    title: str
    description: str
    tags: str
    pinned_comment: str
    community_post: str
    thumbnail_text_options: List[str] = field(default_factory=list)
    title_variants: List[Dict[str, Any]] = field(default_factory=list)
    short_30s_script: str = ""
    short_60s_script: str = ""
    shorts_metadata: Dict[str, Any] = field(default_factory=dict)

# ==============================================================================
# FAILSAFE NETWORK UTILITIES & LOCAL DISK CACHING
# ==============================================================================
def fetch_url(url: str, headers: Optional[Dict[str, str]] = None, post_data: Optional[bytes] = None, timeout: int = 15) -> str:
    """Executes atomic operations wrapper around low-level urllib sockets."""
    default_headers = {"User-Agent": "HistoryAutomationEngineV2/2.0 (contact:bot@history.internal)"}
    if headers:
        default_headers.update(headers)
    
    req = urllib.request.Request(url, data=post_data, headers=default_headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logging.warning(f"Network transport anomaly encountered on attempt {attempt+1} for {url}: {e}")
            if attempt == 2:
                raise e
            time.sleep(2 ** attempt)
    return ""

def get_cache(key_space: str, unique_identifier: str) -> Optional[str]:
    hashed_key = hashlib.md5(f"{key_space}_{unique_identifier}".encode("utf-8")).hexdigest()
    cache_file = CONFIG.CACHE_DIR / f"{hashed_key}.cache"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    return None

def write_cache(key_space: str, unique_identifier: str, content: str) -> None:
    hashed_key = hashlib.md5(f"{key_space}_{unique_identifier}".encode("utf-8")).hexdigest()
    cache_file = CONFIG.CACHE_DIR / f"{hashed_key}.cache"
    try:
        cache_file.write_text(content, encoding="utf-8")
    except Exception as e:
        logging.error(f"Failed writing persistence block metadata: {e}")

# ==============================================================================
# BACKEND CRYPTO/EXECUTION SYSTEM UTILITIES
# ==============================================================================
def execute_system_command(cmd: List[str], execution_timeout: int = 600) -> subprocess.CompletedProcess:
    """Secure subprocess lifecycle harness managing thread context termination."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=execution_timeout, check=True)
        return res
    except subprocess.CalledProcessError as err:
        logging.error(f"Subprocess terminated out-of-bounds. Signal Code: {err.returncode}\nStdout: {err.stdout}\nStderr: {err.stderr}")
        raise RuntimeError(f"Engine Failure during system execution of: {' '.join(cmd)}") from err
    except subprocess.TimeoutExpired as tex:
        logging.error(f"Process hang safety trip triggered. Overran {execution_timeout} seconds constraint limits.")
        raise TimeoutError(f"Command execution timed out: {' '.join(cmd)}") from tex

def extract_media_duration(target_file_path: Path) -> float:
    if not target_file_path.exists():
        return 0.0
    probe_command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(target_file_path)
    ]
    try:
        proc_out = subprocess.run(probe_command, capture_output=True, text=True, check=True)
        return float(proc_out.stdout.strip())
    except Exception as e:
        logging.error(f"Failed extracting media timeline data points for {target_file_path}: {e}")
        return 0.0

# ==============================================================================
# INTEGRATED LLM FAULT-TOLERANT WATERFALL GATEWAY
# ==============================================================================
def parse_and_sanitize_json(raw_payload: str) -> Dict[str, Any]:
    """Applies contextual heuristic scrubbing algorithms on toxic/malformed JSON string data blocks."""
    cleaned = raw_payload.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^
http://googleusercontent.com/immersive_entry_chip/0
