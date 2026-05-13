"""
AI Trend Sniper 策略 - 单元测试

测试范围:
1. BTC 趋势分类 ``_classify_btc_trend``（纯函数）
2. BTC 趋势缓存 ``_fetch_btc_trend`` 的 hit/expired/失败兜底
3. JSON 解析鲁棒性 ``BaseAIStrategy._extract_json``
4. 指标计算与硬触发器 ``_compute_indicators`` / ``_check_hard_trigger``
5. 支撑阻力位 ``_find_support_resistance``
6. Trailing stop ``_compute_trailing_sl``

运行方式: python -m pytest tests/test_sniper_strategy_unit.py -v
"""
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault("OKX_API_KEY", "test")
os.environ.setdefault("OKX_SECRET_KEY", "test")
os.environ.setdefault("OKX_PASSPHRASE", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")


def generate_uptrend_klines(num_bars: int = 300, seed: int = 42) -> list:
    """模拟一段上升趋势 + 末端回调，满足 BULLISH_TREND_PULLBACK 触发条件。"""
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
            'timestamp': f'2026-02-{1 + i // 96:02d} {(i % 96) * 15 // 60:02d}:{(i % 96) * 15 % 60:02d}:00',
            'open': round(open_price, 5),
            'high': round(high_price, 5),
            'low': round(low_price, 5),
            'close': round(close_price, 5),
            'volume': round(base_volume * vol_mult, 2),
        })
        price = close_price

    return klines


def make_strategy():
    """Mock LLMClient（在 base_ai_strategy 中）以避免实例化真实 LLM 客户端。"""
    with patch('strategies.base_ai_strategy.LLMClient'):
        from strategies.ai_trend_sniper_strategy import AITrendSniperStrategy
        return AITrendSniperStrategy(weight=1.0)


# ============================================================
# 1. BTC 趋势分类（纯函数）
# ============================================================

class TestBTCTrendClassification(unittest.TestCase):
    def setUp(self):
        from strategies.ai_trend_sniper_strategy import AITrendSniperStrategy
        self.classify = AITrendSniperStrategy._classify_btc_trend

    def test_strong_bullish(self):
        self.assertEqual(self.classify(2.0), '强势上涨')

    def test_mild_bullish(self):
        self.assertEqual(self.classify(0.8), '温和上涨')

    def test_sideways(self):
        self.assertEqual(self.classify(0.1), '横盘震荡')

    def test_mild_bearish(self):
        self.assertEqual(self.classify(-0.8), '温和下跌')

    def test_strong_bearish(self):
        self.assertEqual(self.classify(-3.5), '强势下跌')

    def test_boundary_values(self):
        cases = [
            (0.3, '横盘震荡'), (-0.3, '横盘震荡'),
            (1.5, '温和上涨'), (-1.5, '温和下跌'),
            (0.31, '温和上涨'), (-0.31, '温和下跌'),
            (1.51, '强势上涨'), (-1.51, '强势下跌'),
        ]
        for pct, expected in cases:
            self.assertEqual(self.classify(pct), expected, f"pct={pct}")


# ============================================================
# 2. BTC 趋势缓存
# ============================================================

class TestBTCTrendCache(unittest.TestCase):
    def setUp(self):
        self.strategy = make_strategy()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_cache_hit(self):
        cached = {
            "price": 95000.0, "change_24h": 1.0,
            "trend": "温和上涨", "high_24h": 96000.0, "low_24h": 94000.0,
        }
        self.strategy._btc_cache = cached
        self.strategy._btc_cache_ts = time.time()

        mock_exchange = AsyncMock()
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result, cached)
        mock_exchange.fetch_ticker.assert_not_called()

    def test_cache_expired(self):
        self.strategy._btc_cache = {
            "price": 95000.0, "change_24h": 1.0,
            "trend": "温和上涨", "high_24h": 96000.0, "low_24h": 94000.0,
        }
        self.strategy._btc_cache_ts = time.time() - 600

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value={
            'last': 97500.0, 'percentage': -2.0,
            'high': 98000.0, 'low': 96500.0,
        })
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result['price'], 97500.0)
        self.assertEqual(result['trend'], '强势下跌')
        mock_exchange.fetch_ticker.assert_called_once()

    def test_api_failure_returns_old_cache(self):
        old_cache = {
            "price": 95000.0, "change_24h": 1.0,
            "trend": "温和上涨", "high_24h": 96000.0, "low_24h": 94000.0,
        }
        self.strategy._btc_cache = old_cache
        self.strategy._btc_cache_ts = time.time() - 600

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(side_effect=Exception("Network error"))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result, old_cache)


