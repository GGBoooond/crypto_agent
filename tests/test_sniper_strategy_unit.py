"""
AI Trend Sniper 策略 - 单元测试

测试范围:
1. BTC 趋势分析 (_fetch_btc_trend): 趋势分类、缓存机制、API 失败容错
2. 指标计算与信号触发 (_check_sniper_triggers): 回调/突破信号、ADX 过滤、持仓过滤
3. JSON 解析鲁棒性 (_parse_ai_json): 多种格式的 AI 响应
4. 支撑阻力位 (_find_support_resistance): 局部高低点检测
5. Prompt 构建 (_build_sniper_prompt): OHLC/影线/BTC/MACD/支撑阻力 全部出现

运行方式: python -m pytest tests/test_sniper_strategy_unit.py -v
"""
import sys
import os
import time
import json
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import numpy as np
import pandas as pd

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# ============================================================
# 辅助函数: 生成模拟K线数据
# ============================================================

def generate_uptrend_klines(num_bars: int = 300, seed: int = 42) -> list:
    """
    生成一段上升趋势 + 末端回调的 K 线数据。
    设计为满足 BULLISH_TREND_PULLBACK 触发条件。
    """
    np.random.seed(seed)
    klines = []
    price = 0.15000
    base_volume = 50_000_000.0

    for i in range(num_bars):
        if i < 150:
            drift = 0.0002
            volatility = 0.003
            vol_mult = np.random.uniform(0.6, 1.2)
        elif i < 270:
            drift = 0.0008
            volatility = 0.005
            vol_mult = np.random.uniform(1.0, 2.5)
        else:
            drift = -0.0004
            volatility = 0.004
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

        volume = base_volume * vol_mult

        klines.append({
            'timestamp': f'2026-02-{1 + i // 96:02d} {(i % 96) * 15 // 60:02d}:{(i % 96) * 15 % 60:02d}:00',
            'open': round(open_price, 5),
            'high': round(high_price, 5),
            'low': round(low_price, 5),
            'close': round(close_price, 5),
            'volume': round(volume, 2),
        })
        price = close_price

    return klines


def make_strategy():
    """
    创建策略实例 (mock 掉 OpenAI 和 ccxt 的初始化)
    """
    with patch('strategies.ai_trend_sniper_strategy.AsyncOpenAI'):
        from strategies.ai_trend_sniper_strategy import AITrendSniperStrategy
        strategy = AITrendSniperStrategy(weight=1.0)
    return strategy


# ============================================================
# 测试类
# ============================================================

class TestBTCTrendClassification(unittest.TestCase):
    """测试 BTC 趋势分类逻辑"""

    def setUp(self):
        self.strategy = make_strategy()

    def _run(self, coro):
        return asyncio.run(coro)

    def _mock_btc_ticker(self, percentage: float):
        """构造 mock ccxt ticker 返回值"""
        return {
            'last': 97000.0,
            'percentage': percentage,
            'high': 98000.0,
            'low': 96000.0,
        }

    def test_strong_bullish(self):
        """24h +2.0% → 强势上涨"""
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value=self._mock_btc_ticker(2.0))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertIsNotNone(result)
        self.assertEqual(result['trend'], '强势上涨')
        self.assertEqual(result['change_24h'], 2.0)

    def test_mild_bullish(self):
        """24h +0.8% → 温和上涨"""
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value=self._mock_btc_ticker(0.8))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result['trend'], '温和上涨')

    def test_sideways(self):
        """24h +0.1% → 横盘震荡"""
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value=self._mock_btc_ticker(0.1))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result['trend'], '横盘震荡')

    def test_mild_bearish(self):
        """24h -0.8% → 温和下跌"""
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value=self._mock_btc_ticker(-0.8))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result['trend'], '温和下跌')

    def test_strong_bearish(self):
        """24h -3.5% → 强势下跌"""
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value=self._mock_btc_ticker(-3.5))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertEqual(result['trend'], '强势下跌')

    def test_boundary_values(self):
        """边界值: +0.3, -0.3, +1.5, -1.5"""
        cases = [
            (0.3, '横盘震荡'),     # 0.3 不 > 0.3, 归为横盘
            (-0.3, '横盘震荡'),    # -0.3 不 < -0.3
            (1.5, '温和上涨'),     # 1.5 不 > 1.5
            (-1.5, '温和下跌'),    # -1.5 不 < -1.5
            (0.31, '温和上涨'),
            (-0.31, '温和下跌'),
            (1.51, '强势上涨'),
            (-1.51, '强势下跌'),
        ]
        for pct, expected_trend in cases:
            self.strategy._btc_cache = None
            self.strategy._btc_cache_ts = 0
            mock_exchange = AsyncMock()
            mock_exchange.fetch_ticker = AsyncMock(return_value=self._mock_btc_ticker(pct))
            self.strategy._ccxt_public = mock_exchange

            result = self._run(self.strategy._fetch_btc_trend())
            self.assertEqual(result['trend'], expected_trend,
                             f"Failed for pct={pct}: expected '{expected_trend}', got '{result['trend']}'")


