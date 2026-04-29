import sys
import os
import re
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from strategies.ai_hybrid_strategy import AIHybridStrategy
except ImportError as e:
    print(f"Import Error: {e}")
    print("Ensure you are running this script with the correct python environment (e.g. source venv/bin/activate)")
    sys.exit(1)

def test_kline_formatting_precision():
    print("Starting test: test_kline_formatting_precision")
    
    # 1. 初始化策略
    try:
        strategy = AIHybridStrategy()
    except Exception as e:
        print(f"Strategy initialization failed: {e}")
        # If it fails due to API key, we might need to mock settings or env vars
        # But for now let's see if it runs
        return
    
    # 2. 模拟 DOGE 样板数据
    base_kline = {
        'timestamp': '2024-01-01 12:00:00',
        'open': 0.12340,
        'high': 0.12350,
        'low': 0.12330,
        'close': 0.12340, 
        'volume': 1000000.0
    }
    
    klines = [base_kline.copy() for _ in range(50)]
    
    # T-0: 实体 0.00005
    klines[-1]['open'] = 0.12340
    klines[-1]['close'] = 0.12345 
    klines[-1]['high'] = 0.12350
    klines[-1]['low'] = 0.12340
    
    # T-1: 实体 0.00010
    klines[-2]['open'] = 0.12350
    klines[-2]['close'] = 0.12340 
    
    # 3. 计算指标
    df = strategy._calculate_indicators(klines)
    if df.empty:
        print("FAIL: Indicators calculation returned empty DataFrame")
        return
        
    print("Indicators calculated successfully.")
    
    # 4. 验证 Prompt
    context = {
        'signal_dir': 'LONG',
        'trigger': 'TEST_TRIGGER',
        'rsi': 30,
        'trend': 'BEARISH'
    }
    
    prompt = strategy._build_ai_prompt("DOGE/USDT", df, context)
    
    print("\n=== Generated Prompt Segment ===")
    print(prompt)
    print("================================")
    
    # 5. 断言检查
    # 检查 T-1 (最近一根, 代码中索引生成逻辑是 -5 到 -1)
    # 期望格式包含类似 "T-1: 阳线 | 实体:0.00005"
    t0_match = re.search(r"T-1:.*?实体:(\d+\.\d+)", prompt)
    if not t0_match:
        print("FAIL: Could not find T-1 entity value in prompt")
        return
        
    t0_body = float(t0_match.group(1))
    print(f"\nDetected T-1 Body: {t0_body}")
    
    expected_body = 0.00005
    if t0_body <= 0:
         print(f"FAIL: Entity value is zero or negative: {t0_body}")
         return
         
    if abs(t0_body - expected_body) > 0.000001:
        print(f"FAIL: Entity value precision mismatch. Expected {expected_body}, got {t0_body}")
        return

    print("PASS: Entity precision check passed.")
    print("Test Completed Successfully.")

if __name__ == "__main__":
    test_kline_formatting_precision()
