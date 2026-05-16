<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ConfigPanel from "@/components/ConfigPanel.vue";
import EquityChart from "@/components/EquityChart.vue";
import MetricsGrid from "@/components/MetricsGrid.vue";
import RunProgress from "@/components/RunProgress.vue";
import TradesTable from "@/components/TradesTable.vue";
import {
  connectRunProgress,
  deleteRun,
  downloadTradesCsv,
  getRunResult,
  getRunSummary,
} from "@/api/backtest";
import type { BacktestResult, RunSummary, WsEvent } from "@/types/backtest";

const route = useRoute();
const router = useRouter();

const runId = computed(() => String(route.params.runId || ""));
const summary = ref<RunSummary | null>(null);
const result = ref<BacktestResult | null>(null);
const loading = ref(true);
const error = ref("");
const activeTab = ref<"trades" | "config" | "raw">("trades");
let stopSocket: (() => void) | null = null;

const sourceLabel = computed(() => {
  const fallbackHits = Number(result.value?.config?.technical_fallback_hits || 0);
  const replayHits = Number(result.value?.config?.replay_hits || 0);
  if (fallbackHits > 0 && replayHits === 0) {
    return "Technical fallback";
  }
  if (replayHits > 0) {
    return "Replay trace";
  }
  return "Strategy analyze";
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    summary.value = await getRunSummary(runId.value);
    if (summary.value.status === "done") {
      result.value = await getRunResult(runId.value);
    }
  } catch (loadError) {
    error.value = loadError instanceof Error ? loadError.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

async function refreshResult(): Promise<void> {
  try {
    result.value = await getRunResult(runId.value);
    summary.value = await getRunSummary(runId.value);
  } catch (refreshError) {
    error.value = refreshError instanceof Error ? refreshError.message : "读取回测结果失败";
  }
}

function bindSocket(): void {
  if (stopSocket) {
    stopSocket();
  }
  stopSocket = connectRunProgress(runId.value, async (event: WsEvent) => {
    if (event.type === "snapshot" || event.type === "status") {
      summary.value = { ...(summary.value || {}), ...(event.data as RunSummary) } as RunSummary;
      return;
    }
    if (event.type === "progress") {
      const data = event.data as Partial<RunSummary>;
      summary.value = {
        ...(summary.value || {
          run_id: runId.value,
          status: "running",
          strategy_name: "",
          symbol: "",
          start: "",
          end: "",
        }),
        status: "running",
        progress: Number(data.progress || 0),
        index: Number(data.index || 0),
        total: Number(data.total || 0),
        equity: Number(data.equity || 0),
        trades: Number(data.trades || 0),
      };
      return;
    }
    if (event.type === "done") {
      await refreshResult();
      return;
    }
    if (event.type === "error") {
      const payload = event.data as { message?: string } | undefined;
      error.value = payload?.message || "运行失败";
      await load();
    }
  });
}

async function removeRun(): Promise<void> {
  if (!window.confirm(`确认删除回测 ${runId.value} 吗？`)) {
    return;
  }
  await deleteRun(runId.value);
  await router.push({ name: "runs" });
}

onMounted(async () => {
  await load();
  bindSocket();
});

watch(runId, async () => {
  await load();
  bindSocket();
});

onUnmounted(() => {
  stopSocket?.();
});
</script>

<template>
  <section class="space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-lg font-semibold text-slate-100">回测详情 · {{ runId }}</h2>
        <p class="text-xs text-slate-400">决策来源: {{ sourceLabel }}</p>
      </div>
      <div class="flex items-center gap-2">
        <button class="rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" @click="router.push({ name: 'runs' })">
          返回列表
        </button>
        <button
          :disabled="!result"
          class="rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
          @click="downloadTradesCsv(runId)"
        >
          下载 CSV
        </button>
        <button class="rounded bg-red-500 px-3 py-2 text-sm font-medium text-white" @click="removeRun">删除</button>
      </div>
    </div>

    <p v-if="error" class="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
      {{ error }}
    </p>

    <RunProgress :run="summary" />

    <div v-if="loading" class="rounded-lg border border-slate-700 bg-cardbg px-4 py-8 text-center text-sm text-slate-400">
      加载中...
    </div>

    <template v-else-if="result">
      <MetricsGrid :result="result" />
      <EquityChart :result="result" />

      <div class="rounded-lg border border-slate-700 bg-cardbg p-3">
        <div class="mb-3 flex items-center gap-2 text-sm">
          <button
            class="rounded px-3 py-2"
            :class="activeTab === 'trades' ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800'"
            @click="activeTab = 'trades'"
          >
            成交明细
          </button>
          <button
            class="rounded px-3 py-2"
            :class="activeTab === 'config' ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800'"
            @click="activeTab = 'config'"
          >
            配置
          </button>
          <button
            class="rounded px-3 py-2"
            :class="activeTab === 'raw' ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800'"
            @click="activeTab = 'raw'"
          >
            原始 JSON
          </button>
        </div>

        <TradesTable v-if="activeTab === 'trades'" :trades="result.trades" />
        <ConfigPanel v-else-if="activeTab === 'config'" :config="result.config" />
        <pre
          v-else
          class="max-h-[500px] overflow-auto rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300"
        >{{ JSON.stringify(result, null, 2) }}</pre>
      </div>
    </template>
  </section>
</template>
