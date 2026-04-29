"""
AI Trend Sniper 策略 - AI 调用延迟测试

测试目标：
1. 构造逼真的 DOGE/USDT 15分钟 K线数据 (300根)
2. 生成策略 Prompt
3. 分别测试 deepseek-chat 和 deepseek-reasoner 的响应时间
4. 验证 AI 返回的 JSON 格式是否正确
"""
import sys
import os
import json
import time
import asyncio
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from openai import AsyncOpenAI
from config import settings


def generate_realistic_klines(num_bars: int = 300) -> list:
    """
    生成逼真的 DOGE/USDT 15分钟 K线数据
    模拟场景：一段上升趋势 -> 最近出现回调到 EMA50 附近
    """
    np.random.seed(42)
    
    klines = []
    
    # 起始价格
    price = 0.15000
    base_volume = 50_000_000.0
    
    # 分三个阶段：
    # Phase 1 (0-149): 缓慢上涨 (建立 EMA200 > 当前价的基础)
    # Phase 2 (150-270): 加速上涨 (建立强趋势, ADX 升高)
    # Phase 3 (271-299): 回调 (模拟回踩 EMA50, RSI 冷却)
    
    for i in range(num_bars):
        if i < 150:
            # Phase 1: 缓慢上涨，波动小
            drift = 0.0002  # 微弱上涨趋势
            volatility = 0.003
        elif i < 270:
            # Phase 2: 加速上涨，波动适中
            drift = 0.0008  # 明显上涨趋势
            volatility = 0.005
        else:
            # Phase 3: 回调阶段
            drift = -0.0004  # 小幅回落
            volatility = 0.004
        
        # 生成 OHLCV
        change = drift + np.random.normal(0, volatility)
        open_price = price
        close_price = price * (1 + change)
        
        # 生成合理的 high/low
        intra_vol = abs(change) + np.random.exponential(volatility * 0.5)
        if close_price > open_price:
            high_price = close_price * (1 + np.random.uniform(0, intra_vol * 0.3))
            low_price = open_price * (1 - np.random.uniform(0, intra_vol * 0.5))
        else:
            high_price = open_price * (1 + np.random.uniform(0, intra_vol * 0.3))
            low_price = close_price * (1 - np.random.uniform(0, intra_vol * 0.5))
        
        # 成交量：趋势期放量，回调期缩量
        if i < 150:
            vol_mult = np.random.uniform(0.6, 1.2)
        elif i < 270:
            vol_mult = np.random.uniform(1.0, 2.5)
        else:
            vol_mult = np.random.uniform(0.4, 0.9)  # 回调缩量
        
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


def build_test_prompt(klines: list) -> str:
    """
    使用策略的完整逻辑构建 Prompt (不依赖策略类实例化，避免交易所初始化)
    """
    import pandas as pd
    
    df = pd.DataFrame(klines)
    cols = ['open', 'high', 'low', 'close', 'volume']
    df[cols] = df[cols].astype(float)
    
    # 计算指标
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['hist'] = df['macd'] - df['signal']
    
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    df['std'] = df['close'].rolling(window=20).std()
    df['upper_bb'] = df['ema20'] + (df['std'] * 2)
    df['lower_bb'] = df['ema20'] - (df['std'] * 2)
    
    # 取最新行
    curr = df.iloc[-1]
    current_price = float(curr['close'])
    atr = float(curr['atr'])
    
    # 构造 context (模拟回调信号)
    ema_dist = round((curr['close'] - curr['ema200']) / curr['ema200'] * 100, 2)
    
    context = {
        "signal_dir": "LONG",
        "trigger": "BULLISH_TREND_PULLBACK (EMA50 Support + RSI Reset)",
        "adx": 28.5,
        "rsi": round(float(curr['rsi']), 2),
        "r_vol": 0.72,
        "ema_dist": ema_dist,
        "atr": round(atr, 5),
    }
    
    min_target_pct = 3.0
    min_target_price_dist = current_price * (min_target_pct / 100.0)
    sl_dist = max(atr * 2.0, min_target_price_dist / 2)
    ref_sl = current_price - sl_dist
    tp_dist = max(min_target_price_dist, atr * 3.0)
    ref_tp = current_price + tp_dist
    
    price_fmt = ".5f"
    
    # K线形态
    vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
    recent_candles = []
    for i in range(15):
        idx = -(15 - i)
        row = df.iloc[idx]
        k_type = "阳" if row['close'] > row['open'] else "阴"
        vol_ratio = row['volume'] / vol_ma if vol_ma > 0 else 0
        recent_candles.append(
            f"T{idx}: {k_type} | C:{row['close']:{price_fmt}} | Vol:{vol_ratio:.1f}x"
        )
    
    prompt = f"""
身份设定：你是一名**传奇的波段交易员(Swing Trader)**，智商极高，风格稳健。
你的座右铭是："弱水三千，只取一瓢"。你只在胜率极高(>80%)且盈亏比极佳(>1:2)时出手。
老板要求：止盈必须 > 3%，绝对不能频繁亏损手续费。

【战场态势】
- 标的: DOGE/USDT:USDT
- 现价: {current_price:{price_fmt}}
- 信号: {context['signal_dir']} ({context['trigger']})
- 趋势强度(ADX): {context['adx']} ( >25 代表趋势强劲)
- 长期趋势: 距离EMA200 {context['ema_dist']}%
- 波动率(ATR): {context['atr']:{price_fmt}}

【微观形态 (15根)】
{chr(10).join(recent_candles)}

【策略约束】
1. **最小利润**: 目标利润必须超过 3% ({min_target_pct}%)。如果当前波动率不足以支撑3%的波动，**直接 REJECT**。
2. **止损逻辑**: 止损要宽（参考价: {ref_sl:{price_fmt}}），必须扛得住常规震荡。
3. **入场确认**: 
   - 如果是回调(Pullback)，必须确认有"止跌"信号（如缩量、下影线）。
   - 如果是突破(Breakout)，必须确认成交量巨大 (RVol > 2.0)。
4. **拒绝垃圾时间**: 如果K线杂乱无章、长上影长下影交替，说明主力分歧，REJECT。

【决策输出 (JSON)】
{{
    "action": "EXECUTE | REJECT",
    "confidence": "HIGH | MEDIUM",
    "reason": "深入分析趋势结构、量价关系。如拒绝，说明原因（如：波动空间不足3%、成交量未配合等）。",
    "tp_price": {ref_tp:{price_fmt}}, // 必须 > 进场价 +/- 3%
    "sl_price": {ref_sl:{price_fmt}}  // 必须合理保护
}}
"""
    return prompt, current_price


