<script setup lang="ts">
import { computed } from "vue";

import type { RunSummary } from "@/types/backtest";

const props = defineProps<{
  run: RunSummary | null;
}>();

const progressPct = computed(() => Math.min(100, Math.round((props.run?.progress || 0) * 100)));

const etaText = computed(() => {
  if (!props.run || props.run.status !== "running" || !props.run.index || !props.run.total || props.run.index <= 0) {
    return "--";
  }
  const startedAt = props.run.started_at ? Date.parse(props.run.started_at) : NaN;
  if (Number.isNaN(startedAt)) {
    return "--";
  }
  const elapsedSeconds = Math.max(1, Math.floor((Date.now() - startedAt) / 1000));
  const secondsPerStep = elapsedSeconds / props.run.index;
  const remaining = Math.max(0, Math.floor((props.run.total - props.run.index) * secondsPerStep));
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${minutes}m ${seconds}s`;
});
</script>

<template>
  <section class="rounded-lg border border-slate-700 bg-cardbg p-4">
    <div class="mb-2 flex items-center justify-between">
      <h3 class="text-sm font-semibold text-slate-200">运行进度</h3>
      <span class="text-xs text-slate-400">{{ run?.status || "unknown" }}</span>
    </div>
    <div class="h-2 w-full overflow-hidden rounded-full bg-slate-800">
      <div class="h-full bg-amber-400 transition-all duration-300" :style="{ width: `${progressPct}%` }" />
    </div>
    <div class="mt-2 flex justify-between text-xs text-slate-400">
      <span>{{ run?.index || 0 }} / {{ run?.total || 0 }}</span>
      <span>{{ progressPct }}%</span>
      <span>ETA: {{ etaText }}</span>
    </div>
  </section>
</template>
