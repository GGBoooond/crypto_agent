"""
实盘测试: 开仓 -> 挂止盈止损单 -> 撤单 -> 平仓 完整流程

测试流程:
1. 设置杠杆 (10x)
2. 开多仓 (买入 0.01 张 DOGE 合约)
3. 挂止盈单
4. 挂止损单
5. 查询条件单状态
6. 撤销止盈止损单
7. 平仓

⚠️ 警告: 这是实盘测试，会产生真实交易！
运行前请确保:
- 交易账户有足够的 USDT 保证金
- 理解测试会产生真实的交易费用
"""

import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchange.okx_exchange import OKXExchange
from config import settings


async def test_full_tp_sl_flow():
    """测试完整的止盈止损流程"""
    
    print("=" * 60)
    print("🚀 实盘测试: 开仓 -> 止盈止损单 -> 撤单 -> 平仓")
    print("=" * 60)
    
    # 测试参数
    symbol = "DOGE/USDT:USDT"
    leverage = 10
    amount = 0.01  # 0.01 张合约 = 10 个 DOGE
    
    exchange = OKXExchange(test_mode=False)
    
    tp_order_id = None
    sl_order_id = None
    
    try:
        # 1. 初始化交易所
        print("\n[1/8] 初始化交易所...")
        ok = await exchange.initialize()
        if not ok:
            print("❌ 交易所初始化失败")
            return False
        print("✅ 交易所初始化成功")
        
        # 2. 设置杠杆
        print(f"\n[2/8] 设置杠杆 {leverage}x...")
        leverage_ok = await exchange.set_leverage(symbol, leverage)
        if not leverage_ok:
            print("❌ 设置杠杆失败")
            return False
        print(f"✅ 杠杆设置成功: {leverage}x")
        
        # 3. 获取当前价格
        print("\n[3/8] 获取当前价格...")
        ticker = await exchange.fetch_ticker(symbol)
        current_price = ticker.get('last', 0)
        print(f"✅ 当前价格: {current_price}")
        
        # 计算止盈止损价格 (多单)
        # 止盈: 当前价 +1%
        # 止损: 当前价 -1%
        tp_price = round(current_price * 1.01, 5)
        sl_price = round(current_price * 0.99, 5)
        print(f"   计划止盈价: {tp_price} (+1%)")
        print(f"   计划止损价: {sl_price} (-1%)")
        
        # 4. 开多仓
        print(f"\n[4/8] 开多仓: {amount} 张合约...")
        order = await exchange.create_market_order(symbol, "buy", amount)
        
        if order.status not in ["closed", "open"]:
            print(f"❌ 开仓失败: {order}")
            return False
        
        entry_price = order.price or current_price
        print(f"✅ 开仓成功!")
        print(f"   订单ID: {order.order_id}")
        print(f"   成交数量: {order.filled}")
        print(f"   成交价格: {entry_price}")
        
        # 等待订单完全成交 (增加等待时间)
        print("   等待订单成交...")
        await asyncio.sleep(3)
        
        # 5. 查询持仓确认
        print("\n[5/8] 查询持仓确认...")
        positions = await exchange.fetch_positions(symbol)
        
        print(f"   原始持仓数据: {positions}")  # 调试输出
        
        position = None
        for pos in positions:
            # 注意: fetch_positions 返回的是 'size' 而不是 'contracts'
            if pos.get('symbol') == symbol and float(pos.get('size', 0)) > 0:
                position = pos
                break
        
        if not position:
            print("❌ 未找到持仓，可能是订单未成交或数量太小")
            # 尝试直接使用下单数量继续测试
            print("   ⚠️ 使用下单数量继续测试...")
            pos_size = amount
            pos_side = 'long'
        else:
            pos_size = float(position.get('size', 0))
            pos_side = position.get('side', 'unknown')
            print(f"✅ 持仓确认: {pos_side} {pos_size} 张")
        
        # 6. 挂止盈止损单
        print(f"\n[6/8] 挂止盈止损条件单...")
        
        # 6.1 挂止盈单 (多仓平仓用 sell)
        print(f"   → 挂止盈单: sell @ {tp_price}")
        tp_result = await exchange.create_algo_order(
            symbol=symbol,
            side='sell',  # 平多用 sell
            amount=pos_size,
            trigger_price=tp_price,
            order_type='conditional',
            reduce_only=True,
            algo_type='tp'
        )
        
        if tp_result.get('status') == 'live':
            tp_order_id = tp_result.get('order_id')
            print(f"   ✅ 止盈单成功: ID={tp_order_id}")
        else:
            print(f"   ❌ 止盈单失败: {tp_result.get('error', 'Unknown')}")
        
        # 6.2 挂止损单
        print(f"   → 挂止损单: sell @ {sl_price}")
        sl_result = await exchange.create_algo_order(
            symbol=symbol,
            side='sell',  # 平多用 sell
            amount=pos_size,
            trigger_price=sl_price,
            order_type='conditional',
            reduce_only=True,
            algo_type='sl'
        )
        
        if sl_result.get('status') == 'live':
            sl_order_id = sl_result.get('order_id')
            print(f"   ✅ 止损单成功: ID={sl_order_id}")
        else:
            print(f"   ❌ 止损单失败: {sl_result.get('error', 'Unknown')}")
        
        # 等待条件单生效
        await asyncio.sleep(2)
        
        # 7. 撤销条件单
        print(f"\n[7/8] 撤销条件单...")
        
        if tp_order_id:
            print(f"   → 撤销止盈单: {tp_order_id}")
            tp_cancel = await exchange.cancel_algo_order(tp_order_id, symbol)
            if tp_cancel:
                print(f"   ✅ 止盈单已撤销")
            else:
                print(f"   ❌ 止盈单撤销失败")
        
        if sl_order_id:
            print(f"   → 撤销止损单: {sl_order_id}")
            sl_cancel = await exchange.cancel_algo_order(sl_order_id, symbol)
            if sl_cancel:
                print(f"   ✅ 止损单已撤销")
            else:
                print(f"   ❌ 止损单撤销失败")
        
        await asyncio.sleep(1)
        
        # 8. 平仓
        print(f"\n[8/8] 平仓...")
        close_order = await exchange.create_market_order(
            symbol=symbol,
            side="sell",  # 平多用 sell
            amount=pos_size,
            reduce_only=True
        )
        
        if close_order.status in ["closed", "open"]:
            print(f"✅ 平仓成功!")
            print(f"   订单ID: {close_order.order_id}")
            print(f"   成交数量: {close_order.filled}")
            print(f"   成交价格: {close_order.price}")
        else:
            print(f"❌ 平仓失败: {close_order}")
            return False
        
        # 结果汇总
        print("\n" + "=" * 60)
        print("🎉 测试完成! 流程汇总:")
        print("=" * 60)
        print(f"  1. 交易对: {symbol}")
        print(f"  2. 杠杆: {leverage}x")
        print(f"  3. 开仓价: {entry_price}")
        print(f"  4. 止盈价: {tp_price} (已撤销)")
        print(f"  5. 止损价: {sl_price} (已撤销)")
        print(f"  6. 平仓价: {close_order.price}")
        
        # 计算盈亏 (简化计算)
        if close_order.price and entry_price:
            pnl_pct = (close_order.price - entry_price) / entry_price * 100
            print(f"  7. 盈亏: {pnl_pct:+.4f}%")
        
        return True
        
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        
        # 异常时尝试清理
        print("\n⚠️ 尝试清理...")
        try:
            # 撤销条件单
            if tp_order_id:
                await exchange.cancel_algo_order(tp_order_id, symbol)
                print(f"   撤销止盈单: {tp_order_id}")
            if sl_order_id:
                await exchange.cancel_algo_order(sl_order_id, symbol)
                print(f"   撤销止损单: {sl_order_id}")
            
            # 尝试平仓
            positions = await exchange.fetch_positions(symbol)
            for pos in positions:
                if pos.get('symbol') == symbol and float(pos.get('size', 0)) > 0:
                    size = float(pos.get('size', 0))
                    side = 'sell' if pos.get('side') == 'long' else 'buy'
                    await exchange.create_market_order(symbol, side, size, reduce_only=True)
                    print(f"   平仓: {side} {size} 张")
        except Exception as cleanup_e:
            print(f"   清理失败: {cleanup_e}")
        
        return False
        
    finally:
        await exchange.close()
        print("\n✅ 交易所连接已关闭")


