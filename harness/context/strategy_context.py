"""Container for context engineering artefacts handed to strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .prompt_builder import PromptBuilder


@dataclass
class StrategyContext:
    """Bundle of context engineering artefacts for a single analyse() call.

    Strategies that opt into harness context can pull regime, kline summary
    and a shared PromptBuilder instance from this container instead of
    re-constructing them locally. Older strategies ignore the parameter.
    """

    regime: str = "ranging"
    regime_extra: Optional[str] = None
    kline_summary: Dict[str, Any] = field(default_factory=dict)
    prompt_builder: Optional["PromptBuilder"] = None
    trace_id: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)
