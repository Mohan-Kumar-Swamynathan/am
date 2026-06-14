"""YouTube Analytics feedback loop — stores performance.json."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger("analytics")

PERFORMANCE_FILE = Path("data/tracking/performance.json")
LEGACY_FILE = Path("analytics_insights.json")
METADATA_DIR = Path("metadata")


def _empty_performance() -> Dict[str, Any]:
    return {
        "historical_runs": [],
        "winning_deities": [],
        "winning_topics": [],
        "winning_hooks": [],
        "winning_formats": [],
        "deity_avg_views": {},
        "topic_avg_watch_time": {},
        "updated": None,
    }


def load_performance() -> Dict[str, Any]:
    for path in (PERFORMANCE_FILE, LEGACY_FILE):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                merged = _empty_performance()
                merged.update(data)
                return merged
            except (json.JSONDecodeError, OSError):
                pass
    return _empty_performance()


def save_performance(data: Dict[str, Any]) -> None:
    PERFORMANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.datetime.now().isoformat()
    PERFORMANCE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    LEGACY_FILE.write_text(json.dumps(_legacy_view(data), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved analytics to %s", PERFORMANCE_FILE)


def _legacy_view(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "best_deity": data.get("winning_deities", [""])[0] if data.get("winning_deities") else "",
        "deity_avg": data.get("deity_avg_views", {}),
        "updated": data.get("updated"),
    }


def _parse_metadata_file(meta_path: Path) -> Dict[str, str]:
    parsed = {
        "video_id": "",
        "deity": "",
        "topic": "",
        "hook_style": "",
        "format": "",
        "title": "",
    }
    for line in meta_path.read_text(encoding="utf-8").splitlines():
        upper = line.upper()
        if upper.startswith("VIDEO_ID:"):
            parsed["video_id"] = line.split(":", 1)[1].strip()
        elif upper.startswith("DEITY:"):
            parsed["deity"] = line.split(":", 1)[1].strip()
        elif upper.startswith("TOPIC:"):
            parsed["topic"] = line.split(":", 1)[1].strip()
        elif upper.startswith("HOOK:"):
            parsed["hook_style"] = line.split(":", 1)[1].strip()
        elif upper.startswith("FORMAT:"):
            parsed["format"] = line.split(":", 1)[1].strip()
        elif upper.startswith("TITLE:"):
            parsed["title"] = line.split(":", 1)[1].strip()
    return parsed


def fetch_video_metrics(youtube_service, video_id: str) -> Dict[str, float]:
    metrics = {
        "views": 0.0,
        "watch_time_hours": 0.0,
        "retention_p30": 0.0,
        "ctr": 0.0,
        "subscribers_gained": 0.0,
    }
    if not youtube_service or not video_id:
        return metrics

    try:
        from googleapiclient.discovery import build as build_service

        credentials = youtube_service._http.credentials
        analytics = build_service("youtubeAnalytics", "v2", credentials=credentials)
        end_date = datetime.datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched,averageViewPercentage,subscribersGained",
            filters=f"video=={video_id}",
            dimensions="video",
        ).execute()
        rows = response.get("rows", [])
        if rows:
            row = rows[0]
            metrics["views"] = float(row[1] or 0)
            metrics["watch_time_hours"] = float(row[2] or 0) / 60.0
            metrics["retention_p30"] = float(row[3] or 0)
            metrics["subscribers_gained"] = float(row[4] or 0)
    except Exception as exc:
        logger.warning("Analytics API failed for %s: %s", video_id, exc)

    try:
        stats = (
            youtube_service.videos()
            .list(part="statistics", id=video_id)
            .execute()
            .get("items", [{}])[0]
            .get("statistics", {})
        )
        metrics["views"] = max(metrics["views"], float(stats.get("viewCount", 0)))
    except Exception:
        pass

    return metrics


def run_analytics_loop(
    get_youtube_service: Callable[[], Any],
    metadata_dir: Path = METADATA_DIR,
    git_commit_fn: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    logger.info("Running analytics feedback loop...")
    youtube = get_youtube_service()
    if not youtube:
        logger.warning("YouTube auth unavailable — skipping analytics")
        return load_performance()

    performance = load_performance()
    historical = performance.setdefault("historical_runs", [])

    meta_files = sorted(metadata_dir.glob("*.txt"), reverse=True)[:25]
    for meta_file in meta_files:
        meta = _parse_metadata_file(meta_file)
        video_id = meta.get("video_id", "")
        if not video_id:
            continue

        metrics = fetch_video_metrics(youtube, video_id)
        if metrics["views"] <= 0:
            continue

        entry = {
            "video_id": video_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "metrics": metrics,
            "metadata": {
                "deity": meta.get("deity", ""),
                "topic": meta.get("topic", "") or meta.get("title", ""),
                "hook_style": meta.get("hook_style", ""),
                "format": meta.get("format", ""),
            },
        }
        historical.append(entry)

    performance["historical_runs"] = historical[-200:]
    _compute_winners(performance)
    save_performance(performance)

    if git_commit_fn:
        try:
            git_commit_fn()
        except Exception as exc:
            logger.warning("Analytics git commit failed: %s", exc)

    logger.info(
        "Analytics complete — best deity: %s",
        performance.get("winning_deities", ["none"])[0],
    )
    return performance


def _compute_winners(performance: Dict[str, Any]) -> None:
    runs = performance.get("historical_runs", [])
    if not runs:
        return

    avg_watch = sum(r["metrics"].get("watch_time_hours", 0) for r in runs) / len(runs)
    winners = [r for r in runs if r["metrics"].get("watch_time_hours", 0) >= avg_watch]

    performance["winning_deities"] = _top_keys(winners, "deity", limit=5)
    performance["winning_topics"] = _top_keys(winners, "topic", limit=5)
    performance["winning_hooks"] = _top_keys(winners, "hook_style", limit=5)
    performance["winning_formats"] = _top_keys(winners, "format", limit=5)

    deity_views: Dict[str, List[float]] = {}
    for run in runs:
        deity = run.get("metadata", {}).get("deity", "")
        if deity:
            deity_views.setdefault(deity, []).append(run["metrics"].get("views", 0))
    performance["deity_avg_views"] = {
        deity: sum(values) / len(values) for deity, values in deity_views.items()
    }


def _top_keys(runs: List[Dict], field: str, limit: int = 5) -> List[str]:
    counts: Dict[str, int] = {}
    for run in runs:
        value = run.get("metadata", {}).get(field, "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return [key for key, _ in sorted(counts.items(), key=lambda item: -item[1])[:limit]]


def get_content_bias_prompt() -> str:
    data = load_performance()
    if not data.get("winning_deities"):
        return ""
    return (
        f"Analytics bias — prefer deities: {', '.join(data['winning_deities'][:3])}. "
        f"Winning hooks: {', '.join(data.get('winning_hooks', [])[:3])}. "
        f"Winning formats: {', '.join(data.get('winning_formats', [])[:3])}."
    )


def load_analytics_insights() -> Dict[str, Any]:
    """Backward-compatible alias."""
    return _legacy_view(load_performance())
