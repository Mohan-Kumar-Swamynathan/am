"""Image quality scoring, deduplication, and source prioritization."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from utils.logger import get_logger

logger = get_logger("image_quality")

try:
    from PIL import Image, ImageFilter, ImageStat
except ImportError:
    Image = None  # type: ignore


SOURCE_PRIORITY = {"wikimedia": 3, "pexels": 2, "pollinations": 1, "scene": 0, "unknown": 0}


@dataclass
class ImageScore:
    path: str
    score: float
    source: str
    width: int
    height: int
    phash: str


def _average_hash(image_path: str, hash_size: int = 8) -> str:
    if Image is None:
        return hashlib.md5(image_path.encode()).hexdigest()[:16]
    with Image.open(image_path) as img:
        gray = img.convert("L").resize((hash_size, hash_size), Image.Resampling.LANCZOS)
        pixels = list(gray.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if px >= avg else "0" for px in pixels)
        return hex(int(bits, 2))[2:].rjust(hash_size * hash_size // 4, "0")


def _hamming_distance(hash_a: str, hash_b: str) -> int:
    if len(hash_a) != len(hash_b):
        return 64
    return sum(ch1 != ch2 for ch1, ch2 in zip(hash_a, hash_b))


def _detect_corner_watermark(img: "Image.Image") -> bool:
    w, h = img.size
    corner = img.crop((w - 140, h - 70, w, h)).convert("L")
    extrema = corner.getextrema()
    if not extrema:
        return False
    return abs(extrema[1] - extrema[0]) < 10


def _blur_score(img: "Image.Image") -> float:
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    variance = stat.var[0] if stat.var else 0.0
    return min(100.0, variance / 8.0)


def score_image(image_path: str, source: str = "unknown") -> Optional[ImageScore]:
    if not os.path.exists(image_path) or Image is None:
        return None
    try:
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            width, height = img.size
            score = 0.0

            if width < 1280 or height < 720:
                return None
            score += 25

            aspect = width / float(height)
            if 1.3 <= aspect <= 1.9:
                score += 15

            if _detect_corner_watermark(img):
                score -= 30

            blur = _blur_score(img)
            score += min(25, blur)

            score += SOURCE_PRIORITY.get(source, 0) * 5
            score = max(0.0, min(100.0, score))

            return ImageScore(
                path=image_path,
                score=score,
                source=source,
                width=width,
                height=height,
                phash=_average_hash(image_path),
            )
    except Exception as exc:
        logger.debug("Image score failed %s: %s", image_path, exc)
        return None


def filter_and_rank_images(
    candidates: List[Tuple[str, str]],
    min_score: float = 40.0,
    max_images: int = 8,
    duplicate_threshold: int = 8,
) -> List[str]:
    """
    candidates: list of (path, source) where source is wikimedia|pexels|pollinations|scene
    Returns ordered unique high-quality paths.
    """
    scored: List[ImageScore] = []
    for path, source in candidates:
        result = score_image(path, source)
        if result and result.score >= min_score:
            scored.append(result)

    scored.sort(key=lambda item: (-item.score, -SOURCE_PRIORITY.get(item.source, 0)))

    selected: List[str] = []
    seen_hashes: Set[str] = set()
    for item in scored:
        if any(_hamming_distance(item.phash, seen) <= duplicate_threshold for seen in seen_hashes):
            continue
        selected.append(item.path)
        seen_hashes.add(item.phash)
        if len(selected) >= max_images:
            break

    logger.info(
        "Image quality filter: %s/%s passed (min_score=%s)",
        len(selected),
        len(candidates),
        min_score,
    )
    return selected
