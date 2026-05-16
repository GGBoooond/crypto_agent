<script setup lang="ts">
import type { BacktestResult } from "@/types/backtest";

defineProps<{ result: BacktestResult }>();

function asPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}
</script>

<template>
  <section class="grid grid-cols-2 gap-3 md:grid-cols-3">
    <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
      <div class="text-xs text-slate-400">总交易数</div>
      <div class="mt-1 text-xl font-semibold">{{ result.total_trades }}</div>
    </div>
    <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
      <div class="text-xs text-slate-400">胜率</div>
      <div class="mt-1 text-xl font-semibold">{{ asPercent(result.win_rate) }}</div>
    </div>
    <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
      <div class="text-xs text-slate-400">Profit Factor</div>
      <div class="mt-1 text-xl font-semibold">{{ result.profit_factor.toFixed(3) }}</div>
    </div>
    <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
      <div class="text-xs text-slate-400">Sharpe</div>
      <div class="mt-1 text-xl font-semibold">{{ result.sharpe.toFixed(3) }}</div>
    </div>
    <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
      <div class="text-xs text-slate-400">Sortino</div>
      <div class="mt-1 text-xl font-semibold">{{ result.sortino.toFixed(3) }}</div>
    </div>
    <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
      <div class="text-xs text-slate-400">最大回撤</div>
      <div class="mt-1 text-xl font-semibold text-red-400">{{ asPercent(result.max_drawdown) }}</div>
    </div>
  </section>
</template>