class TestBTCTrendCache(unittest.TestCase):
    """测试 BTC 趋势缓存机制"""

    def setUp(self):
        self.strategy = make_strategy()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_cache_hit(self):
        """缓存未过期时不应再次请求 API"""
        self.strategy._btc_cache = {
            "price": 95000.0, "change_24h": 1.0,
            "trend": "温和上涨", "high_24h": 96000.0, "low_24h": 94000.0
        }
        self.strategy._btc_cache_ts = time.time()  # 刚刚缓存

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock()
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())

        self.assertEqual(result['price'], 95000.0)
        mock_exchange.fetch_ticker.assert_not_called()  # 不应调用 API

    def test_cache_expired(self):
        """缓存过期后应重新请求 API"""
        self.strategy._btc_cache = {
            "price": 95000.0, "change_24h": 1.0,
            "trend": "温和上涨", "high_24h": 96000.0, "low_24h": 94000.0
        }
        self.strategy._btc_cache_ts = time.time() - 600  # 10分钟前 > TTL 5分钟

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
        """API 失败时应返回旧缓存"""
        old_cache = {
            "price": 95000.0, "change_24h": 1.0,
            "trend": "温和上涨", "high_24h": 96000.0, "low_24h": 94000.0
        }
        self.strategy._btc_cache = old_cache
        self.strategy._btc_cache_ts = time.time() - 600  # 过期

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(side_effect=Exception("Network error"))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())

        self.assertEqual(result, old_cache)  # 返回旧缓存

    def test_api_failure_no_cache_returns_none(self):
        """API 失败且无缓存时应返回 None"""
        self.strategy._btc_cache = None
        self.strategy._btc_cache_ts = 0

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(side_effect=Exception("Network error"))
        self.strategy._ccxt_public = mock_exchange

        result = self._run(self.strategy._fetch_btc_trend())
        self.assertIsNone(result)


