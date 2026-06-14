"""Script retention scoring for YouTube engagement."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

BANNED_SOFT_GREETINGS = ("வணக்கம்", "வரவேற்கிறோம்", "இன்று நாம்", "பார்க்கப்போகிறோம்")
CURIOSITY_MARKERS = ("...", "?", "!", "ரகசியம்", "நம்ப முடியாத", "அதிர்ச்சி", "உண்மை")
EMOTIONAL_MARKERS = ("அன்ப", "பக்தி", "கண்ணீர்", "ஆசி", "நம்பிக்கை", "பயம்", "வியப்பு")
CTA_MARKERS = ("subscribe", "like", "comment", "பதிவு", "கருத்து", "பகிர்", "share")
PATTERN_INTERRUPT_MARKERS = ("[PAUSE", "...", "—", "ஆனால்", "இப்போது", "நினைத்தீர்களா")


@dataclass
class RetentionReport:
    score: int
    passed: bool
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_retention(script_text: str, min_score: int = 60) -> RetentionReport:
    """Score Tamil devotional script 0-100. Reject below min_score."""
    failures: List[str] = []
    warnings: List[str] = []
    score = 100

    if not script_text or len(script_text.strip()) < 200:
        return RetentionReport(score=0, passed=False, failures=["Script too short or empty"])

    words = script_text.split()
    intro = " ".join(words[:45])

    for phrase in BANNED_SOFT_GREETINGS:
        if phrase in intro:
            score -= 25
            failures.append(f"Soft greeting in hook: {phrase}")

    if not any(marker in intro for marker in CURIOSITY_MARKERS):
        score -= 20
        failures.append("Hook lacks curiosity gap")

    if "..." not in script_text and "?" not in script_text[:300]:
        score -= 10
        warnings.append("Missing suspense punctuation in opening")

    interrupt_count = sum(script_text.count(marker) for marker in PATTERN_INTERRUPT_MARKERS)
    expected_interrupts = max(2, len(words) // 120)
    if interrupt_count < expected_interrupts:
        penalty = min(20, (expected_interrupts - interrupt_count) * 5)
        score -= penalty
        warnings.append(f"Low pattern interrupts ({interrupt_count}/{expected_interrupts})")

    if not any(marker in script_text for marker in EMOTIONAL_MARKERS):
        score -= 15
        warnings.append("No clear emotional section detected")

    last_third = script_text[len(script_text) * 2 // 3 :]
    first_two_thirds = script_text[: len(script_text) * 2 // 3]
    early_cta = any(marker in first_two_thirds.lower() for marker in CTA_MARKERS)
    late_cta = any(marker in last_third.lower() for marker in CTA_MARKERS) or "[PAUSE_LONG]" in last_third

    if early_cta and not late_cta:
        score -= 15
        failures.append("CTA appears too early — keep near ending only")
    elif not late_cta:
        score -= 10
        warnings.append("Weak end CTA — add subscribe/comment near close")

    if "[PAUSE_LONG]" not in script_text and "[PAUSE_MED]" not in script_text:
        score -= 5
        warnings.append("No pause markers for TTS pacing")

    score = max(0, min(100, score))
    passed = score >= min_score and not failures
    return RetentionReport(score=score, passed=passed, failures=failures, warnings=warnings)


def retention_prompt_rules() -> str:
    """Inject into LLM script prompts."""
    return (
        "RETENTION RULES:\n"
        "- First 40 words: curiosity hook, NO greetings\n"
        "- Pattern interrupt every ~30 seconds (use ... or [PAUSE_MED])\n"
        "- One emotional devotional section mid-script\n"
        "- CTA (subscribe/comment) ONLY in final 20%\n"
    )
