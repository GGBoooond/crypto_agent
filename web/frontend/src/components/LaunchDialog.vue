<script setup lang="ts">
import { computed, reactive, watch } from "vue";

import type { BacktestMeta, LaunchRequest } from "@/types/backtest";

const props = defineProps<{
  open: boolean;
  meta: BacktestMeta | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  close: [];
  submit: [payload: LaunchRequest];
}>();

const today = new Date();
const weekAgo = new Date(today.getTime() - 7 * 24 * 3600 * 1000);

const form = reactive<LaunchRequest>({
  strategy: "",
  start: weekAgo.toISOString().slice(0, 10),
  end: today.toISOString().slice(0, 10),
  symbol: "DOGE/USDT:USDT",
  llm_mode: "replay",
  initial_balance: 10000,
  timeframe: "1m",
  output_dir: "reports",
});

watch(
  () => props.meta,
  (meta) => {
    if (!meta) {
      return;
    }
    form.strategy = form.strategy || meta.strategies[0] || "";
    form.symbol = meta.default_symbol;
    form.timeframe = meta.default_timeframe;
    form.llm_mode = meta.llm_modes[0] || "replay";
  },
  { immediate: true },
);

const canSubmit = computed(() => {
  return Boolean(form.strategy && form.start && form.end && form.symbol && form.timeframe);
});

function submit(): void {
  emit("submit", { ...form });
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/70 px-4">
    <div class="w-full max-w-2xl rounded-lg border border-slate-700 bg-cardbg p-5 shadow-2xl">
      <div class="mb-4 flex items-center justify-between">
        <h3 class="text-lg font-semibold">新建回测</h3>
        <button class="text-slate-400 hover:text-slate-200" @click="emit('close')">关闭</button>
      </div>
      <div class="grid grid-cols-2 gap-3 text-sm">
        <label class="space-y-1">
          <span class="text-slate-400">策略</span>
          <select v-model="form.strategy" class="w-full rounded border border-slate-700 bg-slate-900 p-2">
            <option v-for="item in meta?.strategies || []" :key="item" :value="item">
              {{ item }}
            </option>
          </select>
        </label>
        <label class="space-y-1">
          <span class="text-slate-400">交易对</span>
          <input v-model="form.symbol" class="w-full rounded border border-slate-700 bg-slate-900 p-2" />
        </label>
        <label class="space-y-1">
          <span class="text-slate-400">开始日期</span>
          <input v-model="form.start" type="date" class="w-full rounded border border-slate-700 bg-slate-900 p-2" />
        </label>
        <label class="space-y-1">
          <span class="text-slate-400">结束日期</span>
          <input v-model="form.end" type="date" class="w-full rounded border border-slate-700 bg-slate-900 p-2" />
        </label>
        <label class="space-y-1">
          <span class="text-slate-400">LLM 模式</span>
          <select v-model="form.llm_mode" class="w-full rounded border border-slate-700 bg-slate-900 p-2">
            <option v-for="mode in meta?.llm_modes || []" :key="mode" :value="mode">
              {{ mode }}
            </option>
          </select>
        </label>
        <label class="space-y-1">
          <span class="text-slate-400">时间周期</span>
          <select v-model="form.timeframe" class="w-full rounded border border-slate-700 bg-slate-900 p-2">
            <option v-for="tf in meta?.timeframe_options || []" :key="tf" :value="tf">
              {{ tf }}
            </option>
          </select>
        </label>
        <label class="space-y-1">
          <span class="text-slate-400">初始资金 (USDT)</span>
          <input
            v-model.number="form.initial_balance"
            type="number"
            min="1"
            class="w-full rounded border border-slate-700 bg-slate-900 p-2"
          />
        </label>
      </div>
      <div class="mt-5 flex justify-end gap-2">
        <button class="rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" @click="emit('close')">
          取消
        </button>
        <button
          :disabled="!canSubmit || loading"
          class="rounded bg-amber-500 px-3 py-2 text-sm font-medium text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
          @click="submit"
        >
          {{ loading ? "启动中..." : "启动回测" }}
        </button>
      </div>
    </div>
  </div>
</template>
