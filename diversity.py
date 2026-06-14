"""Topic / hook / format diversity engine with TTL tracking."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from utils.logger import get_logger

logger = get_logger("diversity")

TRACKING_DIR = Path("data/tracking")
TTL_RULES = {
    "used_topics.json": 90 * 24 * 3600,
    "used_hooks.json": 30 * 24 * 3600,
    "used_formats.json": 90 * 24 * 3600,
    "used_deities.json": 30 * 24 * 3600,
    "used_thumbnails.json": 60 * 24 * 3600,
}


def _normalize_topic(topic: str) -> str:
    return re.sub(r"[^\w\s]", "", topic.lower().strip())[:60]


class DiversityEngine:
    """Enforces content variation windows for monetization safety."""

    def __init__(self, tracking_dir: Path = TRACKING_DIR):
        self.tracking_dir = Path(tracking_dir)
        self.tracking_dir.mkdir(parents=True, exist_ok=True)
        self.cache: Dict[str, Dict[str, float]] = {}
        for filename in TTL_RULES:
            self.cache[filename] = self._load_file(filename)

    def _path(self, filename: str) -> Path:
        return self.tracking_dir / filename

    def _load_file(self, filename: str) -> Dict[str, float]:
        path = self._path(filename)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_file(self, filename: str) -> None:
        path = self._path(filename)
        path.write_text(json.dumps(self.cache[filename], indent=2, ensure_ascii=False), encoding="utf-8")

    def _prune(self, filename: str, now: float) -> None:
        ttl = TTL_RULES[filename]
        self.cache[filename] = {
            key: ts for key, ts in self.cache[filename].items() if (now - ts) < ttl
        }

    def is_topic_allowed(self, topic: str) -> bool:
        now = time.time()
        self._prune("used_topics.json", now)
        normalized = _normalize_topic(topic)
        for registered, _ in self.cache["used_topics.json"].items():
            if normalized in registered or registered in normalized:
                logger.warning("Topic rejected (90-day window): %s", topic[:80])
                return False
        return True

    def is_hook_allowed(self, hook: str) -> bool:
        now = time.time()
        self._prune("used_hooks.json", now)
        key = hook.strip()[:120]
        if key in self.cache["used_hooks.json"]:
            logger.warning("Hook rejected (30-day window): %s", key[:80])
            return False
        return True

    def is_thumbnail_text_allowed(self, text: str) -> bool:
        now = time.time()
        self._prune("used_thumbnails.json", now)
        key = text.strip()[:80]
        if key in self.cache["used_thumbnails.json"]:
            logger.warning("Thumbnail text rejected (60-day window): %s", key)
            return False
        return True

    def validate_pattern(
        self,
        topic: str,
        hook: str,
        fmt: str,
        thumbnail_text: str,
        deity: str = "",
    ) -> bool:
        now = time.time()
        for filename in TTL_RULES:
            self._prune(filename, now)

        if not self.is_topic_allowed(topic):
            return False
        if not self.is_hook_allowed(hook):
            return False
        if not self.is_thumbnail_text_allowed(thumbnail_text):
            return False

        fmt_key = fmt.strip()[:80]
        if fmt_key in self.cache["used_formats.json"]:
            logger.warning("Format rejected (90-day window): %s", fmt_key)
            return False

        deity_key = deity.strip()[:40]
        if deity_key and deity_key in self.cache["used_deities.json"]:
            logger.warning("Deity recently used (30-day window): %s", deity_key)

        return True

    def register_pattern(
        self,
        topic: str,
        hook: str,
        fmt: str,
        thumbnail_text: str,
        deity: str = "",
    ) -> None:
        now = time.time()
        self.cache["used_topics.json"][_normalize_topic(topic)] = now
        self.cache["used_hooks.json"][hook.strip()[:120]] = now
        self.cache["used_formats.json"][fmt.strip()[:80]] = now
        self.cache["used_thumbnails.json"][thumbnail_text.strip()[:80]] = now
        if deity.strip():
            self.cache["used_deities.json"][deity.strip()[:40]] = now
        for filename in TTL_RULES:
            self._save_file(filename)
        logger.info("Registered diversity pattern for topic=%s", topic[:60])

    def pick_least_used(self, options, usage_dict: dict, key_fn=None):
        scored = []
        for option in options:
            key = key_fn(option) if key_fn else str(option)
            scored.append((usage_dict.get(key, 0), option))
        scored.sort(key=lambda item: item[0])
        chosen = scored[0][1]
        key = key_fn(chosen) if key_fn else str(chosen)
        usage_dict[key] = usage_dict.get(key, 0) + 1
        return chosen, usage_dict


_default_engine: Optional[DiversityEngine] = None


def get_diversity_engine() -> DiversityEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = DiversityEngine()
    return _default_engine