class TestBTCTrendLiveAPI(unittest.TestCase):
    """
    集成测试: 真实调用 OKX 公共 API 获取 BTC 行情
    需要网络连接，单独运行:
        python -m unittest tests.test_sniper_strategy_unit.TestBTCTrendLiveAPI -v
    """

    def test_live_fetch_btc_trend(self):
        """真实获取 BTC 行情: 验证返回数据结构和字段合理性"""
        async def _test():
            strategy = make_strategy()
            strategy._btc_cache = None
            strategy._btc_cache_ts = 0
            strategy._ccxt_public = None
            try:
                result = await strategy._fetch_btc_trend()

                self.assertIsNotNone(result, "真实 API 调用不应返回 None (需要网络连接)")

                # 验证所有字段存在
                required_keys = ['price', 'change_24h', 'trend', 'high_24h', 'low_24h']
                for key in required_keys:
                    self.assertIn(key, result, f"返回数据缺少字段: {key}")

                # price 合理性 (BTC 目前在 5万~20万 之间)
                self.assertGreater(result['price'], 50000, f"BTC 价格异常偏低: {result['price']}")
                self.assertLess(result['price'], 200000, f"BTC 价格异常偏高: {result['price']}")

                # change_24h 应在 -30% ~ +30% 之间
                self.assertGreater(result['change_24h'], -30)
                self.assertLess(result['change_24h'], 30)

                # trend 应是五种之一
                valid_trends = ['强势上涨', '温和上涨', '横盘震荡', '温和下跌', '强势下跌']
                self.assertIn(result['trend'], valid_trends,
                              f"趋势判定异常: '{result['trend']}' 不在合法值 {valid_trends} 中")

                # high >= low
                self.assertGreaterEqual(result['high_24h'], result['low_24h'])

                # price 在 high/low 范围内 (允许微小偏差)
                self.assertGreaterEqual(result['price'], result['low_24h'] * 0.99)
                self.assertLessEqual(result['price'], result['high_24h'] * 1.01)

                print(f"\n  [LIVE] BTC 实时行情:")
                print(f"    价格: ${result['price']:,.2f}")
                print(f"    24h涨跌: {result['change_24h']}%")
                print(f"    趋势: {result['trend']}")
                print(f"    24h区间: ${result['low_24h']:,.2f} ~ ${result['high_24h']:,.2f}")
            finally:
                if strategy._ccxt_public is not None:
                    await strategy._ccxt_public.close()

        asyncio.run(_test())

    def test_live_cache_works_after_first_fetch(self):
        """真实获取后，第二次应命中缓存 (不再请求网络)"""
        async def _test():
            strategy = make_strategy()
            strategy._btc_cache = None
            strategy._btc_cache_ts = 0
            strategy._ccxt_public = None
            try:
                # 第一次: 走网络
                result1 = await strategy._fetch_btc_trend()
                self.assertIsNotNone(result1)
                first_ts = strategy._btc_cache_ts

                # 第二次: 应命中缓存, 时间戳不变
                result2 = await strategy._fetch_btc_trend()
                self.assertEqual(strategy._btc_cache_ts, first_ts, "第二次应命中缓存，时间戳不应变化")
                self.assertEqual(result1, result2, "两次结果应相同 (来自缓存)")
            finally:
                if strategy._ccxt_public is not None:
                    await strategy._ccxt_public.close()

        asyncio.run(_test())

    def test_live_btc_in_prompt(self):
        """真实 BTC 数据应能正确嵌入 Prompt"""
        async def _test():
            strategy = make_strategy()
            strategy._btc_cache = None
            strategy._btc_cache_ts = 0
            strategy._ccxt_public = None
            try:
                btc = await strategy._fetch_btc_trend()
                self.assertIsNotNone(btc)

                # 用真实 BTC 数据构建 prompt
                klines = generate_uptrend_klines(300)
                df = strategy._calculate_indicators(klines)
                curr = df.iloc[-1]
                context = {
                    "signal_dir": "LONG",
                    "trigger": "BULLISH_TREND_PULLBACK",
                    "adx": 28.5, "rsi": 42.0, "r_vol": 0.8,
                    "ema_dist": 5.0,
                    "atr": round(float(curr['atr']), 5),
                }

                _, user_prompt = strategy._build_sniper_prompt(
                    "DOGE/USDT:USDT", df, context, btc_trend=btc
                )

                # 真实价格应出现在 prompt 中
                self.assertIn(str(btc['price']), user_prompt)
                self.assertIn(btc['trend'], user_prompt)
                self.assertIn(str(btc['change_24h']), user_prompt)

                print(f"\n  [LIVE] BTC 数据已嵌入 Prompt ✓")
                print(f"    Prompt 包含: BTC现价={btc['price']}, 趋势={btc['trend']}")
            finally:
                if strategy._ccxt_public is not None:
                    await strategy._ccxt_public.close()

        asyncio.run(_test())


