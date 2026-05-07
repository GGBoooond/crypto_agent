---
name: breakout_pullback_long
description: 突破回踩多头入场模板
version: 0.1.0
metadata:
  quant:
    regime: [strong_trend_up]
    sample_size: 0
---

# Breakout Pullback Long

## When to Use
- strong_trend_up
- 放量突破后回踩

## Procedure
1. 回踩确认后开仓
2. 固定止损
3. 分批止盈

## Pitfalls
- 低流动性时段跳过

## Verification
- 触发后若成交量不足则不执行

