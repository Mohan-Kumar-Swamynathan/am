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

REAL_PHOTO_SOURCES = frozenset({"wikimedia", "pexels", "pollinations", "local"})
SOURCE_PRIORITY = {"wikimedia": 4, "pexels": 3, "pollinations": 2, "local": 2, "scene": 0, "unknown": 0}
MIN_IMAGE_BYTES = 50_000


@dataclass
class ImageScore:
    path: str
    score: float
    source: str
    width: int
    height: int
    phash: str


def validate_image_file(image_path: str, min_bytes: int = MIN_IMAGE_BYTES) -> bool:
    """Return True if path is a readable image file (not HTML/error body)."""
    if not image_path or not os.path.exists(image_path):
        return False
    try:
        if os.path.getsize(image_path) < min_bytes:
            return False
        with open(image_path, "rb") as handle:
            header = handle.read(16)
        if header.startswith(b"<!") or header.startswith(b"<html") or header.startswith(b"{"):
            return False
        if Image is None:
            return True
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            width, height = img.size
            return width >= 640 and height >= 360
    except Exception as exc:
        logger.debug("Image validation failed %s: %s", image_path, exc)
        return False


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
    min_bytes = 10_000 if source in {"local", "scene"} else MIN_IMAGE_BYTES
    if not validate_image_file(image_path, min_bytes=min_bytes):
        return None
    if Image is None:
        return None
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            score = 0.0

            if width < 1280 or height < 720:
                if source in REAL_PHOTO_SOURCES and width >= 960 and height >= 540:
                    score += 15
                else:
                    return None
            else:
                score += 25

            aspect = width / float(height)
            if 1.3 <= aspect <= 1.9:
                score += 15

            if source != "scene" and _detect_corner_watermark(img):
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


def _select_unique(
    scored: List[ImageScore],
    max_images: int,
    duplicate_threshold: int,
) -> List[str]:
    selected: List[str] = []
    seen_hashes: Set[str] = set()
    for item in scored:
        if any(_hamming_distance(item.phash, seen) <= duplicate_threshold for seen in seen_hashes):
            continue
        selected.append(item.path)
        seen_hashes.add(item.phash)
        if len(selected) >= max_images:
            break
    return selected


def filter_and_rank_images(
    candidates: List[Tuple[str, str]],
    min_score: float = 28.0,
    max_images: int = 8,
    duplicate_threshold: int = 8,
) -> List[str]:
    """Return ordered unique high-quality paths."""
    scored: List[ImageScore] = []
    for path, source in candidates:
        if not validate_image_file(path):
            continue
        result = score_image(path, source)
        if result and result.score >= min_score:
            scored.append(result)

    scored.sort(key=lambda item: (-item.score, -SOURCE_PRIORITY.get(item.source, 0)))
    selected = _select_unique(scored, max_images, duplicate_threshold)

    logger.info(
        "Image quality filter: %s/%s passed (min_score=%s)",
        len(selected),
        len(candidates),
        min_score,
    )
    return selected


def filter_and_rank_images_with_fallback(
    candidates: List[Tuple[str, str]],
    min_images: int = 4,
    max_images: int = 8,
    duplicate_threshold: int = 8,
) -> Tuple[List[str], Optional[str]]:
    """
    Progressive threshold: 28 -> 20 -> real photos only.
    Returns (selected_paths, best_real_photo_for_thumbnail).
    """
    thresholds = (28.0, 20.0, 0.0)
    for threshold in thresholds:
        if threshold == 0.0:
            real_only = [
                (path, source)
                for path, source in candidates
                if source in REAL_PHOTO_SOURCES and validate_image_file(path)
            ]
            scored: List[ImageScore] = []
            for path, source in real_only:
                result = score_image(path, source)
                if result:
                    scored.append(result)
            scored.sort(key=lambda item: (-item.score, -SOURCE_PRIORITY.get(item.source, 0)))
            selected = _select_unique(scored, max_images, duplicate_threshold)
        else:
            selected = filter_and_rank_images(
                candidates,
                min_score=threshold,
                max_images=max_images,
                duplicate_threshold=duplicate_threshold,
            )

        if len(selected) >= min_images:
            best = _best_real_photo(candidates, selected)
            return selected, best

    selected = filter_and_rank_images(candidates, min_score=0.0, max_images=max_images)
    best = _best_real_photo(candidates, selected)
    return selected, best


def _best_real_photo(
    candidates: List[Tuple[str, str]],
    selected: List[str],
) -> Optional[str]:
    real_paths = {path for path, source in candidates if source in REAL_PHOTO_SOURCES}
    for path in selected:
        if path in real_paths:
            return path
    for path, source in candidates:
        if source in REAL_PHOTO_SOURCES and validate_image_file(path):
            return path
    return None


def pre_scale_image(image_path: str, output_path: Optional[str] = None) -> str:
    """Scale image to 1920x1080 with Lanczos; returns output path."""
    if Image is None or not validate_image_file(image_path, min_bytes=10_000):
        return image_path

    target = output_path or image_path
    try:
        with Image.open(image_path) as img:
            rgb = img.convert("RGB")
            if rgb.size == (1920, 1080):
                return image_path
            scaled = rgb.resize((1920, 1080), Image.Resampling.LANCZOS)
            scaled.save(target, "JPEG", quality=92, optimize=True)
            return target
    except Exception as exc:
        logger.debug("Pre-scale failed %s: %s", image_path, exc)
        return image_path


def pre_scale_images(image_paths: List[str], cache_dir: str) -> List[str]:
    """Pre-scale all images to 1920x1080 into cache_dir."""
    os.makedirs(cache_dir, exist_ok=True)
    scaled: List[str] = []
    for index, path in enumerate(image_paths):
        if not os.path.exists(path):
            continue
        out = os.path.join(cache_dir, f"scaled_{index:02d}.jpg")
        scaled.append(pre_scale_image(path, out))
    return scaled or image_paths
