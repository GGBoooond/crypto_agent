"""V4 + harness/context integration regression tests.

Covers:
- KlineSummarizer produces compressed payload with last_n_compact + tape_signature
- PromptBuilder injects MEMORY / USER / SKILL / REGIME / TRIGGER layers
- ai_hybrid_v4 EXECUTE / REJECT / ADJUST paths still emit signals with the
  expected shape when the LLM call is mocked
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure side-effects (config loading, openai import) succeed even without
# network or real API keys.
os.environ.setdefault("OKX_API_KEY", "test")
os.environ.setdefault("OKX_SECRET_KEY", "test")
os.environ.setdefault("OKX_PASSPHRASE", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")


def _build_klines(num_bars: int = 120, seed_close: float = 0.115) -> list:
    """Build oscillating klines that trigger the OVERSOLD / OVERBOUGHT logic."""
    klines = []
    price = seed_close
    direction = 1
    for i in range(num_bars):
        # Wide swings for the last 30 bars to push RSI / BB extremes
        if i > num_bars - 30:
            direction = -1 if (i % 4 < 2) else 1
        change = direction * (0.0008 if i < num_bars - 30 else 0.002)
        close = price * (1 + change)
        high = close * 1.001
        low = close * 0.999
        open_ = price
        klines.append(
            {
                "timestamp": 1_700_000_000 + i * 60,
                "open": round(open_, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": 1000 + (i * 13 % 500),
            }
        )
        price = close
    return klines


class TestKlineSummarizer(unittest.TestCase):
    def test_summary_contains_compact_tape(self):
        from harness.context import KlineSummarizer

        klines = _build_klines()
        summary = KlineSummarizer().summarize(klines, recent_n=5)
        self.assertIn("last_n_compact", summary)
        self.assertEqual(len(summary["last_n_compact"]), 5)
        self.assertIn("tape_signature", summary)
        self.assertIn("seq=", summary["tape_signature"])
        self.assertIn("summary", summary)

    def test_estimated_tokens_proportional(self):
        from harness.context import KlineSummarizer

        small = KlineSummarizer.estimated_tokens("hi")
        large = KlineSummarizer.estimated_tokens("x" * 4000)
        self.assertLess(small, large)
        self.assertGreaterEqual(large, 800)


class TestPromptBuilder(unittest.TestCase):
    def setUp(self):
        from harness.context import PromptBuilder

        self.builder = PromptBuilder(memory_dir=str(ROOT / "memory"))
        # Clear cache so we re-read disk each test run
        self.builder.snapshot = None

    def test_layered_messages_contain_required_sections(self):
        from harness.context import KlineSummarizer

        klines = _build_klines()
        kline_summary = KlineSummarizer().summarize(klines, recent_n=5)
        messages = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
            strategy_payload={
                "mode": "open",
                "signal_dir": "LONG",
                "trigger_reason": "OVERSOLD_BOUNCE",
                "indicators": {"rsi": 28.4, "atr": 0.0012},
                "ref_tp": 0.118,
                "ref_sl": 0.114,
            },
            regime_extra="change_pct=-0.5 volatility=0.4",
        )
        self.assertEqual(len(messages), 3, "expected 3 messages for prefix caching")
        static = messages[1]["content"]
        dynamic = messages[2]["content"]
        # 静态前缀应包含记忆/技能/上下文层
        for marker in ["[USER]", "[MEMORY]", "[SKILLS_INDEX]", "[SKILLS]",
                        "[CONTEXT]", "[DECISION_SCHEMA]"]:
            self.assertIn(marker, static, f"missing static marker: {marker}")
        # 动态后缀应包含行情/指标/触发层
        for marker in ["[REGIME]", "[KLINE_SUMMARY]", "[INDICATORS]",
                        "[RECENT_TAPE]", "[TRIGGER]"]:
            self.assertIn(marker, dynamic, f"missing dynamic marker: {marker}")
        self.assertIn("OVERSOLD_BOUNCE", dynamic)
        self.assertIn("Regime: ranging", dynamic)

    def test_skill_filtering_by_regime(self):
        from harness.context import KlineSummarizer

        kline_summary = KlineSummarizer().summarize(_build_klines(), recent_n=5)
        # 静态前缀 messages[1] 含 [SKILLS]，动态后缀 messages[2] 含 [REGIME]
        static_ranging = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
        )[1]["content"]
        dynamic_ranging = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
        )[2]["content"]
        self.builder.last_regime = None
        static_up = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="strong_trend_up",
            kline_summary=kline_summary,
            position=None,
        )[1]["content"]
        dynamic_up = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="strong_trend_up",
            kline_summary=kline_summary,
            position=None,
        )[2]["content"]

        self.assertIn("Regime: ranging", dynamic_ranging)
        self.assertIn("Regime: strong_trend_up", dynamic_up)
        # 不同 regime 应选择不同的 skill，静态前缀不同
        self.assertNotEqual(static_ranging, static_up)

    def test_optional_user_instruction_and_extra_context_segments(self):
        """USER_INSTRUCTION → 静态前缀；EXTRA_CONTEXT → 动态后缀。"""
        from harness.context import KlineSummarizer

        kline_summary = KlineSummarizer().summarize(_build_klines(), recent_n=5)

        static_without = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
            strategy_payload={"mode": "open"},
        )[1]["content"]
        dynamic_without = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
            strategy_payload={"mode": "open"},
        )[2]["content"]
        self.assertNotIn("[USER_INSTRUCTION]", static_without)
        self.assertNotIn("[EXTRA_CONTEXT]", dynamic_without)

        self.builder.last_regime = None
        static_with = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
            strategy_payload={
                "mode": "open",
                "user_instruction": "高频原则: 60% 信心即开仓",
                "extra_context": {
                    "btc_trend": {"trend": "上涨", "change_24h": 1.5},
                    "support": [0.12, 0.115],
                },
            },
        )[1]["content"]
        dynamic_with = self.builder.build_messages(
            symbol="DOGE/USDT",
            regime="ranging",
            kline_summary=kline_summary,
            position=None,
            strategy_payload={
                "mode": "open",
                "user_instruction": "高频原则: 60% 信心即开仓",
                "extra_context": {
                    "btc_trend": {"trend": "上涨", "change_24h": 1.5},
                    "support": [0.12, 0.115],
                },
            },
        )[2]["content"]
        self.assertIn("[USER_INSTRUCTION]", static_with)
        self.assertIn("高频原则", static_with)
        self.assertIn("[EXTRA_CONTEXT]", dynamic_with)
        self.assertIn("btc_trend", dynamic_with)
        self.assertIn("support", dynamic_with)


class TestV4SignalExtraction(unittest.TestCase):
    """Verify EXECUTE / REJECT / ADJUST paths still produce the right signal."""

    def setUp(self):
        from strategies.ai_hybrid_v4_strategy import AIHybridV4Strategy

        self.strategy = AIHybridV4Strategy()
        self.klines = _build_klines()
        self.df = self.strategy._compute_indicators(self.klines)

    def test_extract_open_signal_execute(self):
        from core.message import SignalType

        trigger = self.strategy._build_open_payload(
            self.df,
            {
                "signal_dir": "LONG",
                "trigger": "OVERSOLD_BOUNCE",
                "atr": float(self.df.iloc[-1]["atr"]),
            },
        )
        ai_decision = {
            "action": "EXECUTE",
            "confidence": "HIGH",
            "reason": "ok",
            "tp_price": trigger["ref_tp"],
            "sl_price": trigger["ref_sl"],
        }
        signal = self.strategy._extract_open_signal(ai_decision, trigger, "DOGE/USDT")
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)
        self.assertTrue(signal.metadata.get("place_tp_sl_orders"))
        self.assertEqual(signal.metadata.get("tp_price"), trigger["ref_tp"])

    def test_extract_open_signal_reject(self):
        trigger = self.strategy._build_open_payload(
            self.df,
            {
                "signal_dir": "LONG",
                "trigger": "OVERSOLD_BOUNCE",
                "atr": float(self.df.iloc[-1]["atr"]),
            },
        )
        ai_decision = {"action": "REJECT", "confidence": "LOW", "reason": "too risky"}
        signal = self.strategy._extract_open_signal(ai_decision, trigger, "DOGE/USDT")
        self.assertIsNone(signal)

    def test_extract_position_adjust_signal(self):
        from core.message import SignalType

        position = {
            "side": "long",
            "size": 1.0,
            "entry_price": float(self.df.iloc[-1]["close"]) * 0.99,
            "tp_price": float(self.df.iloc[-1]["close"]) * 1.01,
            "sl_price": float(self.df.iloc[-1]["close"]) * 0.97,
        }
        trigger = self.strategy._build_position_payload(self.df, position)
        ai_decision = {
            "action": "ADJUST",
            "reason": "tighten stops",
            "tp_price": trigger["ref_tp"],
            "sl_price": trigger["ref_sl"],
        }
        signal = self.strategy._extract_position_signal(
            ai_decision, position, trigger, "DOGE/USDT"
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.HOLD)
        self.assertTrue(signal.metadata.get("adjust_tp_sl"))


class TestV4HarnessFlowMocked(unittest.TestCase):
    """Plug a fake LLM into BaseAIStrategy._call_llm and run analyze() once."""

    def _stub_response(self, content: str, prompt_tokens=300, completion_tokens=80):
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        return SimpleNamespace(choices=[choice], usage=usage)

    def test_analyze_with_mocked_llm_records_usage(self):
        from strategies.ai_hybrid_v4_strategy import AIHybridV4Strategy

        strategy = AIHybridV4Strategy()
        klines = _build_klines()

        async def _fake_create(*args, **kwargs):
            return self._stub_response(
                '{"action": "EXECUTE", "confidence": "MEDIUM", "reason": "ok",'
                ' "tp_price": 0.118, "sl_price": 0.114}'
            )

        # Force a trigger by injecting last bar into oversold zone
        df = strategy._compute_indicators(klines)
        last_idx = df.index[-1]
        df.loc[last_idx, "rsi"] = 25.0
        df.loc[last_idx, "lower_bb"] = float(df.loc[last_idx, "close"]) * 1.05

        with patch.object(
            strategy.client.chat.completions, "create", side_effect=_fake_create
        ):
            with patch.object(strategy, "_compute_indicators", return_value=df):
                signal = asyncio.run(
                    strategy.analyze(
                        symbol="DOGE/USDT",
                        klines=klines,
                        market_data={"close": klines[-1]["close"]},
                        position=None,
                        context=None,
                    )
                )

        self.assertIsNotNone(signal)
        self.assertIn("llm_usage", signal.metadata)
        self.assertIn("prompt_messages", signal.metadata)
        self.assertEqual(signal.metadata["llm_usage"]["total_tokens"], 380)


if __name__ == "__main__":
    unittest.main()
