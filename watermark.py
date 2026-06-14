"""Permanent channel watermark overlay via FFmpeg."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.retry import run_command_with_retry

logger = get_logger("watermark")

DEFAULT_LOGO_PATH = Path("images/logo.png")
CHANNEL_LABEL = "ஆலய மணி"
WATERMARK_OPACITY = 0.25


def ensure_watermark_asset(logo_path: Path = DEFAULT_LOGO_PATH) -> str:
    logo_path = Path(logo_path)
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    if logo_path.exists() and logo_path.stat().st_size > 100:
        return str(logo_path)

    try:
        from PIL import Image, ImageDraw, ImageFont

        canvas = Image.new("RGBA", (220, 72), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle([0, 0, 219, 71], radius=10, fill=(20, 10, 40, 180))
        font = ImageFont.load_default()
        draw.text((12, 24), CHANNEL_LABEL, font=font, fill=(255, 215, 80, 255))
        canvas.save(logo_path)
        logger.info("Generated fallback watermark: %s", logo_path)
    except Exception as exc:
        logger.warning("Watermark asset generation failed: %s", exc)
        logo_path.write_bytes(b"")

    return str(logo_path)


def build_watermark_filter(
    logo_input_index: int = 1,
    opacity: float = WATERMARK_OPACITY,
) -> str:
    alpha = max(0.05, min(1.0, opacity))
    return (
        f"[{logo_input_index}:v]scale=180:-1,format=rgba,"
        f"colorchannelmixer=aa={alpha}[wm];"
        f"[0:v][wm]overlay=main_w-overlay_w-30:main_h-overlay_h-30"
    )


def apply_watermark(
    input_video: str,
    output_video: str,
    logo_path: Optional[str] = None,
    opacity: float = WATERMARK_OPACITY,
    encode_timeout: int = 600,
) -> bool:
    if not os.path.exists(input_video):
        logger.error("Input video missing: %s", input_video)
        return False

    logo = ensure_watermark_asset(Path(logo_path or DEFAULT_LOGO_PATH))
    if not os.path.exists(logo) or os.path.getsize(logo) < 50:
        logger.warning("Watermark logo unavailable — copying input unchanged")
        if input_video != output_video:
            import shutil
            shutil.copy2(input_video, output_video)
        return False

    filter_graph = build_watermark_filter(logo_input_index=1, opacity=opacity)
    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-i", logo,
        "-filter_complex", filter_graph,
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_video,
    ]
    result = run_command_with_retry(cmd, max_retries=2, timeout=encode_timeout)
    if result.returncode != 0:
        logger.error("Watermark overlay failed")
        return False
    logger.info("Watermark applied: %s", output_video)
    return True