class TestParseAIJson(unittest.TestCase):
    """测试 AI JSON 响应解析的鲁棒性"""

    def setUp(self):
        self.strategy = make_strategy()

    def test_clean_json(self):
        """标准 JSON"""
        text = '{"action": "EXECUTE", "confidence": "HIGH", "reason": "趋势强劲", "tp_price": 0.20, "sl_price": 0.17}'
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'EXECUTE')
        self.assertEqual(result['tp_price'], 0.20)

    def test_json_with_markdown_code_block(self):
        """markdown 代码块包裹"""
        text = '''这是分析结果:
```json
{"action": "REJECT", "confidence": "MEDIUM", "reason": "波动不足", "tp_price": 0.18, "sl_price": 0.16}
```
'''
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'REJECT')

    def test_json_with_inline_comments(self):
        """带 // 注释的 JSON"""
        text = '''{
    "action": "EXECUTE", // 执行交易
    "confidence": "HIGH",
    "reason": "趋势明确",
    "tp_price": 0.20, // 止盈
    "sl_price": 0.17  // 止损
}'''
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'EXECUTE')
        self.assertEqual(result['sl_price'], 0.17)

    def test_json_with_trailing_comma(self):
        """带尾逗号的 JSON"""
        text = '''{
    "action": "REJECT",
    "confidence": "MEDIUM",
    "reason": "K线杂乱",
    "tp_price": 0.18,
    "sl_price": 0.16,
}'''
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'REJECT')

    def test_json_with_surrounding_text(self):
        """JSON 前后有多余文字"""
        text = '根据分析，我的判断如下：\n{"action": "EXECUTE", "confidence": "HIGH", "reason": "OK", "tp_price": 0.20, "sl_price": 0.17}\n以上是我的建议。'
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'EXECUTE')

    def test_json_with_comments_and_trailing_comma(self):
        """同时有注释和尾逗号"""
        text = '''{
    "action": "EXECUTE",  // 执行
    "confidence": "HIGH",
    "reason": "完美信号",
    "tp_price": 0.22,  // 目标价
    "sl_price": 0.18,  // 止损价
}'''
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'EXECUTE')
        self.assertEqual(result['tp_price'], 0.22)

    def test_empty_text_raises(self):
        """空文本应抛异常"""
        with self.assertRaises(ValueError):
            self.strategy._parse_ai_json("")

    def test_no_json_raises(self):
        """没有 JSON 对象应抛异常"""
        with self.assertRaises(ValueError):
            self.strategy._parse_ai_json("这是一段没有JSON的文字，只有纯文本。")

    def test_markdown_block_without_language_tag(self):
        """markdown 代码块不带 json 标签"""
        text = '''```
{"action": "EXECUTE", "confidence": "MEDIUM", "reason": "test", "tp_price": 0.20, "sl_price": 0.17}
```'''
        result = self.strategy._parse_ai_json(text)
        self.assertEqual(result['action'], 'EXECUTE')


