"""Prompt construction for trace attribution."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .attribution_taxonomy import ATTRIBUTION_HINTS


_TRACE_KEYS = (
    "trace_id",
    "timestamp",
    "symbol",
    "regime",
    "fine_regime",
    "skill_used",
    "side",
    "qty",
    "entry_price",
    "exit_price",
    "pnl",
    "forward_return_5m",
    "forward_return_30m",
    "forward_return_4h",
    "slippage_bps",
    "funding_rate",
    "llm_prompt",
    "llm_response",
)


def build_attribution_prompt(trace: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build strict JSON attribution messages for reviewer LLM."""
    compact_trace = {key: trace.get(key) for key in _TRACE_KEYS if key in trace}
    hints = {
        category.value: hint
        for category, hint in ATTRIBUTION_HINTS.items()
    }
    schema = {
        "category": "one attribution category string",
        "confidence": "number between 0 and 1",
        "evidence": "short string that cites fields from the input trace",
    }
    return [
        {
            "role": "system",
            "content": (
                "You are a conservative trade postmortem reviewer. "
                "Return one JSON object only. Do not invent market data. "
                "The evidence field must cite concrete input fields."
            ),
        },
        {
            "role": "user",
            "content": (
                "Choose the best attribution category for this trace.\n\n"
                f"Categories:\n{json.dumps(hints, ensure_ascii=False, indent=2)}\n\n"
                f"Required schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                f"Trace:\n{json.dumps(compact_trace, ensure_ascii=False, default=str)}"
            ),
        },
    ]
