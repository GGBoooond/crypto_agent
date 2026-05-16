"""Post-trade attribution and memory/skill patch drafts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .judge import PatchJudge
from memory.skill_manage import SkillManager


_DIRECT_PATCH_THRESHOLD = 0.8
_REVIEW_THRESHOLD = 0.5
_HIGH_RETURN_THRESHOLD = 0.01


@dataclass
class PostmortemDraft:
    kind: str  # memory_add or skill_patch
    payload: Dict[str, Any]


class PostmortemEngine:
    """Create deterministic draft actions from executed traces."""

    def __init__(
        self,
        skill_manager: SkillManager | None = None,
        judge: PatchJudge | None = None,
        review_dir: str = "evolution/review_queue",
        dry_run: bool = True,
    ):
        self.skill_manager = skill_manager or SkillManager()
        self.judge = judge or PatchJudge()
        self.review_dir = Path(review_dir)
        self.dry_run = dry_run

    def analyze_traces(self, traces: List[Dict[str, Any]]) -> List[PostmortemDraft]:
        drafts: List[PostmortemDraft] = []
        for trace in traces:
            pnl = self._numeric_outcome(trace)
            skill_used = trace.get("skill_used")
            if pnl is None:
                continue
            if pnl < 0 and skill_used:
                draft = self._build_skill_patch(trace, pnl)
                drafts.append(draft)
                self._route_skill_patch(draft, trace)
            elif pnl < 0:
                drafts.append(self._build_memory_add())
            elif pnl >= _HIGH_RETURN_THRESHOLD and not skill_used:
                draft = self._build_new_skill_candidate(trace, pnl)
                drafts.append(draft)
                self._write_review_item(draft, {"score": 0.5, "reason": "new_skill_candidate"})
        return drafts

    def _build_skill_patch(
        self, trace: Dict[str, Any], outcome: float
    ) -> PostmortemDraft:
        skill_name = self._primary_skill_name(trace.get("skill_used"))
        fine_regime = trace.get("fine_regime") or "unknown fine_regime"
        pitfall = (
            f"## Pitfalls\n- {fine_regime}: outcome={outcome:.4f}, "
            f"避免在相同形态下机械复用该 skill"
        )
        return PostmortemDraft(
            kind="skill_patch",
            payload={
                "name": skill_name,
                "old_string": "## Pitfalls",
                "new_string": pitfall,
            },
        )

    @staticmethod
    def _build_memory_add() -> PostmortemDraft:
        return PostmortemDraft(
            kind="memory_add",
            payload={"content": "最近交易出现亏损聚集，需收紧仓位与触发条件"},
        )

    def _build_new_skill_candidate(
        self, trace: Dict[str, Any], outcome: float
    ) -> PostmortemDraft:
        regime = trace.get("regime") or "unknown"
        fine_regime = trace.get("fine_regime") or "unknown"
        content = (
            "---\n"
            f"name: candidate_{regime}_{fine_regime}\n"
            "description: 高收益未归因决策候选 skill\n"
            "metadata:\n"
            "  quant:\n"
            f"    regime: [{regime}]\n"
            "    sample_size: 1\n"
            "---\n\n"
            "# Candidate Skill\n\n"
            "## When to Use\n"
            f"- fine_regime: {fine_regime}\n"
            f"- observed_forward_return: {outcome:.4f}\n\n"
            "## Procedure\n"
            "- 人工审核后补充共性形态与执行步骤\n\n"
            "## Pitfalls\n"
            "- 单样本候选，禁止直接进入 ACTIVE\n"
        )
        return PostmortemDraft(kind="skill_candidate", payload={"content": content})

    def _route_skill_patch(
        self, draft: PostmortemDraft, trace: Dict[str, Any]
    ) -> None:
        payload = draft.payload
        existing_text = self._read_skill_text(str(payload.get("name", "")))
        score = self.judge.score(
            payload,
            skill_stats=self._skill_stats(trace),
            existing_text=existing_text,
        )
        if score["score"] >= _DIRECT_PATCH_THRESHOLD:
            if not self.dry_run:
                self.skill_manager.patch(
                    str(payload["name"]),
                    str(payload["old_string"]),
                    str(payload["new_string"]),
                )
            return
        if score["score"] >= _REVIEW_THRESHOLD:
            self._write_review_item(draft, score)

    def _write_review_item(
        self, draft: PostmortemDraft, score: Dict[str, Any]
    ) -> None:
        self.review_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{draft.kind}_{len(list(self.review_dir.glob('*.md'))) + 1:04d}.md"
        body = (
            f"# {draft.kind}\n\n"
            f"score: {score.get('score')}\n"
            f"reason: {score.get('reason')}\n\n"
            "```json\n"
            f"{draft.payload}\n"
            "```\n"
        )
        if not self.dry_run:
            (self.review_dir / filename).write_text(body, encoding="utf-8")

    def _read_skill_text(self, name: str) -> str:
        if not name:
            return ""
        try:
            return self.skill_manager.skill_view(name)
        except (FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _skill_stats(trace: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "profit_factor": trace.get("profit_factor"),
            "sample_size": trace.get("sample_size"),
        }

    @staticmethod
    def _numeric_outcome(trace: Dict[str, Any]) -> float | None:
        for key in ("forward_return_30m", "forward_return_5m", "pnl"):
            value = trace.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _primary_skill_name(value: Any) -> str:
        text = str(value or "").strip()
        return text.split(",", 1)[0].strip()

