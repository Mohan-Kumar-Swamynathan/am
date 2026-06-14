"""High-impact Tamil thumbnail engine — max 4 words."""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path
from typing import Optional, Tuple

from utils.logger import get_logger

logger = get_logger("thumbnail_engine")

THUMBNAIL_DIR = Path("thumbnails")
TAMIL_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Tamil Sangam MN.ttc",
    "/Library/Fonts/NotoSansTamil-Bold.ttf",
)
ENG_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

DEITY_PALETTE = {
    "முருகன்": ((50, 8, 0), (255, 125, 0)),
    "சிவன்": ((5, 0, 32), (140, 85, 255)),
    "விநாயகர்": ((35, 16, 0), (255, 170, 0)),
    "நடராஜர்": ((6, 2, 35), (145, 105, 255)),
    "ஐயப்பன்": ((0, 20, 6), (0, 190, 70)),
    "அம்மன்": ((45, 0, 25), (255, 50, 165)),
    "பெருமாள்": ((0, 25, 45), (0, 170, 210)),
    "கிருஷ்ணர்": ((0, 6, 42), (75, 145, 255)),
    "லட்சுமி": ((45, 35, 0), (255, 210, 0)),
    "சூரியன்": ((52, 28, 0), (255, 155, 0)),
    "default": ((35, 20, 0), (255, 190, 40)),
}


def _load_font(size: int, tamil: bool = True):
    from PIL import ImageFont

    candidates = TAMIL_FONT_CANDIDATES if tamil else (ENG_FONT,)
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _limit_four_words(text: str) -> str:
    words = text.strip().split()
    return " ".join(words[:4])


def _draw_stroke_glow(draw, position: Tuple[int, int], text: str, font, fill, glow_color):
    x, y = position
    for radius in (6, 4, 2):
        for ox in range(-radius, radius + 1, 2):
            for oy in range(-radius, radius + 1, 2):
                draw.text((x + ox, y + oy), text, font=font, fill=glow_color)
    for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
        draw.text((x + ox, y + oy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def _is_scene_background(bg_image_path: Optional[str]) -> bool:
    if not bg_image_path:
        return True
    base = os.path.basename(bg_image_path).lower()
    scene_markers = ("_hero", "_ambient", "_detail", "_wide", "_close", "_atmosphere", "_texture", "_perspective")
    return base.endswith(".png") and any(marker in base for marker in scene_markers)


def generate_thumbnail(
    title: str,
    deity_name: str,
    output_name: str,
    deity_en: str = "",
    bg_image_path: Optional[str] = None,
    thumbnail_text: Optional[str] = None,
    output_dir: Path = THUMBNAIL_DIR,
) -> Optional[str]:
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

        output_dir.mkdir(parents=True, exist_ok=True)
        width, height = 1280, 720
        _, accent = DEITY_PALETTE.get(deity_name, DEITY_PALETTE["default"])

        if bg_image_path and os.path.exists(bg_image_path) and not _is_scene_background(bg_image_path):
            canvas = Image.open(bg_image_path).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
            canvas = canvas.filter(ImageFilter.GaussianBlur(radius=10))
            canvas = ImageEnhance.Contrast(canvas).enhance(1.35)
            canvas = ImageEnhance.Brightness(canvas).enhance(0.45)
        else:
            canvas = Image.new("RGB", (width, height), (18, 12, 32))

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle([(0, 430), (width, height)], fill=(0, 0, 0, 175))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
        draw = ImageDraw.Draw(canvas)

        headline = _limit_four_words(thumbnail_text or title)
        words = headline.split()
        line_one = " ".join(words[:2]) if len(words) >= 2 else headline
        line_two = " ".join(words[2:]) if len(words) > 2 else ""

        title_font = _load_font(78, tamil=True)
        sub_font = _load_font(58, tamil=True)
        brand_font = _load_font(24, tamil=True)

        glow = tuple(min(255, channel + 40) for channel in accent)
        _draw_stroke_glow(draw, (48, 470), line_one, title_font, (255, 248, 210), glow)
        if line_two:
            _draw_stroke_glow(draw, (48, 560), line_two, sub_font, (255, 223, 120), glow)

        draw.text((48, 36), deity_name[:18], font=_load_font(42, tamil=True), fill=accent)
        draw.text((48, height - 42), "ஆலய மணி", font=brand_font, fill=(*accent, 200))

        out_path = output_dir / f"{output_name}_thumb.jpg"
        canvas.convert("RGB").save(out_path, "JPEG", quality=96, optimize=True)
        logger.info("Thumbnail saved: %s (text=%s)", out_path, headline)
        return str(out_path)
    except Exception as exc:
        logger.error("Thumbnail generation failed: %s", exc)
        return None


def extract_thumbnail_text(title: str, deity: str = "") -> str:
    """Derive max-4-word impact text from full title."""
    cleaned = title.replace("|", " ").replace("🙏", "").replace("🔱", "")
    for token in ("ஆலய மணி", "Tamil", "Devotional", deity):
        cleaned = cleaned.replace(token, "")
    words = [word for word in cleaned.split() if word.strip()]
    if len(words) >= 4:
        return " ".join(words[:4])
    if deity and len(words) < 4:
        return _limit_four_words(f"{deity} {' '.join(words)}")
    return _limit_four_words(" ".join(words) or "கோவில் ரகசியம்")
