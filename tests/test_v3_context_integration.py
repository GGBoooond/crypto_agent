"""V3 (ai_hybrid) + harness/context integration regression tests.

Covers:
- ``_compute_indicators`` returns the indicators required by triggers
- ``_check_hard_trigger`` blocks duplicate-direction positions
- ``_build_trigger_payload`` injects USER_INSTRUCTION + ATR-multiplier schema
- ``analyze`` (template in BaseAIStrategy) records llm_usage + prompt_messages
- ``_extract_signal`` converts ATR multipliers into absolute SL/TP prices
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

os.environ.setdefault("OKX_API_KEY", "test")
os.environ.setdefault("OKX_SECRET_KEY", "test")
os.environ.setdefault("OKX_PASSPHRASE", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")


def _build_klines(num_bars: int = 120, seed_close: float = 0.115) -> list:
    klines = []
    price = seed_close
    direction = 1
    for i in range(num_bars):
        if i > num_bars - 30:
            direction = -1 if (i % 4 < 2) else 1
        change = direction * (0.0008 if i < num_bars - 30 else 0.002)
        close = price * (1 + change)
        klines.append({
            "timestamp": 1_700_000_000 + i * 60,
            "open": round(price, 6),
            "high": round(close * 1.001, 6),
            "low": round(close * 0.999, 6),
            "close": round(close, 6),
            "volume": 1000 + (i * 13 % 500),
        })
        price = close
    return klines


def _stub_llm_response(content: str, prompt_tokens=300, completion_tokens=80):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


class TestV3IndicatorsAndTriggers(unittest.TestCase):
    def setUp(self):
        from strategies.ai_hybrid_strategy import AIHybridStrategy
        self.strategy = AIHybridStrategy()
        self.klines = _build_klines()
        self.df = self.strategy._compute_indicators(self.klines)

    def test_indicators_present(self):
        self.assertIsNotNone(self.df)
        for col in ['rsi', 'atr', 'ma20', 'ema50', 'upper_bb', 'lower_bb', 'bb_width', 'hist']:
            self.assertIn(col, self.df.columns, f"缺少指标列: {col}")

    def test_same_direction_position_blocks_trigger(self):
        triggered, _, ctx = self.strategy._check_hard_trigger(self.df, None, None)
        if triggered:
            same_side = {"side": ctx['signal_dir'].lower(), "size": 100}
            triggered2, _, _ = self.strategy._check_hard_trigger(self.df, same_side, None)
            self.assertFalse(triggered2)


class TestV3TriggerPayload(unittest.TestCase):
    def setUp(self):
        from strategies.ai_hybrid_strategy import AIHybridStrategy
        self.strategy = AIHybridStrategy()
        self.df = self.strategy._compute_indicators(_build_klines())

    def test_payload_contains_atr_schema_and_user_instruction(self):
        payload = self.strategy._build_trigger_payload(
            df=self.df,
            trigger_ctx={"signal_dir": "LONG", "trigger": "OVERSOLD_BOUNCE"},
            position=None,
            mode="open",
            extra={},
        )
        self.assertIsNotNone(payload)
        self.assertIn("decision_schema", payload)
        self.assertIn("stop_loss_adjust", payload["decision_schema"])
        self.assertIn("take_profit_adjust", payload["decision_schema"])
        self.assertIn("user_instruction", payload)
        self.assertIn("高频交易决策逻辑", payload["user_instruction"])
        self.assertIn("indicators", payload)
        self.assertIn("rsi", payload["indicators"])
        self.assertIn("atr", payload["indicators"])

    def test_payload_returns_none_for_empty_df(self):
        import pandas as pd
        payload = self.strategy._build_trigger_payload(
            df=pd.DataFrame(),
            trigger_ctx={"signal_dir": "LONG"},
            position=None,
            mode="open",
            extra={},
        )
        self.assertIsNone(payload)


class TestV3SignalExtraction(unittest.TestCase):
    def setUp(self):
        from strategies.ai_hybrid_strategy import AIHybridStrategy
        self.strategy = AIHybridStrategy()
        self.df = self.strategy._compute_indicators(_build_klines())
        self.payload = self.strategy._build_trigger_payload(
            df=self.df,
            trigger_ctx={"signal_dir": "LONG", "trigger": "OVERSOLD_BOUNCE"},
            position=None,
            mode="open",
            extra={},
        )

    def test_execute_with_atr_multipliers(self):
        from core.message import SignalType
        ai_decision = {
            "action": "EXECUTE",
            "confidence": "HIGH",
            "reason": "顺势回踩到位",
            "stop_loss_adjust": 1.8,
            "take_profit_adjust": 1.3,
        }
        signal = self.strategy._extract_signal(
            ai_decision,
            symbol="DOGE/USDT",
            klines=[],
            market_data={},
            position=None,
            context=None,
            trigger_payload=self.payload,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)
        # 验证 ATR 系数正确转化为绝对价
        current_price = self.payload["current_price"]
        atr = self.payload["indicators"]["atr"]
        self.assertAlmostEqual(signal.stop_loss, current_price - atr * 1.8, places=5)
        self.assertAlmostEqual(signal.take_profit, current_price + atr * 1.3, places=5)
        self.assertEqual(signal.metadata["sl_mult"], 1.8)

    def test_reject_returns_none(self):
        ai_decision = {"action": "REJECT", "reason": "形态不佳"}
        signal = self.strategy._extract_signal(
            ai_decision,
            symbol="DOGE/USDT",
            klines=[],
            market_data={},
            position=None,
            context=None,
            trigger_payload=self.payload,
        )
        self.assertIsNone(signal)

    def test_missing_multipliers_use_defaults(self):
        from core.message import SignalType
        ai_decision = {"action": "EXECUTE", "confidence": "MEDIUM", "reason": "ok"}
        signal = self.strategy._extract_signal(
            ai_decision,
            symbol="DOGE/USDT",
            klines=[],
            market_data={},
            position=None,
            context=None,
            trigger_payload=self.payload,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)
        # 默认 SL=1.5, TP=1.2
        self.assertEqual(signal.metadata["sl_mult"], 1.5)
        self.assertEqual(signal.metadata["tp_mult"], 1.2)


class TestV3HarnessFlowMocked(unittest.TestCase):
    """完整 analyze() 路径：force trigger → mock LLM → 断言 prompt 段 + token 用量。"""

    def test_analyze_records_usage_and_prompt(self):
        from strategies.ai_hybrid_strategy import AIHybridStrategy
        strategy = AIHybridStrategy()

        klines = _build_klines()
        df = strategy._compute_indicators(klines)
        last_idx = df.index[-1]
        df.loc[last_idx, "rsi"] = 25.0
        df.loc[last_idx, "lower_bb"] = float(df.loc[last_idx, "close"]) * 1.05

        async def _fake_create(*args, **kwargs):
            return _stub_llm_response(
                '{"action": "EXECUTE", "confidence": "MEDIUM", "reason": "ok",'
                ' "stop_loss_adjust": 1.5, "take_profit_adjust": 1.2}'
            )

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
        self.assertEqual(signal.metadata["llm_usage"]["total_tokens"], 380)

        prompt_messages = signal.metadata.get("prompt_messages")
        self.assertIsNotNone(prompt_messages)
        self.assertEqual(len(prompt_messages), 3)
        # 静态前缀 messages[1] → [USER_INSTRUCTION], [DECISION_SCHEMA], [CONTEXT]
        static = prompt_messages[1]["content"]
        for marker in ["[USER_INSTRUCTION]", "[DECISION_SCHEMA]"]:
            self.assertIn(marker, static, f"missing marker in static: {marker}")
        self.assertIn("高频交易决策逻辑", static)
        self.assertIn("stop_loss_adjust", static)
        # 动态后缀 messages[2] → [REGIME], [TRIGGER]
        dynamic = prompt_messages[2]["content"]
        for marker in ["[REGIME]", "[TRIGGER]"]:
            self.assertIn(marker, dynamic, f"missing marker in dynamic: {marker}")


if __name__ == "__main__":
    unittest.main()
