"""Prompt builder with frozen snapshot layers."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class FrozenPromptSnapshot:
    built_at: str
    static_prompt: str
    user_memory: str
    system_memory: str
    skills_index: str


class PromptBuilder:
    """Build static/dynamic prompt layers for strategy LLM calls."""

    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = Path(memory_dir)
        self.snapshot: Optional[FrozenPromptSnapshot] = None
        self.last_regime: Optional[str] = None
        self.regime_layer: str = ""

    def _read_text(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def rebuild_static_snapshot(self) -> FrozenPromptSnapshot:
        mem = self._read_text(self.memory_dir / "MEMORY.md")
        usr = self._read_text(self.memory_dir / "USER.md")
        skills = sorted((self.memory_dir / "skills").glob("*/SKILL.md")) if (self.memory_dir / "skills").exists() else []
        skills_index = "\n".join(f"- {p.parent.name}" for p in skills)
        self.snapshot = FrozenPromptSnapshot(
            built_at=datetime.utcnow().isoformat(),
            static_prompt="You are a conservative quantitative trading assistant.",
            user_memory=usr,
            system_memory=mem,
            skills_index=skills_index,
        )
        return self.snapshot

    def update_regime_layer(self, regime: str, extra: Optional[str] = None) -> None:
        if regime != self.last_regime:
            self.last_regime = regime
            self.regime_layer = f"Regime: {regime}"
            if extra:
                self.regime_layer += f"\n{extra}"

    def build(
        self,
        symbol: str,
        regime: str,
        kline_summary: Dict[str, Any],
        position: Optional[Dict[str, Any]],
    ) -> str:
        if self.snapshot is None:
            self.rebuild_static_snapshot()
        self.update_regime_layer(regime)
        dynamic = (
            f"Symbol: {symbol}\n"
            f"KlineSummary: {kline_summary.get('summary', 'n/a')}\n"
            f"Position: {position}\n"
            "Return structured JSON only."
        )
        return (
            f"{self.snapshot.static_prompt}\n\n"
            f"[USER]\n{self.snapshot.user_memory}\n\n"
            f"[MEMORY]\n{self.snapshot.system_memory}\n\n"
            f"[SKILLS]\n{self.snapshot.skills_index}\n\n"
            f"[REGIME]\n{self.regime_layer}\n\n"
            f"[DYNAMIC]\n{dynamic}\n"
        )

