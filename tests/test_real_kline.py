"""
真实K线数据验证测试
直接调用OKX公共接口获取DOGE/USDT的真实K线数据，验证处理后实体是否为0
注意：使用公共接口，无需API Key
"""
import sys
import os
import asyncio

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import ccxt.async_support as ccxt


async def test_real_kline_data():
    print("=" * 60)
    print("真实K线数据验证测试 (直接使用CCXT公共接口)")
    print("=" * 60)
    
    # 1. 初始化交易所连接 (无需API Key，公共数据)
    exchange = ccxt.okx({
        'enableRateLimit': True,
    })
    
    try:
        # 2. 获取真实K线数据
        symbol = "DOGE/USDT:USDT"
        timeframe = "1m"
        
        print(f"\n正在获取 {symbol} {timeframe} K线数据...")
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=20)
        
        if not ohlcv:
            print("FAIL: 未获取到K线数据")
            return
        
        print(f"成功获取 {len(ohlcv)} 根K线\n")
        
        # 3. 检查原始数据类型和值
        print("=" * 60)
        print("原始K线数据检查 (CCXT返回格式)")
        print("=" * 60)
        
        zero_body_count = 0
        
        for i, candle in enumerate(ohlcv[-10:]):  # 只显示最后10根
            # CCXT返回格式: [timestamp, open, high, low, close, volume]
            timestamp = candle[0]
            open_price = candle[1]
            high_price = candle[2]
            low_price = candle[3]
            close_price = candle[4]
            volume = candle[5]
            
            # 计算实体
            body = abs(close_price - open_price)
            upper_shadow = high_price - max(close_price, open_price)
            lower_shadow = min(close_price, open_price) - low_price
            
            # 检查类型
            open_type = type(open_price).__name__
            close_type = type(close_price).__name__
            
            k_type = "阳" if close_price > open_price else "阴" if close_price < open_price else "十"
            
            print(f"K{i+1}: {k_type}线")
            print(f"    Open:  {open_price} (类型: {open_type})")
            print(f"    Close: {close_price} (类型: {close_type})")
            print(f"    High:  {high_price}")
            print(f"    Low:   {low_price}")
            print(f"    实体:  {body}")
            print(f"    上影:  {upper_shadow}")
            print(f"    下影:  {lower_shadow}")
            
            # 检查实体是否为0
            if body == 0:
                print(f"    ⚠️  实体为0!")
                zero_body_count += 1
            elif body < 0.00001:
                print(f"    ⚠️  实体极小: {body}")
            
            print()
        
        # 4. 结论
        print("=" * 60)
        print("测试结论")
        print("=" * 60)
        
        if zero_body_count > 5:
            print(f"WARN: 发现 {zero_body_count}/10 根K线实体为0，可能是横盘行情")
        else:
            print(f"PASS: 只有 {zero_body_count}/10 根K线实体为0（正常范围）")
        
        # 5. 验证格式化精度
        print("\n" + "=" * 60)
        print("格式化精度测试")
        print("=" * 60)
        
        last_candle = ohlcv[-1]
        close_price = last_candle[4]
        open_price = last_candle[1]
        body = abs(close_price - open_price)
        
        # 旧格式化方式
        old_format = f"{body:.2f}"
        
        # 新格式化方式（动态精度）
        price_str = f"{close_price}"
        decimals = len(price_str.split('.')[1]) if '.' in price_str else 2
        fmt = f".{max(decimals, 4)}f"
        new_format = f"{body:{fmt}}"
        
        print(f"当前价格: {close_price}")
        print(f"实体原始值: {body}")
        print(f"旧格式化 (:.2f): '{old_format}'")
        print(f"新格式化 (动态): '{new_format}'")
        
        if old_format == "0.00" and body > 0:
            print("\n⚠️  问题确认: 旧格式化会丢失精度!")
            print("   这就是为什么AI看到的都是'实体:0.00'")
        
        if float(new_format) > 0 or body == 0:
            print("✓  新格式化保留了精度")
        
        # 6. 模拟OKXExchange的处理逻辑
        print("\n" + "=" * 60)
        print("模拟OKXExchange.fetch_ohlcv处理")
        print("=" * 60)
        
        from datetime import datetime
        
        # 模拟原来的处理方式 (不加float转换)
        result_old = []
        for candle in ohlcv[-3:]:
            result_old.append({
                'timestamp': datetime.fromtimestamp(candle[0] / 1000),
                'open': candle[1],  # 原来没有float()
                'high': candle[2],
                'low': candle[3],
                'close': candle[4],
                'volume': candle[5]
            })
        
        # 模拟新的处理方式 (加float转换)
        result_new = []
        for candle in ohlcv[-3:]:
            result_new.append({
                'timestamp': datetime.fromtimestamp(candle[0] / 1000),
                'open': float(candle[1]),
                'high': float(candle[2]),
                'low': float(candle[3]),
                'close': float(candle[4]),
                'volume': float(candle[5])
            })
        
        print("旧处理方式 (无float转换):")
        for k in result_old:
            print(f"  Open类型: {type(k['open']).__name__}, Close类型: {type(k['close']).__name__}")
            print(f"  Open值: {k['open']}, Close值: {k['close']}")
        
        print("\n新处理方式 (有float转换):")
        for k in result_new:
            print(f"  Open类型: {type(k['open']).__name__}, Close类型: {type(k['close']).__name__}")
            print(f"  Open值: {k['open']}, Close值: {k['close']}")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await exchange.close()
        print("\n交易所连接已关闭")


if __name__ == "__main__":
    asyncio.run(test_real_kline_data())
