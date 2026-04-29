import unittest

from config import settings
from exchange.okx_exchange import OKXExchange


class TestOKXExchangeInterfaces(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 强制使用测试模式，避免真实下单
        self.exchange = OKXExchange(test_mode=false)
        ok = await self.exchange.initialize()
        self.assertTrue(ok, "OKX 初始化失败")

    async def asyncTearDown(self):
        await self.exchange.close()

    async def test_public_endpoints(self):
        klines = await self.exchange.fetch_ohlcv(
            settings.trading_symbol,
            settings.trading_timeframe,
            limit=5,
        )
        self.assertTrue(len(klines) > 0, "K线数据为空")

        ticker = await self.exchange.fetch_ticker(settings.trading_symbol)
        self.assertEqual(ticker.get("symbol"), settings.trading_symbol)
        self.assertIsNotNone(ticker.get("last"))

    async def test_private_endpoints_mocked_in_test_mode(self):
        balance = await self.exchange.fetch_balance()
        self.assertIn("USDT", balance)

        positions = await self.exchange.fetch_positions(settings.trading_symbol)
        self.assertIsInstance(positions, list)

        market_order = await self.exchange.create_market_order(
            settings.trading_symbol,
            "buy",
            0.001,
        )
        self.assertEqual(market_order.status, "closed")

        limit_order = await self.exchange.create_limit_order(
            settings.trading_symbol,
            "buy",
            0.001,
            1,
        )
        self.assertEqual(limit_order.status, "open")

        canceled = await self.exchange.cancel_order(
            limit_order.order_id,
            settings.trading_symbol,
        )
        self.assertTrue(canceled)

        leverage_ok = await self.exchange.set_leverage(
            settings.trading_symbol,
            1,
        )
        self.assertTrue(leverage_ok)

        order_info = await self.exchange.fetch_order(
            market_order.order_id,
            settings.trading_symbol,
        )
        self.assertIn("status", order_info)


if __name__ == "__main__":
    unittest.main()


class TestOKXLiveTrading(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.exchange = OKXExchange(test_mode=False)
        ok = await self.exchange.initialize()
        self.assertTrue(ok, "OKX 初始化失败")

    async def asyncTearDown(self):
        await self.exchange.close()

    async def test_live_buy_doge(self):
        leverage_ok = await self.exchange.set_leverage(
            "DOGE/USDT:USDT",
            10,
        )
        self.assertTrue(leverage_ok)

        # DOGE 合约面值是 1000 DOGE/张
        # 最小下单量是 0.01 张 = 10 个 DOGE
        # 0.01 张 × 1000 DOGE × 0.35 USDT ≈ 3.5 USDT 名义价值
        # 10 倍杠杆需要约 0.35 USDT 保证金
        order = await self.exchange.create_market_order(
            "DOGE/USDT:USDT",
            "buy",
            0.01,  # 0.01 张合约 = 10 个 DOGE
        )
        self.assertIn(order.status, ["closed", "open"])
