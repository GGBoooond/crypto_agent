"""Dual reviewer scoring for skill patches."""
from __future__ import annotations

from typing import Any, Dict, Optional

from harness.cost.reviewer_client import ReviewerClient


_MIN_REVIEWER_SCORE = 0.7


class PatchReviewer:
    """Ask two reviewer prompts before applying a skill patch."""

    def __init__(self, reviewer_client: Optional[ReviewerClient] = None) -> None:
        self.reviewer_client = reviewer_client

    def review(
        self,
        patch: Dict[str, Any],
        *,
        trace: Optional[Dict[str, Any]] = None,
        fallback_score: float = 0.0,
    ) -> Dict[str, Any]:
        if self.reviewer_client is None or not self.reviewer_client.is_configured:
            return {
                "reviewer_a_score": fallback_score,
                "reviewer_b_score": fallback_score,
                "accepted": fallback_score >= _MIN_REVIEWER_SCORE,
                "source": "deterministic_fallback",
            }
        reviewer_a = self._ask_reviewer(
            "Does this patch match the trace evidence? Score 0-1.",
            patch,
            trace,
        )
        reviewer_b = self._ask_reviewer(
            "Does this patch avoid overfitting to one narrow case? Score 0-1.",
            patch,
            trace,
        )
        return {
            "reviewer_a_score": reviewer_a,
            "reviewer_b_score": reviewer_b,
            "accepted": reviewer_a >= _MIN_REVIEWER_SCORE and reviewer_b >= _MIN_REVIEWER_SCORE,
            "source": "reviewer_llm",
        }

    def _ask_reviewer(
        self,
        question: str,
        patch: Dict[str, Any],
        trace: Optional[Dict[str, Any]],
    ) -> float:
        if self.reviewer_client is None:
            return 0.0
        payload = self.reviewer_client.chat_json_sync(
            [
                {
                    "role": "system",
                    "content": "Return JSON only: {\"score\": 0.0, \"reason\": \"short\"}.",
                },
                {
                    "role": "user",
                    "content": f"{question}\npatch={patch}\ntrace={trace or {}}",
                },
            ],
            max_tokens=300,
        )
        try:
            return max(0.0, min(float(payload.get("score")), 1.0))
        except (TypeError, ValueError):
            return 0.0