class TestIndicatorsAndTriggers(unittest.TestCase):
    """测试指标计算和信号触发逻辑"""

    def setUp(self):
        self.strategy = make_strategy()
        self.klines = generate_uptrend_klines(300)

    def test_calculate_indicators_returns_all_columns(self):
        """指标计算应返回所有必要列"""
        df = self.strategy._calculate_indicators(self.klines)
        self.assertFalse(df.empty)
        expected_cols = ['ema200', 'ema50', 'ema20', 'macd', 'signal', 'hist',
                         'atr', 'adx', 'rsi', 'upper_bb', 'lower_bb']
        for col in expected_cols:
            self.assertIn(col, df.columns, f"缺少指标列: {col}")

    def test_calculate_indicators_values_reasonable(self):
        """指标值应在合理范围内"""
        df = self.strategy._calculate_indicators(self.klines)
        last = df.iloc[-1]
        # RSI 应在 0-100 之间
        self.assertGreater(last['rsi'], 0)
        self.assertLess(last['rsi'], 100)
        # ATR 应为正数
        self.assertGreater(last['atr'], 0)
        # EMA200 应为正数
        self.assertGreater(last['ema200'], 0)

    def test_adx_filter_blocks_low_adx(self):
        """ADX 低于阈值时应被过滤"""
        df = self.strategy._calculate_indicators(self.klines)
        # 手动将 ADX 置为很低的值
        df.iloc[-1, df.columns.get_loc('adx')] = 15.0

        triggered, reason, ctx = self.strategy._check_sniper_triggers(df)
        self.assertFalse(triggered)

    def test_insufficient_data_returns_false(self):
        """数据不足 200 根时应被过滤"""
        short_klines = self.klines[:100]
        df = self.strategy._calculate_indicators(short_klines)
        triggered, reason, ctx = self.strategy._check_sniper_triggers(df)
        self.assertFalse(triggered)

    def test_same_direction_position_blocks(self):
        """同向持仓时不应重复开仓"""
        df = self.strategy._calculate_indicators(self.klines)
        # 先确认不带持仓时是否能触发
        triggered, reason, ctx = self.strategy._check_sniper_triggers(df)

        if triggered:
            # 模拟同向持仓
            position = {'side': ctx['signal_dir'].lower(), 'size': 100}
            triggered2, _, _ = self.strategy._check_sniper_triggers(df, position)
            self.assertFalse(triggered2, "同向持仓时不应重复开仓")

    def test_trigger_context_has_required_fields(self):
        """触发时 context 应包含所有必要字段"""
        df = self.strategy._calculate_indicators(self.klines)
        triggered, reason, ctx = self.strategy._check_sniper_triggers(df)

        if triggered:
            required_fields = ['signal_dir', 'trigger', 'adx', 'rsi', 'r_vol', 'ema_dist', 'atr']
            for field in required_fields:
                self.assertIn(field, ctx, f"context 缺少字段: {field}")
            self.assertIn(ctx['signal_dir'], ['LONG', 'SHORT'])
            self.assertGreater(ctx['adx'], 0)

    def test_bullish_breakout_with_high_volume(self):
        """构造放量突破场景，应触发 BULLISH_POWER_BREAKOUT"""
        df = self.strategy._calculate_indicators(self.klines)
        last_idx = len(df) - 1

        # 确保 ADX 高于阈值
        df.iloc[last_idx, df.columns.get_loc('adx')] = 30.0
        # 价格在 EMA200 之上 (牛市)
        df.iloc[last_idx, df.columns.get_loc('close')] = df.iloc[last_idx]['ema200'] * 1.05
        # 突破布林带上轨
        df.iloc[last_idx, df.columns.get_loc('upper_bb')] = df.iloc[last_idx]['close'] * 0.99
        # MACD 柱状图 > 0
        df.iloc[last_idx, df.columns.get_loc('hist')] = 0.001
        # 巨大成交量 (> 2x 均量)
        vol_ma = df['volume'].rolling(window=20).mean().iloc[last_idx]
        df.iloc[last_idx, df.columns.get_loc('volume')] = vol_ma * 3.0
        # 确保不满足回调条件 (远离 EMA50)
        df.iloc[last_idx, df.columns.get_loc('ema50')] = df.iloc[last_idx]['close'] * 0.90

        triggered, reason, ctx = self.strategy._check_sniper_triggers(df)
        self.assertTrue(triggered, f"放量突破场景应触发信号, reason={reason}")
        self.assertEqual(ctx['signal_dir'], 'LONG')
        self.assertIn('BREAKOUT', ctx['trigger'])