# ============================================================
# 3. JSON 解析鲁棒性 (基类 _extract_json)
# ============================================================

class TestExtractJson(unittest.TestCase):
    def setUp(self):
        from strategies.base_ai_strategy import BaseAIStrategy
        self.extract = BaseAIStrategy._extract_json

    def test_clean_json(self):
        text = '{"action": "EXECUTE", "tp_price": 0.20, "sl_price": 0.17}'
        result = self.extract(text)
        self.assertEqual(result['action'], 'EXECUTE')
        self.assertEqual(result['tp_price'], 0.20)

    def test_json_with_markdown_code_block(self):
        text = '```json\n{"action": "REJECT", "tp_price": 0.18}\n```'
        result = self.extract(text)
        self.assertEqual(result['action'], 'REJECT')

    def test_json_with_inline_comments(self):
        text = '{\n  "action": "EXECUTE", // ok\n  "tp_price": 0.20\n}'
        result = self.extract(text)
        self.assertEqual(result['action'], 'EXECUTE')

    def test_json_with_trailing_comma(self):
        text = '{"action": "REJECT", "tp_price": 0.18,}'
        result = self.extract(text)
        self.assertEqual(result['action'], 'REJECT')

    def test_json_with_surrounding_text(self):
        text = '我的判断:\n{"action": "EXECUTE", "tp_price": 0.20}\n以上.'
        result = self.extract(text)
        self.assertEqual(result['action'], 'EXECUTE')

    def test_empty_text_returns_none(self):
        self.assertIsNone(self.extract(""))


# ============================================================
# 4. 指标 + 硬触发器
# ============================================================

class TestIndicatorsAndTriggers(unittest.TestCase):
    def setUp(self):
        self.strategy = make_strategy()
        self.klines = generate_uptrend_klines(300)

    def test_compute_indicators_returns_all_columns(self):
        df = self.strategy._compute_indicators(self.klines)
        self.assertIsNotNone(df)
        for col in ['ema200', 'ema50', 'ema20', 'macd', 'signal', 'hist',
                    'atr', 'adx', 'rsi', 'upper_bb', 'lower_bb']:
            self.assertIn(col, df.columns, f"缺少指标列: {col}")

    def test_indicator_values_reasonable(self):
        df = self.strategy._compute_indicators(self.klines)
        last = df.iloc[-1]
        self.assertGreater(last['rsi'], 0)
        self.assertLess(last['rsi'], 100)
        self.assertGreater(last['atr'], 0)
        self.assertGreater(last['ema200'], 0)

    def test_adx_filter_blocks_low_adx(self):
        df = self.strategy._compute_indicators(self.klines)
        df.iloc[-1, df.columns.get_loc('adx')] = 15.0
        triggered, _, _ = self.strategy._check_hard_trigger(df, None, None)
        self.assertFalse(triggered)

    def test_insufficient_data_returns_false(self):
        short_klines = self.klines[:100]
        df = self.strategy._compute_indicators(short_klines)
        triggered, _, _ = self.strategy._check_hard_trigger(df, None, None)
        self.assertFalse(triggered)

    def test_same_direction_position_blocks(self):
        df = self.strategy._compute_indicators(self.klines)
        triggered, _, ctx = self.strategy._check_hard_trigger(df, None, None)
        if triggered:
            position = {'side': ctx['signal_dir'].lower(), 'size': 100}
            triggered2, _, _ = self.strategy._check_hard_trigger(df, position, None)
            self.assertFalse(triggered2)

    def test_bullish_breakout_with_high_volume(self):
        df = self.strategy._compute_indicators(self.klines)
        last_idx = len(df) - 1
        df.iloc[last_idx, df.columns.get_loc('adx')] = 30.0
        df.iloc[last_idx, df.columns.get_loc('close')] = df.iloc[last_idx]['ema200'] * 1.05
        df.iloc[last_idx, df.columns.get_loc('upper_bb')] = df.iloc[last_idx]['close'] * 0.99
        df.iloc[last_idx, df.columns.get_loc('hist')] = 0.001
        vol_ma = df['volume'].rolling(window=20).mean().iloc[last_idx]
        df.iloc[last_idx, df.columns.get_loc('volume')] = vol_ma * 3.0
        df.iloc[last_idx, df.columns.get_loc('ema50')] = df.iloc[last_idx]['close'] * 0.90

        triggered, reason, ctx = self.strategy._check_hard_trigger(df, None, None)
        self.assertTrue(triggered, f"放量突破场景应触发, reason={reason}")
        self.assertEqual(ctx['signal_dir'], 'LONG')
        self.assertIn('BREAKOUT', ctx['trigger'])


