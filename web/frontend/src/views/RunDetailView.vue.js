import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import ConfigPanel from "@/components/ConfigPanel.vue";
import EquityChart from "@/components/EquityChart.vue";
import MetricsGrid from "@/components/MetricsGrid.vue";
import RunProgress from "@/components/RunProgress.vue";
import TradesTable from "@/components/TradesTable.vue";
import { connectRunProgress, deleteRun, downloadTradesCsv, getRunResult, getRunSummary, } from "@/api/backtest";
const route = useRoute();
const router = useRouter();
const runId = computed(() => String(route.params.runId || ""));
const summary = ref(null);
const result = ref(null);
const loading = ref(true);
const error = ref("");
const activeTab = ref("trades");
let stopSocket = null;
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
async function load() {
    loading.value = true;
    error.value = "";
    try {
        summary.value = await getRunSummary(runId.value);
        if (summary.value.status === "done") {
            result.value = await getRunResult(runId.value);
        }
    }
    catch (loadError) {
        error.value = loadError instanceof Error ? loadError.message : "加载失败";
    }
    finally {
        loading.value = false;
    }
}
async function refreshResult() {
    try {
        result.value = await getRunResult(runId.value);
        summary.value = await getRunSummary(runId.value);
    }
    catch (refreshError) {
        error.value = refreshError instanceof Error ? refreshError.message : "读取回测结果失败";
    }
}
function bindSocket() {
    if (stopSocket) {
        stopSocket();
    }
    stopSocket = connectRunProgress(runId.value, async (event) => {
        if (event.type === "snapshot" || event.type === "status") {
            summary.value = { ...(summary.value || {}), ...event.data };
            return;
        }
        if (event.type === "progress") {
            const data = event.data;
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
            const payload = event.data;
            error.value = payload?.message || "运行失败";
            await load();
        }
    });
}
async function removeRun() {
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
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
__VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
    ...{ class: "space-y-4" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "flex items-center justify-between" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({
    ...{ class: "text-lg font-semibold text-slate-100" },
});
(__VLS_ctx.runId);
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "text-xs text-slate-400" },
});
(__VLS_ctx.sourceLabel);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "flex items-center gap-2" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.router.push({ name: 'runs' });
        } },
    ...{ class: "rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.downloadTradesCsv(__VLS_ctx.runId);
        } },
    disabled: (!__VLS_ctx.result),
    ...{ class: "rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.removeRun) },
    ...{ class: "rounded bg-red-500 px-3 py-2 text-sm font-medium text-white" },
});
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300" },
    });
    (__VLS_ctx.error);
}
/** @type {[typeof RunProgress, ]} */ ;
// @ts-ignore
const __VLS_0 = __VLS_asFunctionalComponent(RunProgress, new RunProgress({
    run: (__VLS_ctx.summary),
}));
const __VLS_1 = __VLS_0({
    run: (__VLS_ctx.summary),
}, ...__VLS_functionalComponentArgsRest(__VLS_0));
if (__VLS_ctx.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "rounded-lg border border-slate-700 bg-cardbg px-4 py-8 text-center text-sm text-slate-400" },
    });
}
else if (__VLS_ctx.result) {
    /** @type {[typeof MetricsGrid, ]} */ ;
    // @ts-ignore
    const __VLS_3 = __VLS_asFunctionalComponent(MetricsGrid, new MetricsGrid({
        result: (__VLS_ctx.result),
    }));
    const __VLS_4 = __VLS_3({
        result: (__VLS_ctx.result),
    }, ...__VLS_functionalComponentArgsRest(__VLS_3));
    /** @type {[typeof EquityChart, ]} */ ;
    // @ts-ignore
    const __VLS_6 = __VLS_asFunctionalComponent(EquityChart, new EquityChart({
        result: (__VLS_ctx.result),
    }));
    const __VLS_7 = __VLS_6({
        result: (__VLS_ctx.result),
    }, ...__VLS_functionalComponentArgsRest(__VLS_6));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "rounded-lg border border-slate-700 bg-cardbg p-3" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mb-3 flex items-center gap-2 text-sm" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!!(__VLS_ctx.loading))
                    return;
                if (!(__VLS_ctx.result))
                    return;
                __VLS_ctx.activeTab = 'trades';
            } },
        ...{ class: "rounded px-3 py-2" },
        ...{ class: (__VLS_ctx.activeTab === 'trades' ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800') },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!!(__VLS_ctx.loading))
                    return;
                if (!(__VLS_ctx.result))
                    return;
                __VLS_ctx.activeTab = 'config';
            } },
        ...{ class: "rounded px-3 py-2" },
        ...{ class: (__VLS_ctx.activeTab === 'config' ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800') },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!!(__VLS_ctx.loading))
                    return;
                if (!(__VLS_ctx.result))
                    return;
                __VLS_ctx.activeTab = 'raw';
            } },
        ...{ class: "rounded px-3 py-2" },
        ...{ class: (__VLS_ctx.activeTab === 'raw' ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800') },
    });
    if (__VLS_ctx.activeTab === 'trades') {
        /** @type {[typeof TradesTable, ]} */ ;
        // @ts-ignore
        const __VLS_9 = __VLS_asFunctionalComponent(TradesTable, new TradesTable({
            trades: (__VLS_ctx.result.trades),
        }));
        const __VLS_10 = __VLS_9({
            trades: (__VLS_ctx.result.trades),
        }, ...__VLS_functionalComponentArgsRest(__VLS_9));
    }
    else if (__VLS_ctx.activeTab === 'config') {
        /** @type {[typeof ConfigPanel, ]} */ ;
        // @ts-ignore
        const __VLS_12 = __VLS_asFunctionalComponent(ConfigPanel, new ConfigPanel({
            config: (__VLS_ctx.result.config),
        }));
        const __VLS_13 = __VLS_12({
            config: (__VLS_ctx.result.config),
        }, ...__VLS_functionalComponentArgsRest(__VLS_12));
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "max-h-[500px] overflow-auto rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300" },
        });
        (JSON.stringify(__VLS_ctx.result, null, 2));
    }
}
/** @type {__VLS_StyleScopedClasses['space-y-4']} */ ;
/** @type {__VLS_StyleScopedClasses['flex']} */ ;
/** @type {__VLS_StyleScopedClasses['items-center']} */ ;
/** @type {__VLS_StyleScopedClasses['justify-between']} */ ;
/** @type {__VLS_StyleScopedClasses['text-lg']} */ ;
/** @type {__VLS_StyleScopedClasses['font-semibold']} */ ;
/** @type {__VLS_StyleScopedClasses['text-slate-100']} */ ;
/** @type {__VLS_StyleScopedClasses['text-xs']} */ ;
/** @type {__VLS_StyleScopedClasses['text-slate-400']} */ ;
/** @type {__VLS_StyleScopedClasses['flex']} */ ;
/** @type {__VLS_StyleScopedClasses['items-center']} */ ;
/** @type {__VLS_StyleScopedClasses['gap-2']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-slate-700']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['hover:bg-slate-800']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-slate-700']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['hover:bg-slate-800']} */ ;
/** @type {__VLS_StyleScopedClasses['disabled:cursor-not-allowed']} */ ;
/** @type {__VLS_StyleScopedClasses['disabled:opacity-60']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-red-500']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['font-medium']} */ ;
/** @type {__VLS_StyleScopedClasses['text-white']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-red-800']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-red-950/40']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['text-red-300']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded-lg']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-slate-700']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-cardbg']} */ ;
/** @type {__VLS_StyleScopedClasses['px-4']} */ ;
/** @type {__VLS_StyleScopedClasses['py-8']} */ ;
/** @type {__VLS_StyleScopedClasses['text-center']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['text-slate-400']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded-lg']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-slate-700']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-cardbg']} */ ;
/** @type {__VLS_StyleScopedClasses['p-3']} */ ;
/** @type {__VLS_StyleScopedClasses['mb-3']} */ ;
/** @type {__VLS_StyleScopedClasses['flex']} */ ;
/** @type {__VLS_StyleScopedClasses['items-center']} */ ;
/** @type {__VLS_StyleScopedClasses['gap-2']} */ ;
/** @type {__VLS_StyleScopedClasses['text-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['px-3']} */ ;
/** @type {__VLS_StyleScopedClasses['py-2']} */ ;
/** @type {__VLS_StyleScopedClasses['max-h-[500px]']} */ ;
/** @type {__VLS_StyleScopedClasses['overflow-auto']} */ ;
/** @type {__VLS_StyleScopedClasses['rounded']} */ ;
/** @type {__VLS_StyleScopedClasses['border']} */ ;
/** @type {__VLS_StyleScopedClasses['border-slate-800']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-slate-950']} */ ;
/** @type {__VLS_StyleScopedClasses['p-3']} */ ;
/** @type {__VLS_StyleScopedClasses['text-xs']} */ ;
/** @type {__VLS_StyleScopedClasses['text-slate-300']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            ConfigPanel: ConfigPanel,
            EquityChart: EquityChart,
            MetricsGrid: MetricsGrid,
            RunProgress: RunProgress,
            TradesTable: TradesTable,
            downloadTradesCsv: downloadTradesCsv,
            router: router,
            runId: runId,
            summary: summary,
            result: result,
            loading: loading,
            error: error,
            activeTab: activeTab,
            sourceLabel: sourceLabel,
            removeRun: removeRun,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
