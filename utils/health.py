"""Pre-flight health checks before pipeline execution."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List

from utils.logger import get_logger

logger = get_logger("utils.health")


def run_health_checks(require_llm: bool = True) -> Dict[str, bool]:
    results: Dict[str, bool] = {}
    results["ffmpeg"] = shutil.which("ffmpeg") is not None
    results["ffprobe"] = shutil.which("ffprobe") is not None
    results["edge_tts"] = shutil.which("edge-tts") is not None

    if require_llm:
        has_gemini = bool(os.environ.get("GEMINI_KEY"))
        has_groq = bool(os.environ.get("GROQ_API_KEY"))
        has_github = bool(os.environ.get("GITHUB_TOKEN"))
        has_cerebras = bool(os.environ.get("CEREBRAS_API_KEY"))
        results["llm_key"] = has_gemini or has_groq or has_github or has_cerebras
    else:
        results["llm_key"] = True

    for directory in ("videos", "shorts", "metadata", "scripts", "data/tracking"):
        Path(directory).mkdir(parents=True, exist_ok=True)

    failed: List[str] = [name for name, ok in results.items() if not ok]
    if failed:
        logger.error("Health check failed: %s", ", ".join(failed))
        for name in failed:
            print(f"  MISSING: {name}")
        sys.exit(1)

    logger.info("Health checks passed: %s", results)
    return results