class TestSupportResistance(unittest.TestCase):
    """测试支撑阻力位计算"""

    def setUp(self):
        self.strategy = make_strategy()

    def test_basic_support_resistance(self):
        """基本的支撑阻力位检测"""
        klines = generate_uptrend_klines(300)
        df = self.strategy._calculate_indicators(klines)

        sr = self.strategy._find_support_resistance(df, n=50)

        self.assertIn('resistance', sr)
        self.assertIn('support', sr)
        self.assertIsInstance(sr['resistance'], list)
        self.assertIsInstance(sr['support'], list)

        current_price = float(df.iloc[-1]['close'])

        # 阻力位都应 > 现价
        for r in sr['resistance']:
            self.assertGreater(r, current_price, f"阻力位 {r} 应大于现价 {current_price}")

        # 支撑位都应 < 现价
        for s in sr['support']:
            self.assertLess(s, current_price, f"支撑位 {s} 应小于现价 {current_price}")

    def test_max_three_levels(self):
        """阻力位和支撑位各最多 3 个"""
        klines = generate_uptrend_klines(300)
        df = self.strategy._calculate_indicators(klines)
        sr = self.strategy._find_support_resistance(df, n=50)

        self.assertLessEqual(len(sr['resistance']), 3)
        self.assertLessEqual(len(sr['support']), 3)

    def test_resistance_sorted_ascending(self):
        """阻力位应升序排列 (最近的阻力在前)"""
        klines = generate_uptrend_klines(300)
        df = self.strategy._calculate_indicators(klines)
        sr = self.strategy._find_support_resistance(df, n=50)

        if len(sr['resistance']) > 1:
            for i in range(len(sr['resistance']) - 1):
                self.assertLessEqual(sr['resistance'][i], sr['resistance'][i + 1])

    def test_support_sorted_descending(self):
        """支撑位应降序排列 (最近的支撑在前)"""
        klines = generate_uptrend_klines(300)
        df = self.strategy._calculate_indicators(klines)
        sr = self.strategy._find_support_resistance(df, n=50)

        if len(sr['support']) > 1:
            for i in range(len(sr['support']) - 1):
                self.assertGreaterEqual(sr['support'][i], sr['support'][i + 1])


class TestPromptBuilding(unittest.TestCase):
    """测试 Prompt 构建: 验证新增字段全部出现在输出中"""

    def setUp(self):
        self.strategy = make_strategy()
        self.klines = generate_uptrend_klines(300)
        self.df = self.strategy._calculate_indicators(self.klines)

        curr = self.df.iloc[-1]
        self.context = {
            "signal_dir": "LONG",
            "trigger": "BULLISH_TREND_PULLBACK (EMA50 Support + RSI Reset)",
            "adx": 28.5,
            "rsi": round(float(curr['rsi']), 2),
            "r_vol": 0.72,
            "ema_dist": round((curr['close'] - curr['ema200']) / curr['ema200'] * 100, 2),
            "atr": round(float(curr['atr']), 5),
        }

        self.market_data = {
            'symbol': 'DOGE/USDT:USDT',
            'last': 0.185,
            'bid': 0.1849,
            'ask': 0.1851,
            'high': 0.190,
            'low': 0.175,
            'volume': 50000000,
            'change': 2.5,
            'timestamp': '2026-02-09T12:00:00'
        }

        self.btc_trend = {
            "price": 97500.0,
            "change_24h": -1.82,
            "trend": "强势下跌",
            "high_24h": 99000.0,
            "low_24h": 96500.0,
        }

    def test_prompt_returns_tuple(self):
        """应返回 (system_prompt, user_prompt) 元组"""
        result = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context,
            market_data=self.market_data,
            btc_trend=self.btc_trend
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_system_prompt_has_role_and_rules(self):
        """system_prompt 应包含角色设定和规则"""
        system_prompt, _ = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('波段交易员', system_prompt)
        self.assertIn('核心规则', system_prompt)
        self.assertIn('输出格式', system_prompt)
        self.assertIn('REJECT', system_prompt)
        self.assertIn('JSON', system_prompt)

    def test_user_prompt_has_ohlc(self):
        """user_prompt 中的K线应包含 OHLC 数据"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        # 检查 OHLC 标记
        self.assertIn('O:', user_prompt)
        self.assertIn('H:', user_prompt)
        self.assertIn('L:', user_prompt)
        self.assertIn('C:', user_prompt)

    def test_user_prompt_has_shadow_ratios(self):
        """user_prompt 中的K线应包含影线比例"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('上影:', user_prompt)
        self.assertIn('下影:', user_prompt)

    def test_user_prompt_has_timeframe(self):
        """user_prompt 应包含 K线周期"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('K线周期', user_prompt)

    def test_user_prompt_has_support_resistance(self):
        """user_prompt 应包含支撑/阻力位"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('阻力位', user_prompt)
        self.assertIn('支撑位', user_prompt)

    def test_user_prompt_has_macd_trend(self):
        """user_prompt 应包含 MACD 柱状图趋势"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('MACD', user_prompt)
        self.assertIn('柱状图', user_prompt)
        # 应有箭头趋势
        self.assertTrue('↑' in user_prompt or '↓' in user_prompt,
                        "MACD 趋势应包含 ↑ 或 ↓ 箭头")

    def test_user_prompt_has_ema_order(self):
        """user_prompt 应包含 EMA 排列状态"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('EMA排列', user_prompt)
        # 应该是三种之一
        self.assertTrue(
            '多头排列' in user_prompt or '空头排列' in user_prompt or '交叉/纠缠' in user_prompt,
            "应包含 EMA 排列状态"
        )

    def test_user_prompt_has_market_data(self):
        """传入 market_data 时, user_prompt 应包含 24h 行情"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context,
            market_data=self.market_data
        )
        self.assertIn('24h涨跌幅', user_prompt)
        self.assertIn('24h最高', user_prompt)
        self.assertIn('24h成交量', user_prompt)

    def test_user_prompt_has_btc_trend(self):
        """传入 btc_trend 时, user_prompt 应包含 BTC 趋势信息"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context,
            btc_trend=self.btc_trend
        )
        self.assertIn('BTC现价', user_prompt)
        self.assertIn('97500', user_prompt)
        self.assertIn('BTC趋势判定', user_prompt)
        self.assertIn('强势下跌', user_prompt)
        self.assertIn('-1.82', user_prompt)

    def test_user_prompt_without_btc_trend(self):
        """不传 btc_trend 时, user_prompt 不应包含 BTC 信息"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context,
            btc_trend=None
        )
        self.assertNotIn('BTC现价', user_prompt)
        self.assertNotIn('BTC趋势判定', user_prompt)

    def test_user_prompt_has_ref_prices(self):
        """user_prompt 应包含参考止盈止损价"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        self.assertIn('参考止盈', user_prompt)
        self.assertIn('参考止损', user_prompt)
        self.assertIn('距现价', user_prompt)

    def test_15_candles_in_prompt(self):
        """user_prompt 应包含 15 根 K线"""
        _, user_prompt = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context
        )
        # 每根K线以 "T-" 开头
        candle_count = user_prompt.count('T-')
        self.assertEqual(candle_count, 15, f"应有 15 根K线, 实际 {candle_count} 根")

    def test_system_prompt_no_data(self):
        """system_prompt 不应包含具体交易数据"""
        system_prompt, _ = self.strategy._build_sniper_prompt(
            "DOGE/USDT:USDT", self.df, self.context,
            btc_trend=self.btc_trend
        )
        self.assertNotIn('DOGE', system_prompt)
        self.assertNotIn('BTC现价', system_prompt)
        self.assertNotIn('K线周期', system_prompt)


