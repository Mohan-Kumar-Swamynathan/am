"""Deity-aware Tamil TTS via edge-tts with SSML pauses and chunked generation."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

from utils.logger import get_logger

logger = get_logger("media.tts_engine")

FEMALE_DEITIES = frozenset({"அம்மன்", "லட்சுமி"})
MALE_DEITIES = frozenset({
    "சிவன்", "முருகன்", "விநாயகர்", "பெருமாள்", "ஐயப்பன்",
    "சூரியன்", "நடராஜர்", "கிருஷ்ணர்",
})

FEMALE_HUMANIZE = (
    "highpass=f=80,"
    "equalizer=f=250:t=q:w=0.8:g=2,"
    "equalizer=f=800:t=q:w=0.9:g=1.5,"
    "equalizer=f=2500:t=q:w=1:g=1.5,"
    "equalizer=f=5000:t=q:w=1:g=-2,"
    "acompressor=threshold=-18dB:ratio=2.5:attack=5:release=50:makeup=2,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)

MALE_HUMANIZE = (
    "highpass=f=70,"
    "equalizer=f=150:t=q:w=0.7:g=2,"
    "equalizer=f=500:t=q:w=0.8:g=1.5,"
    "equalizer=f=2000:t=q:w=1:g=2,"
    "equalizer=f=6000:t=q:w=1:g=-2,"
    "aecho=0.4:0.12:25|40:0.05|0.04,"
    "acompressor=threshold=-16dB:ratio=2:attack=6:release=60:makeup=2.5,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)

DEFAULT_HUMANIZE = (
    "highpass=f=80,"
    "equalizer=f=300:t=q:w=0.8:g=1.5,"
    "equalizer=f=2000:t=q:w=1:g=1,"
    "acompressor=threshold=-18dB:ratio=2:attack=5:release=50:makeup=2,"
    "loudnorm=I=-14:TP=-1.5:LRA=9"
)


@dataclass
class TtsProfile:
    voice: str
    rate: str
    pitch: str
    humanize_filter: str


def resolve_tts_profile(deity_name: str = "") -> TtsProfile:
    if deity_name in FEMALE_DEITIES:
        return TtsProfile("ta-IN-PallaviNeural", "-8%", "+0Hz", FEMALE_HUMANIZE)
    if deity_name in MALE_DEITIES:
        return TtsProfile("ta-IN-ValluvarNeural", "-3%", "+0Hz", MALE_HUMANIZE)
    return TtsProfile("ta-IN-PallaviNeural", "-10%", "+0Hz", DEFAULT_HUMANIZE)


def normalize_tts_text(text: str) -> str:
    cleaned = text.replace("**", "").replace("*", "")
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[🙏🔱🐘🕉✨⭐💫🌟]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def inject_ssml_pauses(text: str) -> str:
    text = text.replace("[PAUSE_LONG]", '<break time="800ms"/>')
    text = text.replace("[PAUSE_MED]", '<break time="400ms"/>')
    text = text.replace("[PAUSE_SHORT]", '<break time="200ms"/>')
    text = re.sub(r"\.{3,}", '<break time="500ms"/>', text)
    return text


def split_script_chunks(text: str, max_chars: int = 1500) -> List[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            current = ""
            for sentence in sentences:
                if len(current) + len(sentence) + 1 <= max_chars:
                    current = f"{current} {sentence}".strip()
                else:
                    if current:
                        chunks.append(current)
                    current = sentence
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


async def _synthesize_chunk(
    chunk_text: str,
    chunk_path: str,
    profile: TtsProfile,
) -> None:
    import edge_tts

    plain_text = normalize_tts_text(chunk_text)
    plain_text = plain_text.replace("[PAUSE_LONG]", " ... ")
    plain_text = plain_text.replace("[PAUSE_MED]", " .. ")
    plain_text = plain_text.replace("[PAUSE_SHORT]", " . ")
    communicate = edge_tts.Communicate(
        plain_text,
        profile.voice,
        rate=profile.rate,
        pitch=profile.pitch,
    )
    await communicate.save(chunk_path)


def generate_narration_audio(
    script_text: str,
    output_path: str,
    deity_name: str = "",
    run_fn: Optional[Callable] = None,
) -> bool:
    """Generate humanized narration MP3 from Tamil script."""
    profile = resolve_tts_profile(deity_name)
    chunks = split_script_chunks(script_text)
    if not chunks:
        return False

    temp_dir = os.path.dirname(output_path) or "/tmp"
    chunk_paths: List[str] = []
    runner = run_fn or _default_run

    try:
        async def _generate_all() -> None:
            for index, chunk in enumerate(chunks):
                chunk_path = os.path.join(temp_dir, f"tts_chunk_{index}.mp3")
                await _synthesize_chunk(chunk, chunk_path, profile)
                chunk_paths.append(chunk_path)

        asyncio.run(_generate_all())
    except Exception as exc:
        logger.error("edge-tts chunked generation failed: %s — trying CLI fallback", exc)
        return _fallback_cli_tts(script_text, output_path, profile, runner)

    raw_voice = output_path.replace(".mp3", "_raw.mp3")
    if len(chunk_paths) == 1:
        os.replace(chunk_paths[0], raw_voice)
    else:
        concat_list = os.path.join(temp_dir, "tts_concat.txt")
        with open(concat_list, "w", encoding="utf-8") as handle:
            for chunk_path in chunk_paths:
                handle.write(f"file '{os.path.abspath(chunk_path)}'\n")
        result = runner(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c", "copy", raw_voice,
            ],
            timeout=120,
        )
        if result.returncode != 0:
            return _fallback_cli_tts(script_text, output_path, profile, runner)

    humanized = runner(
        ["ffmpeg", "-y", "-i", raw_voice, "-af", profile.humanize_filter, output_path],
        timeout=180,
    )
    if humanized.returncode != 0:
        os.replace(raw_voice, output_path)
    try:
        if os.path.exists(raw_voice):
            os.remove(raw_voice)
        for chunk_path in chunk_paths:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
    except OSError:
        pass
    return os.path.exists(output_path)


def _fallback_cli_tts(
    script_text: str,
    output_path: str,
    profile: TtsProfile,
    runner: Callable,
) -> bool:
    """Fallback to edge-tts CLI when async API fails."""
    temp_dir = os.path.dirname(output_path) or "/tmp"
    script_file = os.path.join(temp_dir, "tts_fallback_script.txt")
    raw_voice = output_path.replace(".mp3", "_raw.mp3")
    normalized = normalize_tts_text(script_text)
    normalized = normalized.replace("[PAUSE_LONG]", "  ...  ")
    normalized = normalized.replace("[PAUSE_MED]", " ... ")
    normalized = normalized.replace("[PAUSE_SHORT]", " .. ")
    with open(script_file, "w", encoding="utf-8") as handle:
        handle.write(normalized)

    result = runner(
        [
            "edge-tts",
            "--file", script_file,
            "--voice", profile.voice,
            f"--rate={profile.rate}",
            f"--pitch={profile.pitch}",
            "--write-media", raw_voice,
        ],
        timeout=600,
    )
    if result.returncode != 0:
        return False
    humanized = runner(
        ["ffmpeg", "-y", "-i", raw_voice, "-af", profile.humanize_filter, output_path],
        timeout=180,
    )
    if humanized.returncode != 0 and os.path.exists(raw_voice):
        os.replace(raw_voice, output_path)
    return os.path.exists(output_path)


def mix_voice_bgm_bell(
    voice_path: str,
    bgm_path: str,
    bell_path: str,
    output_path: str,
    bgm_volume: float = 0.18,
    run_fn: Optional[Callable] = None,
) -> bool:
    """Mix narration with sidechain-ducked BGM and intro bell."""
    runner = run_fn or _default_run
    filter_complex = (
        "[0:a]adelay=2500|2500,volume=1.0[voice];"
        "[1:a]volume={bgm_vol}[bgm];"
        "[voice][bgm]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=250[ducked];"
        "[2:a]volume=0.65,afade=t=out:st=2:d=0.5[bell];"
        "[voice][ducked][bell]amix=inputs=3:duration=first:dropout_transition=2,"
        "loudnorm=I=-14:TP=-1.5:LRA=9[out]"
    ).format(bgm_vol=bgm_volume)

    result = runner(
        [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-i", bgm_path,
            "-i", bell_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ac", "2",
            output_path,
        ],
        timeout=300,
    )
    return result.returncode == 0 and os.path.exists(output_path)


def _default_run(command: List[str], timeout: int = 120):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
