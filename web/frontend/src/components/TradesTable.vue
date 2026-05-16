<script setup lang="ts">
import { computed, ref } from "vue";

import type { TradeItem } from "@/types/backtest";

const props = defineProps<{ trades: TradeItem[] }>();

const pageSize = 20;
const page = ref(1);

const totalPages = computed(() => Math.max(1, Math.ceil(props.trades.length / pageSize)));
const currentPageTrades = computed(() => {
  const start = (page.value - 1) * pageSize;
  return props.trades.slice(start, start + pageSize);
});

function nextPage(): void {
  page.value = Math.min(totalPages.value, page.value + 1);
}

function prevPage(): void {
  page.value = Math.max(1, page.value - 1);
}
</script>

<template>
  <section class="rounded-lg border border-slate-700 bg-cardbg p-4">
    <div class="mb-3 flex items-center justify-between">
      <h3 class="text-sm font-semibold text-slate-200">成交明细</h3>
      <div class="text-xs text-slate-400">共 {{ trades.length }} 笔</div>
    </div>
    <div class="overflow-x-auto">
      <table class="min-w-full text-left text-xs">
        <thead class="text-slate-400">
          <tr>
            <th class="px-2 py-2">时间</th>
            <th class="px-2 py-2">方向</th>
            <th class="px-2 py-2">数量</th>
            <th class="px-2 py-2">价格</th>
            <th class="px-2 py-2">盈亏</th>
            <th class="px-2 py-2">手续费</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="trade in currentPageTrades" :key="trade.trade_id" class="border-t border-slate-800">
            <td class="px-2 py-2 text-slate-300">{{ trade.timestamp.replace("T", " ").slice(0, 19) }}</td>
            <td class="px-2 py-2">{{ trade.side }}</td>
            <td class="px-2 py-2">{{ trade.amount.toFixed(3) }}</td>
            <td class="px-2 py-2">{{ trade.price.toFixed(6) }}</td>
            <td class="px-2 py-2" :class="trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'">
              {{ trade.pnl.toFixed(4) }}
            </td>
            <td class="px-2 py-2 text-slate-400">{{ trade.fee.toFixed(4) }}</td>
          </tr>
          <tr v-if="!currentPageTrades.length">
            <td colspan="6" class="px-2 py-4 text-center text-slate-500">暂无成交数据</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="mt-3 flex items-center justify-end gap-2 text-xs">
      <button class="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" @click="prevPage">上一页</button>
      <span class="text-slate-400">{{ page }} / {{ totalPages }}</span>
      <button class="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" @click="nextPage">下一页</button>
    </div>
  </section>
</template>
