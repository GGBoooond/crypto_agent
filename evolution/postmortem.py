"""Post-trade attribution and memory/skill patch drafts."""
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PostmortemDraft:
    kind: str  # memory_add or skill_patch
    payload: Dict[str, Any]


class PostmortemEngine:
    """Create deterministic draft actions from executed traces."""

    def analyze_traces(self, traces: List[Dict[str, Any]]) -> List[PostmortemDraft]:
        drafts: List[PostmortemDraft] = []
        for trace in traces:
            pnl = trace.get("pnl")
            skill_used = trace.get("skill_used")
            if pnl is None:
                continue
            if pnl < 0 and skill_used:
                drafts.append(
                    PostmortemDraft(
                        kind="skill_patch",
                        payload={
                            "name": skill_used,
                            "old_string": "## Pitfalls",
                            "new_string": "## Pitfalls\n- 新增: 连续亏损场景自动降级为 paper 模式",
                        },
                    )
                )
            elif pnl < 0:
                drafts.append(
                    PostmortemDraft(
                        kind="memory_add",
                        payload={"content": "最近交易出现亏损聚集，需收紧仓位与触发条件"},
                    )
                )
        return drafts

