<script setup lang="ts">
import * as echarts from "echarts";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { BacktestResult } from "@/types/backtest";

const props = defineProps<{ result: BacktestResult }>();

const chartRef = ref<HTMLElement | null>(null);
let chart: echarts.ECharts | null = null;

const drawdownSeries = computed(() => {
  let peak = Number.NEGATIVE_INFINITY;
  return props.result.equity_curve.map((point) => {
    peak = Math.max(peak, point.equity);
    if (peak <= 0) {
      return 0;
    }
    return -((peak - point.equity) / peak);
  });
});

function renderChart(): void {
  if (!chartRef.value) {
    return;
  }
  if (!chart) {
    chart = echarts.init(chartRef.value);
  }
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: ["Equity", "Drawdown"], textStyle: { color: "#cbd5e1" } },
    grid: { left: 50, right: 50, top: 36, bottom: 50 },
    xAxis: {
      type: "category",
      data: props.result.equity_curve.map((item) => item.timestamp.slice(5, 16)),
      axisLabel: { color: "#94a3b8", hideOverlap: true },
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { color: "#94a3b8" },
        splitLine: { lineStyle: { color: "#334155" } },
      },
      {
        type: "value",
        min: -1,
        max: 0,
        axisLabel: {
          color: "#94a3b8",
          formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
        },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "Equity",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: props.result.equity_curve.map((item) => item.equity),
        lineStyle: { color: "#f59e0b", width: 2 },
      },
      {
        name: "Drawdown",
        type: "line",
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: drawdownSeries.value,
        lineStyle: { color: "#ef4444", width: 1.5 },
      },
    ],
  });
}

onMounted(() => {
  renderChart();
  window.addEventListener("resize", renderChart);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", renderChart);
  chart?.dispose();
  chart = null;
});

watch(
  () => props.result,
  () => {
    renderChart();
  },
  { deep: true },
);
</script>

<template>
  <section class="rounded-lg border border-slate-700 bg-cardbg p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">资金曲线与回撤</h3>
    <div ref="chartRef" class="h-80 w-full" />
  </section>
</template>