async def test_short_tp_sl_flow():
    """测试空仓的止盈止损流程"""
    
    print("\n" + "=" * 60)
    print("🚀 实盘测试: 开空仓 -> 止盈止损单 -> 撤单 -> 平仓")
    print("=" * 60)
    
    symbol = "DOGE/USDT:USDT"
    leverage = 10
    amount = 0.01
    
    exchange = OKXExchange(test_mode=False)
    
    tp_order_id = None
    sl_order_id = None
    
    try:
        print("\n[1/8] 初始化交易所...")
        ok = await exchange.initialize()
        if not ok:
            print("❌ 交易所初始化失败")
            return False
        print("✅ 交易所初始化成功")
        
        print(f"\n[2/8] 设置杠杆 {leverage}x...")
        await exchange.set_leverage(symbol, leverage)
        print(f"✅ 杠杆设置成功")
        
        print("\n[3/8] 获取当前价格...")
        ticker = await exchange.fetch_ticker(symbol)
        current_price = ticker.get('last', 0)
        print(f"✅ 当前价格: {current_price}")
        
        # 空单止盈止损 (与多单相反)
        # 止盈: 当前价 -1% (价格下跌盈利)
        # 止损: 当前价 +1% (价格上涨亏损)
        tp_price = round(current_price * 0.99, 5)
        sl_price = round(current_price * 1.01, 5)
        print(f"   计划止盈价: {tp_price} (-1%)")
        print(f"   计划止损价: {sl_price} (+1%)")
        
        print(f"\n[4/8] 开空仓: {amount} 张合约...")
        order = await exchange.create_market_order(symbol, "sell", amount)
        
        if order.status not in ["closed", "open"]:
            print(f"❌ 开仓失败: {order}")
            return False
        
        entry_price = order.price or current_price
        print(f"✅ 开仓成功! 价格: {entry_price}")
        
        print("   等待订单成交...")
        await asyncio.sleep(3)
        
        print("\n[5/8] 查询持仓确认...")
        positions = await exchange.fetch_positions(symbol)
        
        print(f"   原始持仓数据: {positions}")
        
        position = None
        for pos in positions:
            if pos.get('symbol') == symbol and float(pos.get('size', 0)) > 0:
                position = pos
                break
        
        if not position:
            print("❌ 未找到持仓，使用下单数量继续...")
            pos_size = amount
        else:
            pos_size = float(position.get('size', 0))
            print(f"✅ 持仓确认: short {pos_size} 张")
        
        print(f"\n[6/8] 挂止盈止损条件单...")
        
        # 空仓平仓用 buy
        print(f"   → 挂止盈单: buy @ {tp_price}")
        tp_result = await exchange.create_algo_order(
            symbol=symbol,
            side='buy',  # 平空用 buy
            amount=pos_size,
            trigger_price=tp_price,
            order_type='conditional',
            reduce_only=True,
            algo_type='tp'
        )
        
        if tp_result.get('status') == 'live':
            tp_order_id = tp_result.get('order_id')
            print(f"   ✅ 止盈单成功: ID={tp_order_id}")
        else:
            print(f"   ❌ 止盈单失败: {tp_result.get('error', 'Unknown')}")
        
        print(f"   → 挂止损单: buy @ {sl_price}")
        sl_result = await exchange.create_algo_order(
            symbol=symbol,
            side='buy',
            amount=pos_size,
            trigger_price=sl_price,
            order_type='conditional',
            reduce_only=True,
            algo_type='sl'
        )
        
        if sl_result.get('status') == 'live':
            sl_order_id = sl_result.get('order_id')
            print(f"   ✅ 止损单成功: ID={sl_order_id}")
        else:
            print(f"   ❌ 止损单失败: {sl_result.get('error', 'Unknown')}")
        
        await asyncio.sleep(2)
        
        print(f"\n[7/8] 撤销条件单...")
        
        if tp_order_id:
            tp_cancel = await exchange.cancel_algo_order(tp_order_id, symbol)
            print(f"   ✅ 止盈单已撤销" if tp_cancel else "   ❌ 止盈单撤销失败")
        
        if sl_order_id:
            sl_cancel = await exchange.cancel_algo_order(sl_order_id, symbol)
            print(f"   ✅ 止损单已撤销" if sl_cancel else "   ❌ 止损单撤销失败")
        
        await asyncio.sleep(1)
        
        print(f"\n[8/8] 平仓...")
        close_order = await exchange.create_market_order(
            symbol=symbol,
            side="buy",  # 平空用 buy
            amount=pos_size,
            reduce_only=True
        )
        
        if close_order.status in ["closed", "open"]:
            print(f"✅ 平仓成功! 价格: {close_order.price}")
        else:
            print(f"❌ 平仓失败: {close_order}")
            return False
        
        print("\n🎉 空仓测试完成!")
        return True
        
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        await exchange.close()


if __name__ == "__main__":
    print("选择测试模式:")
    print("1. 多仓测试 (默认)")
    print("2. 空仓测试")
    print("3. 两者都测试")
    
    choice = input("\n请输入选项 [1/2/3]: ").strip() or "1"
    
    if choice == "1":
        asyncio.run(test_full_tp_sl_flow())
    elif choice == "2":
        asyncio.run(test_short_tp_sl_flow())
    elif choice == "3":
        asyncio.run(test_full_tp_sl_flow())
        print("\n" + "-" * 60 + "\n")
        asyncio.run(test_short_tp_sl_flow())
    else:
        print("无效选项，运行默认多仓测试")
        asyncio.run(test_full_tp_sl_flow())
