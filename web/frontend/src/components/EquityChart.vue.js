import * as echarts from "echarts";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
const props = defineProps();
const chartRef = ref(null);
let chart = null;
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
function renderChart() {
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
                    formatter: (value) => `${(value * 100).toFixed(0)}%`,
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
watch(() => props.result, () => {
    renderChart();
}, { deep: true });
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
__VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
    ...{ class: "rounded-lg border border-slate-700 bg-cardbg p-4" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h3, __VLS_intrinsicElements.h3)({
    ...{ class: "mb-3 text-sm font-semibold text-slate-200" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div)({
    ref: "chartRef",
    ...{ class: "h-80 w-full" },
});
/** @type {typeof __VLS_ctx.chartRef} */ ;
/** @type {__VLS_StyleScopedClasses['rounded-lg']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-slate-700']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-cardbg']} */ ;
/** @type {__VLS_StyleScopedClasses['p-4']} */ ;
/** @type {__VLS_StyleScopedClasses['mb-3']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['font-semibold']} */ ;
/** @type {__VLS_StyleScopedClasses['text-slate-200']} */ ;
/** @type {__VLS_StyleScopedClasses['h-80']} */ ;
/** @type {__VLS_StyleScopedClasses['w-full']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            chartRef: chartRef,
        };
    },
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
