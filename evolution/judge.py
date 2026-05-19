"""Patch scoring for skill evolution proposals."""
from __future__ import annotations

import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Optional

from .patch_reviewer import PatchReviewer


class PatchJudge:
    """Score patches with deterministic performance and novelty signals."""

    def __init__(self, patch_reviewer: Optional[PatchReviewer] = None) -> None:
        self.patch_reviewer = patch_reviewer

    def score(
        self,
        patch: Dict[str, Any],
        reviewer_a_score: Optional[float] = None,
        reviewer_b_score: Optional[float] = None,
        *,
        skill_stats: Optional[Dict[str, Any]] = None,
        existing_text: str = "",
        trace: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if reviewer_a_score is not None and reviewer_b_score is not None:
            score = (reviewer_a_score + reviewer_b_score) / 2
            reviewer_payload = {
                "reviewer_a_score": reviewer_a_score,
                "reviewer_b_score": reviewer_b_score,
                "accepted": reviewer_a_score >= 0.7 and reviewer_b_score >= 0.7,
                "source": "explicit_scores",
            }
        else:
            score = self._score_from_evidence(patch, skill_stats or {}, existing_text)
            reviewer_payload = {
                "reviewer_a_score": None,
                "reviewer_b_score": None,
                "accepted": score >= 0.8,
                "source": "deterministic",
            }
            if self.patch_reviewer is not None:
                reviewer_payload = self.patch_reviewer.review(
                    patch,
                    trace=trace,
                    fallback_score=score,
                )
                score = min(
                    float(reviewer_payload["reviewer_a_score"]),
                    float(reviewer_payload["reviewer_b_score"]),
                )
        return {
            "score": round(score, 3),
            "accepted": score >= 0.8 and bool(reviewer_payload.get("accepted")),
            "reason": self._reason(score),
            "patch": patch,
            **reviewer_payload,
        }

    def load_skill_stats(self, skill_id: str, db_path: str = "memory/trades.db") -> Dict[str, Any]:
        path = Path(db_path)
        if not skill_id or not path.exists():
            return {}
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT sample_size, profit_factor, ic_pearson
                FROM skill_performance
                WHERE skill_id = ?
                """,
                (skill_id,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        total_samples = sum(int(row.get("sample_size") or 0) for row in rows)
        if total_samples <= 0:
            return {}
        weighted_pf = sum(
            float(row.get("profit_factor") or 0.0) * int(row.get("sample_size") or 0)
            for row in rows
        ) / total_samples
        weighted_ic = sum(
            float(row.get("ic_pearson") or 0.0) * int(row.get("sample_size") or 0)
            for row in rows
        ) / total_samples
        return {
            "sample_size": total_samples,
            "profit_factor": weighted_pf,
            "ic_pearson": weighted_ic,
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

