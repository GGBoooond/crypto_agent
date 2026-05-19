"""Fixed postmortem attribution categories."""
from __future__ import annotations

from enum import Enum
from typing import Dict


class AttributionCategory(str, Enum):
    REGIME_MISJUDGE = "regime_misjudge"
    SKILL_PITFALL_HIT = "skill_pitfall_hit"
    MARKET_SHOCK = "market_shock"
    SLIPPAGE_EXCESS = "slippage_excess"
    FUNDING_DRAG = "funding_drag"
    LLM_HALLUCINATION = "llm_hallucination"
    POSITION_SIZING_ERROR = "position_sizing_error"
    TAKE_PROFIT_MISS = "take_profit_miss"
    RANDOM_NOISE = "random_noise"


ATTRIBUTION_HINTS: Dict[AttributionCategory, str] = {
    AttributionCategory.REGIME_MISJUDGE: "开仓时 regime/fine_regime 与后续走势明显不匹配。",
    AttributionCategory.SKILL_PITFALL_HIT: "trace 命中了 skill 已知坑点，但执行前没有拦下。",
    AttributionCategory.MARKET_SHOCK: "5 分钟收益绝对值过大，属于突发极端波动。",
    AttributionCategory.SLIPPAGE_EXCESS: "slippage_bps 明显偏高，亏损主要来自成交质量。",
    AttributionCategory.FUNDING_DRAG: "持仓跨 funding，且 funding 成本与方向相反。",
    AttributionCategory.LLM_HALLUCINATION: "LLM reason 与输入行情字段明显对不上。",
    AttributionCategory.POSITION_SIZING_ERROR: "qty 或仓位比例与账户风险不匹配。",
    AttributionCategory.TAKE_PROFIT_MISS: "盈利未及时兑现，随后回撤或止损。",
    AttributionCategory.RANDOM_NOISE: "单笔波动没有稳定、可行动的归因证据。",
}


def normalize_category(value: str) -> AttributionCategory:
    try:
        return AttributionCategory(str(value).strip())
    except ValueError:
        return AttributionCategory.RANDOM_NOISE