# ============================================================
# 5. 支撑阻力位
# ============================================================

class TestSupportResistance(unittest.TestCase):
    def setUp(self):
        self.strategy = make_strategy()

    def test_basic_support_resistance(self):
        klines = generate_uptrend_klines(300)
        df = self.strategy._compute_indicators(klines)
        sr = self.strategy._find_support_resistance(df, n=50)

        self.assertIn('resistance', sr)
        self.assertIn('support', sr)
        current_price = float(df.iloc[-1]['close'])
        for r in sr['resistance']:
            self.assertGreater(r, current_price)
        for s in sr['support']:
            self.assertLess(s, current_price)

    def test_max_three_levels(self):
        klines = generate_uptrend_klines(300)
        df = self.strategy._compute_indicators(klines)
        sr = self.strategy._find_support_resistance(df, n=50)
        self.assertLessEqual(len(sr['resistance']), 3)
        self.assertLessEqual(len(sr['support']), 3)


# ============================================================
# 6. Trailing stop 数学
# ============================================================

class TestTrailingStop(unittest.TestCase):
    def setUp(self):
        self.strategy = make_strategy()
        self.klines = generate_uptrend_klines(300)
        self.df = self.strategy._compute_indicators(self.klines)

    def test_long_below_activation_returns_none(self):
        # 价格未达"浮盈 1 倍 ATR"门槛
        atr = float(self.df.iloc[-1]['atr'])
        current_price = float(self.df.iloc[-1]['close'])
        entry_price = current_price - atr * 0.3  # 浮盈不到 1 倍 ATR
        result = self.strategy._compute_trailing_sl(
            df=self.df,
            pos_side='long',
            entry_price=entry_price,
            current_price=current_price,
            current_sl=0.0,
            atr=atr,
        )
        self.assertIsNone(result)

    def test_long_returns_higher_sl_after_activation(self):
        atr = float(self.df.iloc[-1]['atr'])
        current_price = float(self.df.iloc[-1]['close'])
        entry_price = current_price - atr * 5.0  # 充分浮盈
        result = self.strategy._compute_trailing_sl(
            df=self.df,
            pos_side='long',
            entry_price=entry_price,
            current_price=current_price,
            current_sl=0.0,
            atr=atr,
        )
        self.assertIsNotNone(result)
        self.assertLess(result, current_price)

    def test_long_does_not_lower_sl(self):
        atr = float(self.df.iloc[-1]['atr'])
        current_price = float(self.df.iloc[-1]['close'])
        entry_price = current_price - atr * 5.0
        # 把 current_sl 设到一个比"近 10 根高点 - 2.5 ATR"还高的位置
        recent_high = float(self.df['high'].tail(10).max())
        already_high_sl = recent_high - atr * 1.0  # 比新计算 SL (recent_high - 2.5*ATR) 高
        result = self.strategy._compute_trailing_sl(
            df=self.df,
            pos_side='long',
            entry_price=entry_price,
            current_price=current_price,
            current_sl=already_high_sl,
            atr=atr,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
