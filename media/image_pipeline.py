"""Tiered image assembly — real photos first, generated scenes last."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from image_quality import (
    REAL_PHOTO_SOURCES,
    filter_and_rank_images_with_fallback,
    pre_scale_images,
    validate_image_file,
)
from utils.logger import get_logger

logger = get_logger("media.image_pipeline")

TOPIC_PEXELS_QUERIES = {
    "festival": ["temple festival india", "hindu festival crowd colorful"],
    "history": ["ancient temple ruins india", "stone inscription temple"],
    "science": ["temple bells sound waves", "acoustic meditation india"],
    "ritual": ["hindu puja ritual india", "aarti ceremony temple lamps"],
    "mantra": ["meditation india om chant", "yoga spiritual india"],
}


@dataclass
class ImageAssemblyResult:
    image_paths: List[str] = field(default_factory=list)
    candidates: List[Tuple[str, str]] = field(default_factory=list)
    best_real_photo: Optional[str] = None
    real_photo_count: int = 0
    scene_count: int = 0
    thumb_bg: Optional[str] = None


def composite_photo_with_vignette(photo_path: str, output_path: Optional[str] = None) -> str:
    """Apply subtle vignette for visual cohesion."""
    try:
        from PIL import Image, ImageDraw, ImageEnhance

        if not validate_image_file(photo_path, min_bytes=10_000):
            return photo_path

        target = output_path or photo_path
        with Image.open(photo_path) as img:
            canvas = img.convert("RGB").resize((1920, 1080), Image.Resampling.LANCZOS)
            canvas = ImageEnhance.Contrast(canvas).enhance(1.08)
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            for margin in range(0, 180, 20):
                alpha = min(90, margin // 3)
                draw.rectangle(
                    [(margin, margin), (1920 - margin, 1080 - margin)],
                    outline=(0, 0, 0, alpha),
                )
            canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
            canvas.save(target, "JPEG", quality=92, optimize=True)
            return target
    except Exception as exc:
        logger.debug("Vignette failed %s: %s", photo_path, exc)
        return photo_path


def _topic_pexels_query(topic: str) -> Optional[str]:
    topic_lower = (topic or "").lower()
    keyword_map = {
        "festival": ["festival", "திருவிழா", "கும்பாபிஷேகம்"],
        "history": ["history", "வரலாறு", "ancient", "பழமை"],
        "science": ["science", "ஆராய்ச்சி", "sound", "frequency"],
        "ritual": ["ritual", "பூஜை", "worship", "வழிபாடு"],
        "mantra": ["mantra", "ஓம்", "meditation", "தியானம்"],
    }
    for key, keywords in keyword_map.items():
        if any(word in topic_lower for word in keywords):
            return random.choice(TOPIC_PEXELS_QUERIES[key])
    return None


def _fetch_topic_pexels(
    topic: str,
    img_dir: str,
    pexels_api_key: str,
    log_fn: Callable[[str], None],
) -> List[str]:
    if not pexels_api_key:
        return []

    query = _topic_pexels_query(topic)
    if not query:
        return []

    import requests

    downloaded: List[str] = []
    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": pexels_api_key},
            params={
                "query": query,
                "per_page": 4,
                "orientation": "landscape",
                "page": random.randint(1, 3),
            },
            timeout=12,
        )
        if response.status_code != 200:
            return []
        for photo in response.json().get("photos", []):
            url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("original", "")
            if not url:
                continue
            file_path = os.path.join(img_dir, f"topic_{len(downloaded)}.jpg")
            img_resp = requests.get(url, timeout=25, stream=True)
            if img_resp.status_code != 200:
                continue
            with open(file_path, "wb") as handle:
                for chunk in img_resp.iter_content(8192):
                    handle.write(chunk)
            if validate_image_file(file_path):
                downloaded.append(file_path)
        if downloaded:
            log_fn(f"  Topic Pexels: {len(downloaded)} ({query})")
    except Exception as exc:
        log_fn(f"  Topic Pexels skipped: {exc}")
    return downloaded


def _collect_local_fallback(image_file: str, log_fn: Callable[[str], None]) -> List[str]:
    paths: List[str] = []
    if os.path.isdir("images"):
        for name in sorted(os.listdir("images")):
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                candidate = os.path.join("images", name)
                if validate_image_file(candidate, min_bytes=10_000):
                    paths.append(candidate)
    if paths:
        log_fn(f"  Local images: {len(paths)}")
        return paths[:6]
    if image_file and os.path.exists(image_file) and validate_image_file(image_file, min_bytes=5_000):
        log_fn("  Using fallback image.png")
        return [image_file]
    return []


def assemble_video_images(
    deity: str,
    deity_en: str,
    topic: str,
    day: str,
    img_dir: str,
    fetch_wikimedia_fn: Callable,
    fetch_pollinations_fn: Callable,
    fetch_pexels_deity_fn: Callable,
    generate_scenes_fn: Callable,
    pexels_api_key: str = "",
    fallback_image: str = "image.png",
    min_real_photos: int = 4,
    max_images: int = 8,
    log_fn: Callable[[str], None] = logger.info,
) -> ImageAssemblyResult:
    """Assemble video images: Wikimedia -> Pollinations -> Pexels -> local -> scenes."""
    os.makedirs(img_dir, exist_ok=True)
    candidates: List[Tuple[str, str]] = []

    wiki_imgs = fetch_wikimedia_fn(deity, img_dir, count=4) or []
    wiki_imgs = [path for path in wiki_imgs if validate_image_file(path)]
    for path in wiki_imgs:
        candidates.append((path, "wikimedia"))
    if wiki_imgs:
        log_fn(f"  Wikimedia: {len(wiki_imgs)} photos")

    poll_paths: List[str] = []
    for index in range(2):
        poll_path = os.path.join(img_dir, f"ai_scene_{index}.jpg")
        fetched = fetch_pollinations_fn(deity_en, topic, poll_path)
        if fetched and validate_image_file(fetched):
            poll_paths.append(fetched)
    for path in poll_paths:
        candidates.append((path, "pollinations"))
    if poll_paths:
        log_fn(f"  Pollinations: {len(poll_paths)} photos")

    topic_pexels = _fetch_topic_pexels(topic, img_dir, pexels_api_key, log_fn)
    for path in topic_pexels:
        candidates.append((path, "pexels"))

    pexels_imgs = fetch_pexels_deity_fn(deity, day) or []
    pexels_imgs = [path for path in pexels_imgs if validate_image_file(path)]
    for path in pexels_imgs:
        if path not in {item[0] for item in candidates}:
            candidates.append((path, "pexels"))
    if pexels_imgs:
        log_fn(f"  Pexels deity: {len(pexels_imgs)} photos")
    elif not pexels_api_key:
        log_fn("  Pexels skipped (no key)")

    local_imgs = _collect_local_fallback(fallback_image, log_fn)
    for path in local_imgs:
        candidates.append((path, "local"))

    real_candidates = [(path, source) for path, source in candidates if source in REAL_PHOTO_SOURCES]
    real_photo_count = len({path for path, _ in real_candidates})

    filtered, best_real = filter_and_rank_images_with_fallback(
        candidates,
        min_images=min_real_photos,
        max_images=max_images,
    )

    scene_paths: List[str] = []
    if len(filtered) < min_real_photos:
        scene_needed = min(2, max(1, min_real_photos - len(filtered)))
        scene_paths = generate_scenes_fn(day, topic=topic, scene_type=deity, num_scenes=scene_needed, channel="am")
        for path in scene_paths:
            candidates.append((path, "scene"))
        if scene_paths:
            log_fn(f"  Scene filler: {len(scene_paths)} (real photos below target)")

    if not filtered and real_candidates:
        filtered = [path for path, _ in real_candidates[:max_images]]
        best_real = filtered[0] if filtered else best_real

    if scene_paths:
        for path in scene_paths:
            if path not in filtered:
                filtered.append(path)

    filtered = filtered[:max_images]
    real_path_set = {path for path, source in candidates if source in REAL_PHOTO_SOURCES}
    real_in_final = sum(1 for path in filtered if path in real_path_set)
    scene_count = sum(1 for path in filtered if any(
        path == candidate_path and source == "scene" for candidate_path, source in candidates
    ))

    scaled_dir = os.path.join(img_dir, "scaled")
    filtered = pre_scale_images(filtered, scaled_dir)

    thumb_bg = best_real
    if thumb_bg and validate_image_file(thumb_bg):
        thumb_bg = composite_photo_with_vignette(thumb_bg, os.path.join(img_dir, "thumb_bg.jpg"))

    log_fn(f"  real_photos={real_in_final} scenes={scene_count} total={len(filtered)}")

    return ImageAssemblyResult(
        image_paths=filtered,
        candidates=candidates,
        best_real_photo=best_real,
        real_photo_count=real_in_final,
        scene_count=scene_count,
        thumb_bg=thumb_bg,
    )
