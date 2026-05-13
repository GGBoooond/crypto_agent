"""AI Scalping (PromptOnlyAIStrategy) integration regression tests.

Covers:
- ``REQUIRES_HARD_TRIGGER=False`` so each analyse cycle reaches the LLM
- ``_build_trigger_payload`` injects DECISION_SCHEMA + USER_INSTRUCTION
- ``_extract_signal`` maps BUY / SELL / HOLD / CLOSE correctly via the
  PromptOnlyAIStrategy._extract_signal_default helper
- ``analyze`` records llm_usage + prompt_messages with the expected layers
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


def _build_klines(num_bars: int = 80) -> list:
    klines = []
    price = 0.115
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


class TestScalpingTriggerPayload(unittest.TestCase):
    def setUp(self):
        from strategies.ai_scalping_strategy import AIScalpingStrategy
        self.strategy = AIScalpingStrategy()

    def test_payload_contains_schema_and_instruction(self):
        df = self.strategy._compute_indicators(_build_klines())
        payload = self.strategy._build_trigger_payload(
            df=df,
            trigger_ctx={},
            position=None,
            mode="open",
            extra={},
        )
        self.assertIsNotNone(payload)
        self.assertIn("decision_schema", payload)
        self.assertIn("BUY | SELL | HOLD | CLOSE", payload["decision_schema"])
        self.assertIn("user_instruction", payload)
        self.assertIn("【决策逻辑", payload["user_instruction"])
        self.assertIn("indicators", payload)


class TestScalpingSignalExtraction(unittest.TestCase):
    def setUp(self):
        from strategies.ai_scalping_strategy import AIScalpingStrategy
        self.strategy = AIScalpingStrategy()

    def test_buy_action_maps_to_buy_signal(self):
        from core.message import SignalType
        signal = self.strategy._extract_signal(
            {
                "action": "BUY",
                "confidence": "HIGH",
                "reason": "超卖反弹",
                "entry_price": 0.115,
                "stop_loss": 0.114,
                "take_profit": 0.117,
            },
            symbol="DOGE/USDT",
            klines=_build_klines(),
            market_data={},
            position=None,
            context=None,
            trigger_payload=None,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)
        self.assertEqual(signal.stop_loss, 0.114)
        self.assertEqual(signal.take_profit, 0.117)

    def test_sell_action_maps_to_sell_signal(self):
        from core.message import SignalType
        signal = self.strategy._extract_signal(
            {"action": "SELL", "confidence": "MEDIUM", "reason": "超买回调",
             "stop_loss": 0.116, "take_profit": 0.114},
            symbol="DOGE/USDT",
            klines=_build_klines(),
            market_data={},
            position=None,
            context=None,
            trigger_payload=None,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.SELL)

    def test_hold_returns_none(self):
        signal = self.strategy._extract_signal(
            {"action": "HOLD", "confidence": "LOW", "reason": "横盘"},
            symbol="DOGE/USDT",
            klines=_build_klines(),
            market_data={},
            position=None,
            context=None,
            trigger_payload=None,
        )
        self.assertIsNone(signal)

    def test_close_long_position_maps_to_close_long(self):
        from core.message import SignalType
        position = {"side": "long", "size": 100, "entry_price": 0.110}
        signal = self.strategy._extract_signal(
            {"action": "CLOSE", "confidence": "HIGH", "reason": "止盈"},
            symbol="DOGE/USDT",
            klines=_build_klines(),
            market_data={},
            position=position,
            context=None,
            trigger_payload=None,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.CLOSE_LONG)

    def test_close_short_position_maps_to_close_short(self):
        from core.message import SignalType
        position = {"side": "short", "size": 100, "entry_price": 0.120}
        signal = self.strategy._extract_signal(
            {"action": "CLOSE", "confidence": "HIGH", "reason": "止损"},
            symbol="DOGE/USDT",
            klines=_build_klines(),
            market_data={},
            position=position,
            context=None,
            trigger_payload=None,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.CLOSE_SHORT)


class TestScalpingHarnessFlowMocked(unittest.TestCase):
    """REQUIRES_HARD_TRIGGER=False — analyze 必然进入 LLM。"""

    def test_analyze_reaches_llm_and_records_usage(self):
        from strategies.ai_scalping_strategy import AIScalpingStrategy
        strategy = AIScalpingStrategy()
        klines = _build_klines()

        async def _fake_create(*args, **kwargs):
            return _stub_llm_response(
                '{"action": "BUY", "confidence": "HIGH", "reason": "超卖反弹",'
                ' "entry_price": 0.115, "stop_loss": 0.114, "take_profit": 0.118}'
            )

        with patch.object(
            strategy.client.chat.completions, "create", side_effect=_fake_create
        ):
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
        self.assertEqual(signal.metadata["llm_usage"]["total_tokens"], 260)

        prompt_messages = signal.metadata.get("prompt_messages")
        self.assertIsNotNone(prompt_messages)
        self.assertEqual(len(prompt_messages), 3)
        # 静态前缀 messages[1] → [USER_INSTRUCTION], [DECISION_SCHEMA]
        static = prompt_messages[1]["content"]
        self.assertIn("[USER_INSTRUCTION]", static)
        self.assertIn("[DECISION_SCHEMA]", static)
        # 动态后缀 messages[2] → [REGIME], [TRIGGER] 等
        dynamic = prompt_messages[2]["content"]
        self.assertIn("[REGIME]", dynamic)
        self.assertIn("剥头皮", prompt_messages[0]["content"])  # SYSTEM_ROLE_OVERRIDE


if __name__ == "__main__":
    unittest.main()
