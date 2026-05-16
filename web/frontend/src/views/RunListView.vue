<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";

import LaunchDialog from "@/components/LaunchDialog.vue";
import { createRun, fetchMeta } from "@/api/backtest";
import { useRunsStore } from "@/stores/runs";
import type { BacktestMeta, LaunchRequest } from "@/types/backtest";

const router = useRouter();
const runsStore = useRunsStore();

const showDialog = ref(false);
const creating = ref(false);
const meta = ref<BacktestMeta | null>(null);
const actionError = ref("");

const runs = computed(() => runsStore.runs);

async function refresh(): Promise<void> {
  await runsStore.refreshRuns();
  runs.value
    .filter((run) => run.status === "running" || run.status === "queued")
    .forEach((run) => runsStore.subscribeRun(run.run_id));
}

async function openDialog(): Promise<void> {
  actionError.value = "";
  if (!meta.value) {
    meta.value = await fetchMeta();
  }
  showDialog.value = true;
}

async function submit(payload: LaunchRequest): Promise<void> {
  creating.value = true;
  actionError.value = "";
  try {
    const run = await createRun(payload);
    runsStore.subscribeRun(run.run_id);
    showDialog.value = false;
    await refresh();
    await router.push({ name: "run-detail", params: { runId: run.run_id } });
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "启动失败";
  } finally {
    creating.value = false;
  }
}

function pct(value?: number): string {
  if (value === undefined || value === null) {
    return "--";
  }
  return `${(value * 100).toFixed(2)}%`;
}

onMounted(async () => {
  await refresh();
});

onUnmounted(() => {
  runsStore.disposeAllSubscriptions();
});
</script>

<template>
  <section class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-slate-200">回测任务列表</h2>
      <div class="flex items-center gap-2">
        <button class="rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" @click="refresh">
          刷新
        </button>
        <button class="rounded bg-amber-500 px-3 py-2 text-sm font-medium text-slate-900" @click="openDialog">
          新建回测
        </button>
      </div>
    </div>
    <p v-if="runsStore.error || actionError" class="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
      {{ runsStore.error || actionError }}
    </p>
    <div class="overflow-x-auto rounded-lg border border-slate-700 bg-cardbg">
      <table class="min-w-full text-left text-sm">
        <thead class="border-b border-slate-700 text-xs text-slate-400">
          <tr>
            <th class="px-3 py-3">Run ID</th>
            <th class="px-3 py-3">策略</th>
            <th class="px-3 py-3">交易对</th>
            <th class="px-3 py-3">区间</th>
            <th class="px-3 py-3">状态</th>
            <th class="px-3 py-3">胜率</th>
            <th class="px-3 py-3">Sharpe</th>
            <th class="px-3 py-3">最大回撤</th>
            <th class="px-3 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.run_id" class="border-b border-slate-800 text-xs">
            <td class="px-3 py-3 font-mono text-slate-300">{{ run.run_id }}</td>
            <td class="px-3 py-3">{{ run.strategy_name || "--" }}</td>
            <td class="px-3 py-3">{{ run.symbol || "--" }}</td>
            <td class="px-3 py-3">{{ run.start?.slice(0, 10) }} ~ {{ run.end?.slice(0, 10) }}</td>
            <td class="px-3 py-3">
              <div class="space-y-1">
                <span
                  class="rounded px-2 py-1"
                  :class="{
                    'bg-amber-500/20 text-amber-300': run.status === 'running' || run.status === 'queued',
                    'bg-green-500/20 text-green-300': run.status === 'done',
                    'bg-red-500/20 text-red-300': run.status === 'failed',
                  }"
                >
                  {{ run.status }}
                </span>
                <div v-if="run.status === 'running' || run.status === 'queued'" class="h-1 w-20 rounded bg-slate-800">
                  <div class="h-1 rounded bg-amber-400" :style="{ width: `${Math.round((run.progress || 0) * 100)}%` }" />
                </div>
              </div>
            </td>
            <td class="px-3 py-3">{{ pct(run.win_rate) }}</td>
            <td class="px-3 py-3">{{ run.sharpe?.toFixed(3) || "--" }}</td>
            <td class="px-3 py-3 text-red-300">{{ pct(run.max_drawdown) }}</td>
            <td class="px-3 py-3">
              <button class="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" @click="router.push({ name: 'run-detail', params: { runId: run.run_id } })">
                查看
              </button>
            </td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="9" class="px-4 py-8 text-center text-sm text-slate-500">
              {{ runsStore.loading ? "加载中..." : "暂无回测记录" }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <LaunchDialog :open="showDialog" :meta="meta" :loading="creating" @close="showDialog = false" @submit="submit" />
  </section>
</template>
