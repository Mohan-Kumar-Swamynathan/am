"""Auto Shorts generator — 3 vertical clips per long video."""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

from thumbnail_engine import extract_thumbnail_text, generate_thumbnail
from utils.logger import get_logger
from utils.retry import run_command_with_retry

logger = get_logger("shorts_generator")

SHORTS_DIR = Path("shorts")
SHORTS_META_DIR = Path("metadata/shorts")
UPLOAD_QUEUE_FILE = Path("upload_queue.json")

VERTICAL_FILTER = (
    "[0:v]split=2[bg][fg];"
    "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[blurred];"
    "[fg]scale=1080:607,pad=1080:1920:0:(1920-607)/2:black[padded];"
    "[blurred][padded]overlay=0:(H-h)/2"
)

FALLBACK_FILTER = (
    "scale=1080:1920:force_original_aspect_ratio=decrease,"
    "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
)


@dataclass
class ShortClipSpec:
    index: int
    start_seconds: float
    duration_seconds: float
    title_suffix: str


@dataclass
class GeneratedShort:
    video_path: str
    thumbnail_path: str
    title: str
    description: str
    tags: str
    pinned_comment: str
    topic: str
    parent_output_name: str


DEFAULT_SPECS = (
    ShortClipSpec(1, 8.0, 45.0, "🔥 Hook"),
    ShortClipSpec(2, 90.0, 50.0, "✨ Highlight"),
    ShortClipSpec(3, 180.0, 55.0, "🙏 Blessing"),
)


def _get_duration(video_path: str) -> float:
    import subprocess

    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True,
        text=True,
        timeout=15,
    )
    try:
        return max(30.0, float(result.stdout.strip() or "300"))
    except ValueError:
        return 300.0


def _extract_clip(
    source_video: str,
    output_path: str,
    start: float,
    duration: float,
) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", source_video,
        "-vf", VERTICAL_FILTER,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = run_command_with_retry(cmd, max_retries=2, timeout=240)
    if result.returncode == 0:
        return True

    fallback_cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", source_video,
        "-vf", FALLBACK_FILTER,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    result = run_command_with_retry(fallback_cmd, max_retries=2, timeout=240)
    return result.returncode == 0


def _build_short_metadata(
    base_metadata: Dict,
    spec: ShortClipSpec,
    output_name: str,
    thumb_text: str,
) -> Dict[str, str]:
    base_title = base_metadata.get("title", "ஆலய மணி Short")[:80]
    topic = base_metadata.get("topic", base_title)
    deity = base_metadata.get("deity", "")
    tags = base_metadata.get("tags", "tamil devotional, aalaya mani, shorts")

    title = f"{thumb_text} {spec.title_suffix} | Short | ஆலய மணி"[:100]
    description = (
        f"{thumb_text}\n\n"
        f"{topic}\n\n"
        f"🙏 Full video on @aalayamani channel\n"
        f"#Shorts #TamilDevotional #AalayaMani #{deity}\n\n"
        f"{base_metadata.get('description', '')[:1500]}"
    )[:4900]

    pinned = base_metadata.get(
        "pinned_comment",
        "இந்த Short பிடித்திருந்தா full video-வை பாருங்கள் 🙏",
    )[:480]

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "pinned_comment": pinned,
        "topic": topic,
        "deity": deity,
        "thumbnail_text": thumb_text,
        "parent_output_name": output_name,
        "clip_index": spec.index,
    }


def generate_shorts_from_video(
    source_video: str,
    output_name: str,
    base_metadata: Dict,
    specs: Optional[List[ShortClipSpec]] = None,
    deity_name: str = "",
    bg_image_path: Optional[str] = None,
) -> List[GeneratedShort]:
    if not os.path.exists(source_video):
        logger.error("Source video missing for shorts: %s", source_video)
        return []

    SHORTS_DIR.mkdir(parents=True, exist_ok=True)
    SHORTS_META_DIR.mkdir(parents=True, exist_ok=True)

    total_duration = _get_duration(source_video)
    clip_specs = list(specs or DEFAULT_SPECS)
    thumb_text = extract_thumbnail_text(base_metadata.get("title", ""), deity_name)
    generated: List[GeneratedShort] = []

    for spec in clip_specs:
        if spec.start_seconds + 20 >= total_duration:
            spec = ShortClipSpec(spec.index, 5.0, min(spec.duration_seconds, 40.0), spec.title_suffix)

        short_path = SHORTS_DIR / f"{output_name}_short_{spec.index}.mp4"
        if not _extract_clip(source_video, str(short_path), spec.start_seconds, spec.duration_seconds):
            logger.warning("Short clip %s failed", spec.index)
            continue

        meta = _build_short_metadata(base_metadata, spec, output_name, thumb_text)
        thumb_path = generate_thumbnail(
            title=meta["title"],
            deity_name=deity_name,
            output_name=f"{output_name}_short_{spec.index}",
            thumbnail_text=thumb_text,
            bg_image_path=bg_image_path,
            output_dir=SHORTS_DIR,
        )

        meta_path = SHORTS_META_DIR / f"{output_name}_short_{spec.index}.json"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        generated.append(
            GeneratedShort(
                video_path=str(short_path),
                thumbnail_path=thumb_path or "",
                title=meta["title"],
                description=meta["description"],
                tags=meta["tags"],
                pinned_comment=meta["pinned_comment"],
                topic=meta["topic"],
                parent_output_name=output_name,
            )
        )
        logger.info("Short %s ready: %s", spec.index, short_path)

    return generated


def queue_shorts_for_upload(
    shorts: List[GeneratedShort],
    privacy: str = "public",
    queue_file: Path = UPLOAD_QUEUE_FILE,
    append_fn: Optional[Callable[[Dict], None]] = None,
) -> None:
    if not shorts:
        return

    queue: List[Dict] = []
    if queue_file.exists():
        try:
            queue = json.loads(queue_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            queue = []

    for short in shorts:
        item = {
            "video_path": short.video_path,
            "metadata": {
                "title": short.title,
                "description": short.description,
                "tags": short.tags,
                "pinned_comment": short.pinned_comment,
                "topic": short.topic,
                "thumbnail_path": short.thumbnail_path,
                "content_type": "short",
                "parent_output_name": short.parent_output_name,
            },
            "privacy": privacy,
            "queued_at": datetime.datetime.now().isoformat(),
            "status": "pending",
        }
        queue.append(item)
        if append_fn:
            append_fn(item)

    queue_file.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Queued %s shorts for upload", len(shorts))
