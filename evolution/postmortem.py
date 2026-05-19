"""Post-trade attribution and memory/skill patch drafts."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from harness.cost.reviewer_client import ReviewerClient
from memory.skill_manage import SkillManager

from .attribution import SkillAttributionJob
from .attribution_taxonomy import AttributionCategory, normalize_category
from .judge import PatchJudge
from .postmortem_prompt import build_attribution_prompt
from .skill_lifecycle import SkillLifecycleManager


_DIRECT_PATCH_THRESHOLD = 0.8
_REVIEW_THRESHOLD = 0.5
_SIGNIFICANT_RETURN_THRESHOLD = 0.01
_HIGH_RETURN_THRESHOLD = 0.02
_MIN_DRAFT_CLUSTER_SIZE = 30


@dataclass
class PostmortemDraft:
    kind: str
    payload: Dict[str, Any]


class PostmortemEngine:
    """Classify executed traces and route drafts for review."""

    def __init__(
        self,
        skill_manager: Optional[SkillManager] = None,
        judge: Optional[PatchJudge] = None,
        reviewer_client: Optional[ReviewerClient] = None,
        lifecycle_manager: Optional[SkillLifecycleManager] = None,
        review_dir: str = "evolution/review_queue",
        drafts_dir: str = "evolution/drafts",
        shocks_path: str = "evolution/shocks.log",
        dry_run: bool = True,
        db_path: str = "memory/trades.db",
    ):
        self.skill_manager = skill_manager or SkillManager()
        self.judge = judge or PatchJudge()
        self.reviewer_client = reviewer_client
        self.lifecycle_manager = lifecycle_manager or SkillLifecycleManager()
        self.review_dir = Path(review_dir)
        self.drafts_dir = Path(drafts_dir)
        self.shocks_path = Path(shocks_path)
        self.dry_run = dry_run
        self.db_path = db_path

    def analyze_traces(self, traces: List[Dict[str, Any]]) -> List[PostmortemDraft]:
        drafts: List[PostmortemDraft] = []
        significant_traces = self._significant_traces(traces)
        for trace in significant_traces:
            label = self._classify_trace(trace)
            draft = self._route_label(trace, label)
            if draft is not None:
                drafts.append(draft)
        drafts.extend(self._build_clustered_skill_candidates(traces))
        return drafts

    def _route_label(
        self,
        trace: Dict[str, Any],
        label: Dict[str, Any],
    ) -> Optional[PostmortemDraft]:
        category = normalize_category(str(label.get("category") or ""))
        self._mark_trace_category(trace, label)
        if category == AttributionCategory.REGIME_MISJUDGE:
            draft = self._build_memory_add(trace, label)
            self._write_draft_file(draft, prefix="memory")
            return draft
        if category == AttributionCategory.SKILL_PITFALL_HIT and trace.get("skill_used"):
            draft = self._build_skill_patch(trace, label)
            self._write_draft_file(draft, prefix="skill_patch")
            self._route_skill_patch(draft, trace)
            return draft
        if category == AttributionCategory.MARKET_SHOCK:
            self._append_market_shock(trace, label)
            return PostmortemDraft(kind="market_shock", payload=label)
        if self._is_profitable_skill_trace(trace):
            draft = self._build_confidence_boost(trace, label)
            self._write_draft_file(draft, prefix="skill_boost")
            return draft
        draft = PostmortemDraft(
            kind="trace_attribution",
            payload={
                "trace_id": trace.get("trace_id"),
                "category": category.value,
                "confidence": label.get("confidence", 0.0),
                "evidence": label.get("evidence", ""),
            },
        )
        self._write_draft_file(draft, prefix="trace")
        return draft

    def _classify_trace(self, trace: Dict[str, Any]) -> Dict[str, Any]:
        if self.reviewer_client is not None and self.reviewer_client.is_configured:
            try:
                payload = self.reviewer_client.chat_json_sync(build_attribution_prompt(trace))
                return self._clean_label(payload)
            except (RuntimeError, ValueError):
                pass
        return self._deterministic_label(trace)

    def _deterministic_label(self, trace: Dict[str, Any]) -> Dict[str, Any]:
        forward_5m = abs(self._safe_float(trace.get("forward_return_5m")) or 0.0)
        slippage = abs(self._safe_float(trace.get("slippage_bps")) or 0.0)
        funding = self._safe_float(trace.get("funding_rate")) or 0.0
        pnl = self._numeric_outcome(trace) or 0.0
        if forward_5m > 0.05:
            category = AttributionCategory.MARKET_SHOCK
            evidence = "forward_return_5m shows extreme move"
        elif slippage > 15:
            category = AttributionCategory.SLIPPAGE_EXCESS
            evidence = "slippage_bps is above threshold"
        elif pnl < 0 and funding != 0:
            category = AttributionCategory.FUNDING_DRAG
            evidence = "negative outcome with non-zero funding_rate"
        elif pnl < 0 and trace.get("skill_used"):
            category = AttributionCategory.SKILL_PITFALL_HIT
            evidence = "negative outcome after skill_used"
        elif pnl < 0:
            category = AttributionCategory.REGIME_MISJUDGE
            evidence = "negative outcome without a skill attribution"
        else:
            category = AttributionCategory.RANDOM_NOISE
            evidence = "profitable or weakly significant trace"
        return {
            "category": category.value,
            "confidence": 0.7,
            "evidence": evidence,
        }

    def _clean_label(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        category = normalize_category(str(payload.get("category") or ""))
        try:
            confidence = max(0.0, min(float(payload.get("confidence", 0.0)), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "category": category.value,
            "confidence": confidence,
            "evidence": str(payload.get("evidence") or ""),
        }

    def _build_skill_patch(
        self,
        trace: Dict[str, Any],
        label: Dict[str, Any],
    ) -> PostmortemDraft:
        skill_name = self._primary_skill_name(trace.get("skill_used"))
        fine_regime = trace.get("fine_regime") or "unknown fine_regime"
        outcome = self._numeric_outcome(trace) or 0.0
        pitfall = (
            "## Pitfalls\n"
            f"- {fine_regime}: outcome={outcome:.4f}; "
            f"evidence={label.get('evidence', '')}"
        )
        return PostmortemDraft(
            kind="skill_patch",
            payload={
                "name": skill_name,
                "old_string": "## Pitfalls",
                "new_string": pitfall,
                "trace_id": trace.get("trace_id"),
                "category": label.get("category"),
            },
        )

    @staticmethod
    def _build_memory_add(
        trace: Dict[str, Any],
        label: Dict[str, Any],
    ) -> PostmortemDraft:
        content = (
            f"- {datetime.now(timezone.utc).date().isoformat()} "
            f"{trace.get('symbol', 'unknown')}: regime={trace.get('regime')}, "
            f"fine_regime={trace.get('fine_regime')}; evidence={label.get('evidence')}"
        )
        return PostmortemDraft(kind="memory_add", payload={"content": content})

    @staticmethod
    def _build_confidence_boost(
        trace: Dict[str, Any],
        label: Dict[str, Any],
    ) -> PostmortemDraft:
        return PostmortemDraft(
            kind="confidence_boost",
            payload={
                "trace_id": trace.get("trace_id"),
                "skill_used": trace.get("skill_used"),
                "fine_regime": trace.get("fine_regime"),
                "category": label.get("category"),
                "evidence": label.get("evidence"),
            },
        )

    def _build_clustered_skill_candidates(
        self,
        traces: Iterable[Dict[str, Any]],
    ) -> List[PostmortemDraft]:
        clusters: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for trace in traces:
            if trace.get("skill_used"):
                continue
            outcome = self._numeric_outcome(trace)
            if outcome is None or outcome < _HIGH_RETURN_THRESHOLD:
                continue
            key = (
                str(trace.get("fine_regime") or "unknown"),
                str(trace.get("trigger_reason") or trace.get("regime") or "unknown"),
            )
            clusters[key].append(trace)
        drafts: List[PostmortemDraft] = []
        for (fine_regime, trigger_reason), rows in clusters.items():
            if not self._passes_skill_draft_gate(rows):
                continue
            draft = self._build_new_skill_candidate(fine_regime, trigger_reason, rows)
            drafts.append(draft)
            if not self.dry_run:
                self.skill_manager.create_draft(draft.payload["name"], draft.payload["content"])
        return drafts

    def _build_new_skill_candidate(
        self,
        fine_regime: str,
        trigger_reason: str,
        rows: List[Dict[str, Any]],
    ) -> PostmortemDraft:
        regime = str(rows[0].get("regime") or "unknown")
        name = self._safe_name(f"candidate_{regime}_{fine_regime}")
        avg_return = sum(self._numeric_outcome(row) or 0.0 for row in rows) / len(rows)
        content = (
            "---\n"
            f"name: {name}\n"
            "description: 高收益未归因决策候选 skill\n"
            "metadata:\n"
            "  quant:\n"
            f"    regime: [{regime}]\n"
            f"    sample_size: {len(rows)}\n"
            "---\n\n"
            f"# {name}\n\n"
            "## When to Use\n"
            f"- fine_regime: {fine_regime}\n"
            f"- trigger_reason: {trigger_reason}\n"
            f"- observed_avg_forward_return: {avg_return:.4f}\n\n"
            "## Procedure\n"
            "- 人工审核后补充共性形态与执行步骤\n\n"
            "## Pitfalls\n"
            "- 候选 skill 默认留在 .draft，禁止直接进入 prompt。\n"
        )
        return PostmortemDraft(
            kind="skill_candidate",
            payload={"name": name, "content": content, "sample_size": len(rows)},
        )

    def _passes_skill_draft_gate(self, rows: List[Dict[str, Any]]) -> bool:
        if len(rows) < _MIN_DRAFT_CLUSTER_SIZE:
            return False
        outcomes = [self._numeric_outcome(row) or 0.0 for row in rows]
        split_index = max(int(len(outcomes) * 0.7), 1)
        oos_returns = outcomes[split_index:] or outcomes
        return self._profit_factor(oos_returns) > 1.3 and self._one_sided_p_value(outcomes) < 0.05

    @staticmethod
    def _profit_factor(values: List[float]) -> float:
        positives = [value for value in values if value > 0]
        negatives = [value for value in values if value < 0]
        if positives and not negatives:
            return 999.0
        if negatives and not positives:
            return 0.0
        if not positives and not negatives:
            return 0.0
        return sum(positives) / abs(sum(negatives))

    @staticmethod
    def _one_sided_p_value(values: List[float]) -> float:
        if len(values) < 2:
            return 1.0
        mean_value = sum(values) / len(values)
        variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return 0.0 if mean_value > 0 else 1.0
        z_score = mean_value / (std_dev / math.sqrt(len(values)))
        return 0.5 * math.erfc(z_score / math.sqrt(2))

    def _route_skill_patch(
        self,
        draft: PostmortemDraft,
        trace: Dict[str, Any],
    ) -> None:
        payload = draft.payload
        skill_name = str(payload.get("name", ""))
        existing_text = self._read_skill_text(skill_name)
        stats = self.judge.load_skill_stats(skill_name, self.db_path) or self._skill_stats(trace)
        score = self.judge.score(
            payload,
            skill_stats=stats,
            existing_text=existing_text,
            trace=trace,
        )
        if score["score"] >= _DIRECT_PATCH_THRESHOLD and score.get("accepted"):
            if not self.dry_run:
                self.skill_manager.patch_with_audit(
                    skill_name,
                    str(payload["old_string"]),
                    str(payload["new_string"]),
                    audit={**score, "trace_id": trace.get("trace_id")},
                )
                self.lifecycle_manager.on_patch(skill_name)
            return
        if score["score"] >= _REVIEW_THRESHOLD:
            self._write_review_item(draft, score)

    def _write_draft_file(self, draft: PostmortemDraft, prefix: str) -> None:
        if self.dry_run:
            return
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = self.drafts_dir / f"{prefix}_{timestamp}.md"
        path.write_text(self._draft_body(draft), encoding="utf-8")

    def _write_review_item(
        self,
        draft: PostmortemDraft,
        score: Dict[str, Any],
    ) -> None:
        if self.dry_run:
            return
        self.review_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{draft.kind}_{len(list(self.review_dir.glob('*.md'))) + 1:04d}.md"
        payload = {"score": score, "draft": draft.payload}
        body = f"# {draft.kind}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n"
        (self.review_dir / filename).write_text(body, encoding="utf-8")

    def _append_market_shock(
        self,
        trace: Dict[str, Any],
        label: Dict[str, Any],
    ) -> None:
        if self.dry_run:
            return
        self.shocks_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace.get("trace_id"),
            "symbol": trace.get("symbol"),
            "evidence": label.get("evidence"),
        }
        with self.shocks_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _mark_trace_category(
        self,
        trace: Dict[str, Any],
        label: Dict[str, Any],
    ) -> None:
        trace_id = trace.get("trace_id")
        path = Path(self.db_path)
        if self.dry_run or not trace_id or not path.exists():
            return
        try:
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    UPDATE traces
                    SET stage = ?
                    WHERE trace_id = ?
                    """,
                    (f"postmortem_{label.get('category', 'unknown')}", trace_id),
                )
        except sqlite3.Error:
            return

    def _significant_traces(self, traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        outcomes = [abs(self._numeric_outcome(trace) or 0.0) for trace in traces]
        avg_abs = sum(outcomes) / len(outcomes) if outcomes else 0.0
        selected = []
        for trace in traces:
            outcome = self._numeric_outcome(trace)
            if outcome is None:
                continue
            if abs(outcome) > _SIGNIFICANT_RETURN_THRESHOLD:
                selected.append(trace)
            elif avg_abs > 0 and abs(outcome) > avg_abs * 1.5:
                selected.append(trace)
            elif self._is_profitable_skill_trace(trace):
                selected.append(trace)
        return selected

    def _read_skill_text(self, name: str) -> str:
        if not name:
            return ""
        try:
            return self.skill_manager.skill_view(name)
        except (FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _draft_body(draft: PostmortemDraft) -> str:
        return (
            f"# {draft.kind}\n\n"
            "```json\n"
            f"{json.dumps(draft.payload, ensure_ascii=False, indent=2)}\n"
            "```\n"
        )

    @staticmethod
    def _skill_stats(trace: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "profit_factor": trace.get("profit_factor"),
            "sample_size": trace.get("sample_size"),
        }

    @staticmethod
    def _numeric_outcome(trace: Dict[str, Any]) -> Optional[float]:
        for key in ("forward_return_30m", "forward_return_5m", "pnl"):
            value = trace.get(key)
            if value is None:
                continue
            parsed = PostmortemEngine._safe_float(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _is_profitable_skill_trace(trace: Dict[str, Any]) -> bool:
        outcome = PostmortemEngine._numeric_outcome(trace)
        return bool(trace.get("skill_used")) and outcome is not None and outcome > _HIGH_RETURN_THRESHOLD

    @staticmethod
    def _primary_skill_name(value: Any) -> str:
        skills = SkillAttributionJob._skill_ids(value)
        return skills[0] if skills else ""

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
        return cleaned or "candidate_skill"

