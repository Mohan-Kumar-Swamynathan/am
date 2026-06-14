"""Retry helpers with exponential backoff and subprocess protection."""

from __future__ import annotations

import subprocess
import time
from functools import wraps
from typing import Callable, List, Optional, TypeVar

from utils.logger import get_logger

logger = get_logger("utils.retry")
T = TypeVar("T")


def run_command_with_retry(
    cmd: List[str],
    max_retries: int = 3,
    initial_delay: float = 5.0,
    timeout: int = 600,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    last_result: Optional[subprocess.CompletedProcess] = None
    preview = " ".join(cmd[:10])
    if len(cmd) > 10:
        preview += " ..."

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Executing command (attempt %s/%s): %s", attempt, max_retries, preview)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            last_result = result
            if result.returncode == 0:
                return result
            logger.warning(
                "Command failed rc=%s stderr=%s",
                result.returncode,
                (result.stderr or "")[-300:],
            )
        except subprocess.TimeoutExpired:
            logger.error("Command timed out after %ss: %s", timeout, preview)
        except Exception as exc:
            logger.error("Command exception: %s", exc)

        if attempt < max_retries:
            delay = initial_delay * (2 ** (attempt - 1))
            logger.info("Retrying in %ss...", delay)
            time.sleep(delay)

    if last_result is not None:
        return last_result
    raise RuntimeError(f"Command failed after {max_retries} attempts: {preview}")


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 2.0,
    exceptions: tuple = (Exception,),
):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= max_retries:
                        raise
                    logger.warning(
                        "%s failed (attempt %s/%s): %s — retry in %ss",
                        func.__name__,
                        attempt,
                        max_retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= 2
            raise RuntimeError(f"{func.__name__} exhausted retries")

        return wrapper

    return decorator
