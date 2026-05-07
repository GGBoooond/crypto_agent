---
name: ranging_mean_revert
description: 震荡区间均值回归模板
version: 0.1.0
metadata:
  quant:
    regime: [ranging]
    sample_size: 0
---

# Ranging Mean Revert

## When to Use
- ranging
- RSI 超买超卖后回归

## Procedure
1. 下轨做多上轨做空
2. 快速止损
3. 小目标止盈

## Pitfalls
- 趋势行情禁用

## Verification
- ADX > 25 时拒绝本 skill

