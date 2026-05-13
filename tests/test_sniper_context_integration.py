"""AI Trend Sniper + harness/context integration regression tests.

Covers:
- ``_on_position_pre_llm``: trailing stop short-circuits BEFORE the LLM call
- ``_collect_extra_payload``: BTC trend + S/R levels stuffed into extra_context
- ``analyze`` injects [EXTRA_CONTEXT] into the prompt; ``_extract_signal``
  enforces the 2.5%+ profit floor; the trigger_payload carries absolute prices
- ``_check_hard_trigger`` rejects same-direction positions
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OKX_API_KEY", "test")
os.environ.setdefault("OKX_SECRET_KEY", "test")
os.environ.setdefault("OKX_PASSPHRASE", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")


def _build_uptrend_klines(num_bars: int = 300, seed: int = 42) -> list:
    np.random.seed(seed)
    klines = []
    price = 0.15000
    base_volume = 50_000_000.0

    for i in range(num_bars):
        if i < 150:
            drift, volatility = 0.0002, 0.003
            vol_mult = np.random.uniform(0.6, 1.2)
        elif i < 270:
            drift, volatility = 0.0008, 0.005
            vol_mult = np.random.uniform(1.0, 2.5)
        else:
            drift, volatility = -0.0004, 0.004
            vol_mult = np.random.uniform(0.4, 0.9)

        change = drift + np.random.normal(0, volatility)
        open_price = price
        close_price = price * (1 + change)

        intra_vol = abs(change) + np.random.exponential(volatility * 0.5)
        if close_price > open_price:
            high_price = close_price * (1 + np.random.uniform(0, intra_vol * 0.3))
            low_price = open_price * (1 - np.random.uniform(0, intra_vol * 0.5))
        else:
            high_price = open_price * (1 + np.random.uniform(0, intra_vol * 0.3))
            low_price = close_price * (1 - np.random.uniform(0, intra_vol * 0.5))

        klines.append({
            "timestamp": 1_700_000_000 + i * 60,
            "open": round(open_price, 5),
            "high": round(high_price, 5),
            "low": round(low_price, 5),
            "close": round(close_price, 5),
            "volume": round(base_volume * vol_mult, 2),
        })
        price = close_price
    return klines


def _stub_llm_response(content: str, prompt_tokens=400, completion_tokens=100):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_strategy():
    """Real LLMClient (with fake API key — no network calls during init);
    then mock _fetch_btc_trend to avoid ccxt network."""
    from strategies.ai_trend_sniper_strategy import AITrendSniperStrategy
    strategy = AITrendSniperStrategy()
    strategy._fetch_btc_trend = AsyncMock(return_value={
        "price": 97000.0,
        "change_24h": 1.5,
        "trend": "温和上涨",
        "high_24h": 98000.0,
        "low_24h": 96000.0,
    })
    return strategy


class TestSniperTriggerPayload(unittest.TestCase):
    def setUp(self):
        self.strategy = _make_strategy()
        self.df = self.strategy._compute_indicators(_build_uptrend_klines())

    def test_payload_contains_extra_context(self):
        payload = self.strategy._build_trigger_payload(
            df=self.df,
            trigger_ctx={"signal_dir": "LONG", "trigger": "BULLISH_TREND_PULLBACK"},
            position=None,
            mode="open",
            extra={"extra_context": {
                "support": [0.14, 0.135],
                "resistance": [0.16, 0.165],
                "btc_trend": {"trend": "温和上涨", "change_24h": 1.5},
            }},
        )
        self.assertIsNotNone(payload)
        self.assertIn("extra_context", payload)
        self.assertEqual(payload["extra_context"]["support"], [0.14, 0.135])
        self.assertIn("decision_schema", payload)
        self.assertIn("EXECUTE | REJECT", payload["decision_schema"])
        # 绝对价 schema：tp/sl 直接是 number
        self.assertIn("tp_price", payload["decision_schema"])
        self.assertIn("ref_tp", payload)
        self.assertIn("ref_sl", payload)


class TestSniperSignalExtraction(unittest.TestCase):
    def setUp(self):
        self.strategy = _make_strategy()
        self.df = self.strategy._compute_indicators(_build_uptrend_klines())
        self.payload = self.strategy._build_trigger_payload(
            df=self.df,
            trigger_ctx={"signal_dir": "LONG", "trigger": "BULLISH_TREND_PULLBACK"},
            position=None,
            mode="open",
            extra={},
        )

    def test_execute_with_sufficient_profit(self):
        from core.message import SignalType
        current_price = self.payload["current_price"]
        tp = current_price * 1.05  # +5%
        sl = current_price * 0.97  # -3%
        signal = self.strategy._extract_signal(
            {"action": "EXECUTE", "confidence": "HIGH", "reason": "ok",
             "tp_price": tp, "sl_price": sl},
            symbol="DOGE/USDT",
            klines=[],
            market_data={},
            position=None,
            context=None,
            trigger_payload=self.payload,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.BUY)
        self.assertEqual(signal.take_profit, tp)
        self.assertEqual(signal.stop_loss, sl)

    def test_execute_with_insufficient_profit_rejected(self):
        """止盈不足 2.5% 时硬性否决（防止 AI 给出垃圾交易）。"""
        current_price = self.payload["current_price"]
        tp = current_price * 1.01  # 仅 +1%
        sl = current_price * 0.99
        signal = self.strategy._extract_signal(
            {"action": "EXECUTE", "confidence": "HIGH", "reason": "small move",
             "tp_price": tp, "sl_price": sl},
            symbol="DOGE/USDT",
            klines=[],
            market_data={},
            position=None,
            context=None,
            trigger_payload=self.payload,
        )
        self.assertIsNone(signal)

    def test_reject_returns_none(self):
        signal = self.strategy._extract_signal(
            {"action": "REJECT", "reason": "杂乱"},
            symbol="DOGE/USDT",
            klines=[],
            market_data={},
            position=None,
            context=None,
            trigger_payload=self.payload,
        )
        self.assertIsNone(signal)


class TestSniperTrailingShortCircuit(unittest.TestCase):
    """持仓时 trailing stop 命中应直接返 Signal，绕过 LLM。"""

    def test_trailing_short_circuits_llm(self):
        from core.message import SignalType
        strategy = _make_strategy()
        klines = _build_uptrend_klines()
        df = strategy._compute_indicators(klines)
        atr = float(df.iloc[-1]['atr'])
        current_price = float(df.iloc[-1]['close'])

        # 构造一个充分浮盈的 LONG 持仓，触发 trailing stop
        position = {
            "side": "long",
            "size": 100,
            "entry_price": current_price - atr * 5.0,
            "tp_price": current_price * 1.10,
            "sl_price": current_price - atr * 4.0,  # 旧 SL 远低于追踪后 SL
        }

        # mock LLM 永远抛错——确保 trailing 路径根本不调它
        async def _fail_llm(*args, **kwargs):
            raise AssertionError("LLM should not be called when trailing stop fires")

        with patch.object(strategy.client.chat.completions, "create", side_effect=_fail_llm):
            with patch.object(strategy, "_compute_indicators", return_value=df):
                signal = asyncio.run(strategy.analyze(
                    symbol="DOGE/USDT",
                    klines=klines,
                    market_data={"close": current_price},
                    position=position,
                    context=None,
                ))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, SignalType.HOLD)
        self.assertTrue(signal.metadata.get("adjust_tp_sl"))
        self.assertIn("trailing_info", signal.metadata)


class TestSniperHarnessFlowMocked(unittest.TestCase):
    """完整 analyze() 路径：force trigger → mock LLM → 断言 [EXTRA_CONTEXT] 段。"""

    def test_analyze_injects_extra_context(self):
        strategy = _make_strategy()
        klines = _build_uptrend_klines()
        df = strategy._compute_indicators(klines)

        # 强制触发：把指标改成"放量突破"场景
        last_idx = len(df) - 1
        df.iloc[last_idx, df.columns.get_loc('adx')] = 30.0
        df.iloc[last_idx, df.columns.get_loc('close')] = df.iloc[last_idx]['ema200'] * 1.05
        df.iloc[last_idx, df.columns.get_loc('upper_bb')] = df.iloc[last_idx]['close'] * 0.99
        df.iloc[last_idx, df.columns.get_loc('hist')] = 0.001
        vol_ma = df['volume'].rolling(window=20).mean().iloc[last_idx]
        df.iloc[last_idx, df.columns.get_loc('volume')] = vol_ma * 3.0
        df.iloc[last_idx, df.columns.get_loc('ema50')] = df.iloc[last_idx]['close'] * 0.90

        current_price = float(df.iloc[last_idx]['close'])
        tp = current_price * 1.05
        sl = current_price * 0.97

        async def _fake_create(*args, **kwargs):
            return _stub_llm_response(
                f'{{"action": "EXECUTE", "confidence": "HIGH", "reason": "ok",'
                f' "tp_price": {tp}, "sl_price": {sl}}}'
            )

        with patch.object(strategy.client.chat.completions, "create", side_effect=_fake_create):
            with patch.object(strategy, "_compute_indicators", return_value=df):
                signal = asyncio.run(strategy.analyze(
                    symbol="DOGE/USDT",
                    klines=klines,
                    market_data={"close": current_price},
                    position=None,
                    context=None,
                ))

        self.assertIsNotNone(signal)
        prompt_messages = signal.metadata.get("prompt_messages")
        self.assertIsNotNone(prompt_messages)
        self.assertEqual(len(prompt_messages), 3)
        # 静态前缀 messages[1] → [DECISION_SCHEMA]
        static = prompt_messages[1]["content"]
        self.assertIn("[DECISION_SCHEMA]", static)
        # 动态后缀 messages[2] → [REGIME], [EXTRA_CONTEXT], [TRIGGER]
        dynamic = prompt_messages[2]["content"]
        for marker in ["[REGIME]", "[EXTRA_CONTEXT]", "[TRIGGER]"]:
            self.assertIn(marker, dynamic, f"missing marker: {marker}")
        self.assertIn("btc_trend", dynamic)
        self.assertIn("温和上涨", dynamic)
        self.assertIn("support", dynamic)
        self.assertIn("resistance", dynamic)


class TestSniperDuplicatePositionGuard(unittest.TestCase):
    """同向持仓时不应重复开仓。"""

    def test_same_side_position_blocks_trigger(self):
        strategy = _make_strategy()
        df = strategy._compute_indicators(_build_uptrend_klines())
        triggered, _, ctx = strategy._check_hard_trigger(df, None, None)
        if triggered:
            same_side = {"side": ctx['signal_dir'].lower(), "size": 100}
            triggered2, _, _ = strategy._check_hard_trigger(df, same_side, None)
            self.assertFalse(triggered2)


if __name__ == "__main__":
    unittest.main()
