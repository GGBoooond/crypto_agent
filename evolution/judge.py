"""Patch scoring for skill evolution proposals."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, Optional


class PatchJudge:
    """Score patches with deterministic performance and novelty signals."""

    def score(
        self,
        patch: Dict[str, Any],
        reviewer_a_score: Optional[float] = None,
        reviewer_b_score: Optional[float] = None,
        *,
        skill_stats: Optional[Dict[str, Any]] = None,
        existing_text: str = "",
    ) -> Dict[str, Any]:
        if reviewer_a_score is not None and reviewer_b_score is not None:
            score = (reviewer_a_score + reviewer_b_score) / 2
        else:
            score = self._score_from_evidence(patch, skill_stats or {}, existing_text)
        return {
            "score": round(score, 3),
            "accepted": score >= 0.8,
            "reason": self._reason(score),
            "patch": patch,
        }

    def _score_from_evidence(
        self,
        patch: Dict[str, Any],
        skill_stats: Dict[str, Any],
        existing_text: str,
    ) -> float:
        pf_score = self._profit_factor_score(skill_stats.get("profit_factor"))
        novelty_score = self._novelty_score(patch, existing_text)
        support_score = self._support_score(skill_stats)
        return (pf_score * 0.4) + (novelty_score * 0.35) + (support_score * 0.25)

    @staticmethod
    def _profit_factor_score(value: Any) -> float:
        try:
            profit_factor = float(value)
        except (TypeError, ValueError):
            return 0.5
        if profit_factor <= 0:
            return 0.2
        return max(0.0, min(profit_factor / 2.0, 1.0))

    @staticmethod
    def _novelty_score(patch: Dict[str, Any], existing_text: str) -> float:
        candidate = str(patch.get("new_string") or patch.get("content") or "")
        if not candidate or not existing_text:
            return 0.6
        similarity = SequenceMatcher(None, candidate.lower(), existing_text.lower()).ratio()
        return max(0.0, 1.0 - similarity)

    @staticmethod
    def _support_score(skill_stats: Dict[str, Any]) -> float:
        sample_size = skill_stats.get("sample_size")
        try:
            samples = float(sample_size)
        except (TypeError, ValueError):
            return 0.4
        return max(0.0, min(samples / 30.0, 1.0))

    @staticmethod
    def _reason(score: float) -> str:
        if score >= 0.8:
            return "accepted_high_confidence"
        if score >= 0.5:
            return "needs_human_review"
        return "rejected_below_threshold"

