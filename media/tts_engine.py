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
    "highpass=f=85,"
    "equalizer=f=200:t=q:w=0.9:g=1.5,"   # body warmth
    "equalizer=f=800:t=q:w=0.8:g=2,"     # presence
    "equalizer=f=3000:t=q:w=1:g=1.5,"    # clarity
    "equalizer=f=5500:t=q:w=1:g=-2,"     # de-ess sibilance
    "equalizer=f=9000:t=q:w=1:g=-3,"     # cut digital harshness
    "aecho=0.75:0.65:28:0.06,"           # small room reverb — temple stone warmth
    "acompressor=threshold=-20dB:ratio=1.8:attack=8:release=200:makeup=2,"
    "atempo=0.98,"                        # 2% slow — removes rushed TTS cadence
    "loudnorm=I=-14:TP=-1.5:LRA=11"
)

MALE_HUMANIZE = (
    "highpass=f=65,"
    "equalizer=f=120:t=q:w=0.8:g=2,"    # chest resonance
    "equalizer=f=400:t=q:w=0.9:g=1.5,"  # warmth
    "equalizer=f=2200:t=q:w=1:g=2,"     # intelligibility
    "equalizer=f=6500:t=q:w=1:g=-2,"    # cut harshness
    "aecho=0.72:0.58:22|38:0.07|0.04,"  # dual-tap room — natural depth
    "acompressor=threshold=-18dB:ratio=1.7:attack=7:release=250:makeup=2.5,"
    "atempo=0.97,"                        # 3% slow — ValluvarNeural rushes slightly
    "loudnorm=I=-14:TP=-1.5:LRA=11"
)

DEFAULT_HUMANIZE = (
    "highpass=f=80,"
    "equalizer=f=300:t=q:w=0.8:g=1.5,"
    "equalizer=f=2000:t=q:w=1:g=1,"
    "aecho=0.72:0.55:22:0.05,"          # neutral room
    "acompressor=threshold=-18dB:ratio=1.8:attack=6:release=180:makeup=2,"
    "atempo=0.98,"
    "loudnorm=I=-14:TP=-1.5:LRA=11"
)


@dataclass
class TtsProfile:
    voice: str
    rate: str
    pitch: str
    humanize_filter: str


def resolve_tts_profile(deity_name: str = "") -> TtsProfile:
    if deity_name in FEMALE_DEITIES:
        # +2Hz pitch lift removes flat robotic quality; -10% rate gives Tamil cadence room
        return TtsProfile("ta-IN-PallaviNeural", "-15%", "+2Hz", FEMALE_HUMANIZE)
    if deity_name in MALE_DEITIES:
        # +1Hz pitch; -5% rate — ValluvarNeural sounds most natural at near-default speed
        return TtsProfile("ta-IN-ValluvarNeural", "-5%", "+1Hz", MALE_HUMANIZE)
    return TtsProfile("ta-IN-PallaviNeural", "-10%", "+2Hz", DEFAULT_HUMANIZE)


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
    call_llm_fn: Optional[Callable] = None,
) -> bool:
    """Generate SSML-optimised narration MP3 from Tamil script."""
    profile = resolve_tts_profile(deity_name)
    runner = run_fn or _default_run

    # Try SSML pipeline first — most natural
    try:
        from ssml_processor import generate_ssml_audio, VOICE_TA_MALE, VOICE_TA_FEMALE
        ssml_voice = VOICE_TA_FEMALE if deity_name in FEMALE_DEITIES else VOICE_TA_MALE
        ok = generate_ssml_audio(
            script=script_text,
            output_path=output_path,
            voice=ssml_voice,
            language="ta",
            call_llm_fn=call_llm_fn,
            run_fn=runner,
        )
        if ok:
            logger.info("SSML narration generated: %s", output_path)
            return True
        logger.warning("SSML pipeline returned False — falling back to chunked edge-tts")
    except Exception as e:
        logger.warning("SSML pipeline error: %s — falling back", e)

    # Fallback: original chunked edge-tts approach
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
        logger.info("TTS generated %s chunks for narration", len(chunk_paths))
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
            timeout=max(300, len(chunk_paths) * 90),
        )
        if result.returncode != 0:
            return _fallback_cli_tts(script_text, output_path, profile, runner)

    humanize_timeout = max(600, len(chunks) * 120, len(script_text) // 20)
    humanized = runner(
        ["ffmpeg", "-y", "-i", raw_voice, "-af", profile.humanize_filter, output_path],
        timeout=humanize_timeout,
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
        timeout=max(600, len(script_text) // 15),
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
        "[0:a]adelay=2500|2500,volume=1.0,asplit=2[voice][sidechain];"
        "[1:a]volume={bgm_vol}[bgm];"
        "[bgm][sidechain]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=400[bg_ducked];"
        "[2:a]volume=0.65,afade=t=out:st=2:d=0.5[bell];"
        "[voice][bg_ducked][bell]amix=inputs=3:duration=first:dropout_transition=2,"
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