async def test_model_latency(model_name: str, prompt: str, client: AsyncOpenAI) -> dict:
    """
    测试单个模型的调用延迟和响应质量
    """
    print(f"\n{'='*60}")
    print(f"  测试模型: {model_name}")
    print(f"{'='*60}")
    
    result = {
        "model": model_name,
        "latency_ms": 0,
        "success": False,
        "action": None,
        "reason": None,
        "tp_price": None,
        "sl_price": None,
        "error": None,
        "raw_response": None,
    }
    
    try:
        start_time = time.perf_counter()
        
        # reasoner 模型不支持 system role 和 temperature 参数
        # 且需要更大的 max_tokens (思考过程 + 最终输出共享额度)
        is_reasoner = "reasoner" in model_name
        
        messages = []
        if is_reasoner:
            # reasoner 不支持 system message，将指令合并到 user message
            messages = [
                {"role": "user", "content": "你是顶级量化交易员。只输出JSON，不要输出任何其他文字。\n\n" + prompt}
            ]
        else:
            messages = [
                {"role": "system", "content": "你是顶级量化交易员。只输出JSON。"},
                {"role": "user", "content": prompt}
            ]
        
        create_params = {
            "model": model_name,
            "messages": messages,
            "max_tokens": 4096 if is_reasoner else 500,
        }
        if not is_reasoner:
            create_params["temperature"] = 0.1
        
        response = await asyncio.wait_for(
            client.chat.completions.create(**create_params),
            timeout=120  # reasoner 可能较慢，给 120s
        )
        
        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000
        
        result["latency_ms"] = round(latency_ms, 1)
        
        message = response.choices[0].message
        raw_text = message.content or ""
        
        # deepseek-reasoner 的思考过程在 reasoning_content 中
        reasoning_text = getattr(message, 'reasoning_content', None) or ""
        
        result["raw_response"] = raw_text
        
        # Token 使用情况
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else "N/A"
        completion_tokens = usage.completion_tokens if usage else "N/A"
        total_tokens = usage.total_tokens if usage else "N/A"
        # reasoner 模型有 reasoning_tokens
        reasoning_tokens = getattr(usage, 'completion_tokens_details', None)
        reasoning_tokens_count = getattr(reasoning_tokens, 'reasoning_tokens', None) if reasoning_tokens else None
        
        print(f"\n  ⏱  响应耗时: {latency_ms:.0f} ms ({latency_ms/1000:.2f} s)")
        print(f"  📊 Token 用量: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")
        if reasoning_tokens_count is not None:
            print(f"  🧠 思考 Token: {reasoning_tokens_count}")
        
        # 尝试从多个来源提取 JSON:
        # 优先级: content > reasoning_content
        json_text = None
        json_source = ""
        
        for source_name, source_text in [("content", raw_text), ("reasoning_content", reasoning_text)]:
            if not source_text:
                continue
            start_idx = source_text.find('{')
            end_idx = source_text.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                try:
                    # 预校验: 能否解析为有效 JSON
                    candidate = source_text[start_idx:end_idx]
                    json.loads(candidate)
                    json_text = candidate
                    json_source = source_name
                    break
                except json.JSONDecodeError:
                    continue
        
        if json_text:
            ai_decision = json.loads(json_text)
            result["success"] = True
            result["action"] = ai_decision.get("action")
            result["reason"] = ai_decision.get("reason")
            result["tp_price"] = ai_decision.get("tp_price")
            result["sl_price"] = ai_decision.get("sl_price")
            
            print(f"\n  ✅ JSON 解析成功 (来源: {json_source})")
            print(f"  📋 决策: {result['action']}")
            print(f"  💡 理由: {result['reason']}")
            print(f"  🎯 止盈: {result['tp_price']}")
            print(f"  🛡  止损: {result['sl_price']}")
            if reasoning_text:
                print(f"  🧠 思考摘要: {reasoning_text[:200]}...")
        else:
            result["error"] = "无法从 content 和 reasoning_content 中提取 JSON"
            print(f"\n  ❌ JSON 解析失败")
            print(f"  content: {raw_text[:200] if raw_text else '(空)'}")
            print(f"  reasoning_content: {reasoning_text[:200] if reasoning_text else '(空)'}")
            
    except asyncio.TimeoutError:
        result["error"] = "请求超时 (120s)"
        result["latency_ms"] = 120000
        print(f"\n  ❌ 请求超时 (120s)")
        
    except Exception as e:
        result["error"] = str(e)
        print(f"\n  ❌ 调用异常: {e}")
    
    return result


