"""Pre-upload quality validation for monetization-ready content."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger("media.quality_gate")

MIN_REAL_PHOTOS = 3
MIN_VIDEO_BYTES = 1_000_000
MIN_RETENTION_SCORE = 70


@dataclass
class QualityReport:
    passed: bool
    failures: List[str] = field(default_factory=list)
    real_photo_count: int = 0
    retention_score: int = 0
    video_size_mb: float = 0.0

    def log_summary(self, log_fn=logger.info) -> None:
        if self.passed:
            log_fn(
                f"Quality gate passed: real_photos={self.real_photo_count} "
                f"retention={self.retention_score} size={self.video_size_mb:.1f}MB"
            )
            return
        log_fn(f"Quality gate FAILED: {'; '.join(self.failures)}")


def validate_video_ready(
    video_path: Optional[str],
    script: str,
    retention_score: int,
    real_photo_count: int,
    min_real_photos: int = MIN_REAL_PHOTOS,
    min_retention: int = MIN_RETENTION_SCORE,
    min_video_bytes: int = MIN_VIDEO_BYTES,
) -> QualityReport:
    """Block upload when content quality is too low for monetization review."""
    failures: List[str] = []

    if real_photo_count < min_real_photos:
        failures.append(f"real_photos={real_photo_count} (need>={min_real_photos})")

    if retention_score < min_retention:
        failures.append(f"retention={retention_score} (need>={min_retention})")

    if not script or len(script.strip()) < 100:
        failures.append("script too short")

    video_size_mb = 0.0
    if not video_path or not os.path.exists(video_path):
        failures.append("video file missing")
    else:
        video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if os.path.getsize(video_path) < min_video_bytes:
            failures.append(f"video size {video_size_mb:.1f}MB too small")

    return QualityReport(
        passed=len(failures) == 0,
        failures=failures,
        real_photo_count=real_photo_count,
        retention_score=retention_score,
        video_size_mb=video_size_mb,
    )
