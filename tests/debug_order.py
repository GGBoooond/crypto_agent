"""调试下单问题"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from exchange.okx_exchange import OKXExchange


async def main():
    ex = OKXExchange(test_mode=False)
    ok = await ex.initialize()
    if not ok:
        print("初始化失败")
        return

    print("=== 账户信息 ===")
    # 查看账户余额
    balance = await ex.exchange.fetch_balance({'type': 'swap'})
    print(f"USDT 余额: {balance.get('USDT', {})}")

    # 查看 DOGE 合约信息
    print("\n=== DOGE 合约信息 ===")
    await ex.exchange.load_markets()
    market = ex.exchange.market("DOGE/USDT:USDT")
    print(f"合约类型: {market.get('type')}")
    print(f"最小下单量: {market.get('limits', {}).get('amount', {})}")
    print(f"合约面值: {market.get('contractSize')}")

    # 尝试下单
    print("\n=== 尝试下单 ===")
    symbol = "DOGE/USDT:USDT"
    side = "buy"
    # DOGE 合约面值 1000 DOGE/张，0.01 张 = 10 个 DOGE
    amount = 0.01

    # 设置杠杆
    leverage_ok = await ex.set_leverage(symbol, 10)
    print(f"设置杠杆: {leverage_ok}")

    # 直接用 ccxt 下单，看详细错误
    try:
        params = {
            'tdMode': 'cross',
            'posSide': 'long',
        }
        print(f"下单参数: symbol={symbol}, side={side}, amount={amount}, params={params}")
        
        order = await ex.exchange.create_market_order(symbol, side, amount, params=params)
        print(f"下单成功: {order}")
    except Exception as e:
        print(f"下单失败: {e}")

    await ex.close()


if __name__ == "__main__":
    asyncio.run(main())
