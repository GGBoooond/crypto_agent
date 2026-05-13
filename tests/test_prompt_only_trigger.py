"""PromptOnlyAIStrategy trigger-mode regression tests.

Covers the three documented usage modes:

* Mode A — REQUIRES_HARD_TRIGGER=False: every analyse cycle reaches LLM
* Mode B — REQUIRES_HARD_TRIGGER=True + TRIGGER_RULES=[…]: first matched rule
            calls LLM; all-miss returns None; triggers are independently testable
* Mode B edge — REQUIRES_HARD_TRIGGER=True with TRIGGER_RULES=[]: returns None
            (no silent passthrough) and warns

Additionally validates that TRIGGER_RULES are module-level callables that work
without ``self`` so they can be unit-tested in isolation.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OKX_API_KEY", "test")
os.environ.setdefault("OKX_SECRET_KEY", "test")
os.environ.setdefault("OKX_PASSPHRASE", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")


def _build_klines(num_bars: int = 80, seed_close: float = 0.115) -> List[Dict[str, Any]]:
    klines = []
    price = seed_close
    for i in range(num_bars):
        change = 0.0008 * (1 if i % 2 == 0 else -1)
        close = price * (1 + change)
        klines.append({
            "timestamp": 1_700_000_000 + i * 60,
            "open": round(price, 6),
            "high": round(close * 1.001, 6),
            "low": round(close * 0.999, 6),
            "close": round(close, 6),
            "volume": 1000 + (i * 7 % 400),
        })
        price = close
    return klines


def _stub_llm_response(content: str, prompt_tokens=200, completion_tokens=60):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


# ============================================================
# Module-level rule functions (tested independently below)
# ============================================================

def _rule_oversold_bounce(
    df: pd.DataFrame,
    position: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """RSI < 30 + price < lower_bb → 反弹做多。"""
    curr = df.iloc[-1]
    if curr['rsi'] < 30 and curr['close'] < curr['lower_bb']:
        return ("LONG", "OVERSOLD_BOUNCE", {"rsi": float(curr['rsi'])})
    return None


def _rule_overbought_dump(
    df: pd.DataFrame,
    position: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """RSI > 70 + price > upper_bb → 回落做空。"""
    curr = df.iloc[-1]
    if curr['rsi'] > 70 and curr['close'] > curr['upper_bb']:
        return ("SHORT", "OVERBOUGHT_DUMP", {"rsi": float(curr['rsi'])})
    return None


# ============================================================
# Concrete test strategies — three modes
# ============================================================

def _make_test_strategy(
    *,
    requires_trigger: bool,
    trigger_rules: List = None,
    name: str = "TestPromptOnlyStrategy",
):
    """Factory that builds a concrete PromptOnlyAIStrategy subclass on the fly."""
    from strategies.prompt_only_ai_strategy import PromptOnlyAIStrategy

    class _TestStrategy(PromptOnlyAIStrategy):
        MIN_KLINES = 50
        REQUIRES_HARD_TRIGGER = requires_trigger
        TRIGGER_RULES = trigger_rules or []
        SYSTEM_ROLE_OVERRIDE = "test role"
        DECISION_SCHEMA = '{"action": "BUY|HOLD"}'
        USER_INSTRUCTION = "test instruction"

        def _compute_indicators(self, klines):
            df = pd.DataFrame(klines)
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ma20'] + (df['std'] * 2)
            df['lower_bb'] = df['ma20'] - (df['std'] * 2)
            return df

        def _extract_signal(self, ai_decision, *, symbol, **kwargs):
            return self._extract_signal_default(ai_decision, symbol=symbol, **kwargs)

    return _TestStrategy(name=name)


# ============================================================
# Tests
# ============================================================

class TestRuleFunctionsIndependentlyCallable(unittest.TestCase):
    """规则函数是 module-level 函数，可不依赖 self 单测。"""

    def test_oversold_bounce_hits(self):
        df = pd.DataFrame({
            'rsi': [50, 50, 25],
            'close': [0.115, 0.114, 0.110],
            'lower_bb': [0.114, 0.113, 0.115],
        })
        result = _rule_oversold_bounce(df, None)
        self.assertEqual(result[0], "LONG")
        self.assertEqual(result[1], "OVERSOLD_BOUNCE")
        self.assertAlmostEqual(result[2]['rsi'], 25.0)

    def test_oversold_bounce_misses(self):
        df = pd.DataFrame({
            'rsi': [50, 50, 50],
            'close': [0.115, 0.114, 0.115],
            'lower_bb': [0.114, 0.113, 0.110],
        })
        self.assertIsNone(_rule_oversold_bounce(df, None))

    def test_overbought_dump_hits(self):
        df = pd.DataFrame({
            'rsi': [50, 50, 75],
            'close': [0.115, 0.116, 0.120],
            'upper_bb': [0.117, 0.118, 0.118],
        })
        result = _rule_overbought_dump(df, None)
        self.assertEqual(result[0], "SHORT")


class TestModeAZeroTrigger(unittest.TestCase):
    """模式 A：REQUIRES_HARD_TRIGGER=False，每次都进入 LLM。"""

    def test_analyze_always_calls_llm(self):
        strategy = _make_test_strategy(requires_trigger=False)
        klines = _build_klines()

        async def _fake_create(*args, **kwargs):
            return _stub_llm_response(
                '{"action": "BUY", "confidence": "HIGH", "reason": "test",'
                ' "stop_loss": 0.114, "take_profit": 0.117}'
            )

        with patch.object(strategy.client.chat.completions, "create", side_effect=_fake_create):
            signal = asyncio.run(strategy.analyze(
                symbol="DOGE/USDT",
                klines=klines,
                market_data={"close": klines[-1]["close"]},
                position=None,
                context=None,
            ))

        from core.message import SignalType
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)
        self.assertIn("llm_usage", signal.metadata)


class TestModeBDeclarativeTriggers(unittest.TestCase):
    """模式 B：声明式 TRIGGER_RULES。"""

    def test_first_rule_matches_calls_llm(self):
        """让 OVERSOLD_BOUNCE 命中：构造 RSI 极低 + 价格跌破 lower_bb。"""
        strategy = _make_test_strategy(
            requires_trigger=True,
            trigger_rules=[_rule_oversold_bounce, _rule_overbought_dump],
        )
        klines = _build_klines()
        df = strategy._compute_indicators(klines)
        last_idx = df.index[-1]
        df.loc[last_idx, "rsi"] = 25.0
        df.loc[last_idx, "lower_bb"] = float(df.loc[last_idx, "close"]) * 1.05

        async def _fake_create(*args, **kwargs):
            return _stub_llm_response(
                '{"action": "BUY", "confidence": "HIGH", "reason": "ok"}'
            )

        with patch.object(strategy.client.chat.completions, "create", side_effect=_fake_create):
            with patch.object(strategy, "_compute_indicators", return_value=df):
                signal = asyncio.run(strategy.analyze(
                    symbol="DOGE/USDT",
                    klines=klines,
                    market_data={"close": klines[-1]["close"]},
                    position=None,
                    context=None,
                ))

        from core.message import SignalType
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)

    def test_no_rule_matches_returns_none_without_calling_llm(self):
        strategy = _make_test_strategy(
            requires_trigger=True,
            trigger_rules=[_rule_oversold_bounce, _rule_overbought_dump],
        )
        klines = _build_klines()

        async def _fail_llm(*args, **kwargs):
            raise AssertionError("LLM should not be called when no rule matches")

        with patch.object(strategy.client.chat.completions, "create", side_effect=_fail_llm):
            signal = asyncio.run(strategy.analyze(
                symbol="DOGE/USDT",
                klines=klines,
                market_data={"close": klines[-1]["close"]},
                position=None,
                context=None,
            ))

        self.assertIsNone(signal)

    def test_empty_trigger_rules_returns_none(self):
        """REQUIRES_HARD_TRIGGER=True 但 TRIGGER_RULES=[] 必须返回 None（避免静默放行）。"""
        strategy = _make_test_strategy(requires_trigger=True, trigger_rules=[])
        klines = _build_klines()

        async def _fail_llm(*args, **kwargs):
            raise AssertionError("LLM should not be called when no trigger rules")

        with patch.object(strategy.client.chat.completions, "create", side_effect=_fail_llm):
            signal = asyncio.run(strategy.analyze(
                symbol="DOGE/USDT",
                klines=klines,
                market_data={"close": klines[-1]["close"]},
                position=None,
                context=None,
            ))

        self.assertIsNone(signal)


class TestPromptOnlyPayload(unittest.TestCase):
    """验证 _build_trigger_payload 装配 indicators / decision_schema / user_instruction。"""

    def test_payload_includes_required_fields(self):
        strategy = _make_test_strategy(requires_trigger=False)
        df = strategy._compute_indicators(_build_klines())
        payload = strategy._build_trigger_payload(
            df=df,
            trigger_ctx={},
            position=None,
            mode="open",
            extra={},
        )
        self.assertEqual(payload["mode"], "open")
        self.assertEqual(payload["decision_schema"], '{"action": "BUY|HOLD"}')
        self.assertEqual(payload["user_instruction"], "test instruction")
        self.assertIn("indicators", payload)
        self.assertIn("rsi", payload["indicators"])


if __name__ == "__main__":
    unittest.main()
