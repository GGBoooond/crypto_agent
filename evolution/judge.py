"""LLM-as-judge placeholder for patch scoring."""
from typing import Dict


class PatchJudge:
    """Dual-sign scoring abstraction for skill patch drafts."""

    def score(self, patch: Dict, reviewer_a_score: float, reviewer_b_score: float) -> Dict:
        score = (reviewer_a_score + reviewer_b_score) / 2
        return {
            "score": score,
            "accepted": score >= 0.7,
            "reason": "accepted" if score >= 0.7 else "rejected_below_threshold",
            "patch": patch,
        }