class TestFullAnalyzeFlow(unittest.TestCase):
    """测试完整 analyze 流程 (mock AI 调用)"""

    def setUp(self):
        self.strategy = make_strategy()
        self.klines = generate_uptrend_klines(300)
        self.market_data = {
            'symbol': 'DOGE/USDT:USDT', 'last': 0.185,
            'bid': 0.1849, 'ask': 0.1851,
            'high': 0.190, 'low': 0.175,
            'volume': 50000000, 'change': 2.5,
            'timestamp': '2026-02-09T12:00:00'
        }

    def _run(self, coro):
        return asyncio.run(coro)

    def test_insufficient_klines_returns_none(self):
        """K线不足时应返回 None"""
        result = self._run(self.strategy.analyze(
            "DOGE/USDT:USDT", self.klines[:100], self.market_data
        ))
        self.assertIsNone(result)

    def test_disabled_strategy_returns_none(self):
        """策略禁用时应返回 None"""
        self.strategy.enabled = False
        result = self._run(self.strategy.analyze(
            "DOGE/USDT:USDT", self.klines, self.market_data
        ))
        self.assertIsNone(result)

    @patch.object(
        make_strategy().__class__, '_fetch_btc_trend',
        new_callable=lambda: lambda self: AsyncMock(return_value={
            "price": 97000.0, "change_24h": 1.5,
            "trend": "温和上涨", "high_24h": 98000.0, "low_24h": 96000.0
        })
    )
    def test_ai_execute_produces_signal(self, mock_btc):
        """AI 返回 EXECUTE 时应产生完整 Signal"""
        strategy = make_strategy()

        # Mock BTC
        strategy._fetch_btc_trend = AsyncMock(return_value={
            "price": 97000.0, "change_24h": 1.5,
            "trend": "温和上涨", "high_24h": 98000.0, "low_24h": 96000.0
        })

        # 强制触发信号 (mock _check_sniper_triggers)
        df = strategy._calculate_indicators(self.klines)
        curr = df.iloc[-1]
        mock_context = {
            "signal_dir": "LONG",
            "trigger": "BULLISH_TREND_PULLBACK",
            "adx": 30.0, "rsi": 42.0, "r_vol": 0.8,
            "ema_dist": 5.0, "atr": round(float(curr['atr']), 5)
        }
        strategy._check_sniper_triggers = MagicMock(
            return_value=(True, "[LONG] BULLISH_TREND_PULLBACK", mock_context)
        )

        # Mock AI 返回
        current_price = float(curr['close'])
        tp = round(current_price * 1.05, 5)  # +5%
        sl = round(current_price * 0.97, 5)  # -3%

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "action": "EXECUTE",
            "confidence": "HIGH",
            "reason": "趋势强劲，回调到位",
            "tp_price": tp,
            "sl_price": sl
        })
        strategy.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = self._run(strategy.analyze("DOGE/USDT:USDT", self.klines, self.market_data))

        self.assertIsNotNone(result, "AI 返回 EXECUTE 时应产生 Signal")
        self.assertEqual(result.signal_type.value, "BUY")
        self.assertAlmostEqual(result.take_profit, tp, places=5)
        self.assertAlmostEqual(result.stop_loss, sl, places=5)
        self.assertIn('Sniper', result.reason)

    def test_ai_reject_returns_none(self):
        """AI 返回 REJECT 时应返回 None"""
        strategy = make_strategy()

        strategy._fetch_btc_trend = AsyncMock(return_value=None)

        df = strategy._calculate_indicators(self.klines)
        curr = df.iloc[-1]
        mock_context = {
            "signal_dir": "LONG", "trigger": "BULLISH_TREND_PULLBACK",
            "adx": 30.0, "rsi": 42.0, "r_vol": 0.8,
            "ema_dist": 5.0, "atr": round(float(curr['atr']), 5)
        }
        strategy._check_sniper_triggers = MagicMock(
            return_value=(True, "[LONG] BULLISH_TREND_PULLBACK", mock_context)
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "action": "REJECT",
            "confidence": "MEDIUM",
            "reason": "波动空间不足3%",
            "tp_price": 0.18, "sl_price": 0.17
        })
        strategy.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = self._run(strategy.analyze("DOGE/USDT:USDT", self.klines, self.market_data))
        self.assertIsNone(result, "AI 返回 REJECT 时应返回 None")

    def test_low_tp_rejected_by_safety_check(self):
        """AI 给出的止盈不足 2.5% 时应被安全检查否决"""
        strategy = make_strategy()

        strategy._fetch_btc_trend = AsyncMock(return_value=None)

        df = strategy._calculate_indicators(self.klines)
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        mock_context = {
            "signal_dir": "LONG", "trigger": "BULLISH_TREND_PULLBACK",
            "adx": 30.0, "rsi": 42.0, "r_vol": 0.8,
            "ema_dist": 5.0, "atr": round(float(curr['atr']), 5)
        }
        strategy._check_sniper_triggers = MagicMock(
            return_value=(True, "[LONG] BULLISH_TREND_PULLBACK", mock_context)
        )

        # 止盈只有 1% — 应被否决
        tp = round(current_price * 1.01, 5)
        sl = round(current_price * 0.97, 5)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "action": "EXECUTE", "confidence": "HIGH",
            "reason": "看好", "tp_price": tp, "sl_price": sl
        })
        strategy.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = self._run(strategy.analyze("DOGE/USDT:USDT", self.klines, self.market_data))
        self.assertIsNone(result, "止盈不足 2.5% 应被安全检查否决")


if __name__ == "__main__":
    unittest.main(verbosity=2)