async def main():
    print("=" * 60)
    print("  AI Trend Sniper - 大模型调用延迟测试")
    print("=" * 60)
    
    # 1. 生成逼真数据
    print("\n[1/3] 生成 300 根 DOGE/USDT 15分钟 K线数据...")
    klines = generate_realistic_klines(300)
    print(f"  价格范围: {klines[0]['close']:.5f} -> {klines[-1]['close']:.5f}")
    print(f"  K线数量: {len(klines)}")
    
    # 2. 构建 Prompt
    print("\n[2/3] 构建策略 Prompt...")
    prompt, current_price = build_test_prompt(klines)
    print(f"  当前价格: {current_price:.5f}")
    print(f"  Prompt 长度: {len(prompt)} 字符")
    print(f"\n  --- Prompt 预览 (前500字) ---")
    print(f"  {prompt[:500]}...")
    print(f"  --- 预览结束 ---")
    
    # 3. 测试两个模型
    print("\n[3/3] 开始测试 AI 模型响应...")
    
    client = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url
    )
    
    models = ["deepseek-chat", "deepseek-reasoner"]
    results = []
    
    for model in models:
        result = await test_model_latency(model, prompt, client)
        results.append(result)
    
    # 4. 汇总对比
    print("\n")
    print("=" * 60)
    print("  📊 测试结果汇总")
    print("=" * 60)
    print(f"  {'模型':<22} {'耗时(ms)':<12} {'耗时(s)':<10} {'状态':<10} {'决策':<10}")
    print(f"  {'-'*22} {'-'*12} {'-'*10} {'-'*10} {'-'*10}")
    
    for r in results:
        status = "✅ 成功" if r["success"] else f"❌ {r['error'][:15]}"
        action = r["action"] or "N/A"
        print(f"  {r['model']:<22} {r['latency_ms']:<12.0f} {r['latency_ms']/1000:<10.2f} {status:<10} {action:<10}")
    
    # 速度对比
    if len(results) == 2 and all(r["latency_ms"] > 0 for r in results):
        chat_ms = results[0]["latency_ms"]
        reasoner_ms = results[1]["latency_ms"]
        ratio = reasoner_ms / chat_ms if chat_ms > 0 else 0
        faster = "deepseek-chat" if chat_ms < reasoner_ms else "deepseek-reasoner"
        diff_ms = abs(chat_ms - reasoner_ms)
        
        print(f"\n  ⚡ 速度对比:")
        print(f"     {faster} 更快，快 {diff_ms:.0f}ms ({ratio:.1f}x)")
    
    print(f"\n  📝 结论建议:")
    print(f"     实盘交易中，API 延迟是关键因素。")
    print(f"     如果 reasoner 延迟 > 10s，建议实盘使用 deepseek-chat。")
    print(f"     如果 reasoner 延迟 < 5s 且决策质量明显更好，可考虑使用 reasoner。")
    print()


if __name__ == "__main__":
    asyncio.run(main())
