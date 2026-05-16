import { defineStore } from "pinia";
import { connectRunProgress, listRuns } from "@/api/backtest";
export const useRunsStore = defineStore("runs", {
    state: () => ({
        runs: [],
        loading: false,
        error: "",
        subscriptions: {},
    }),
    actions: {
        async refreshRuns() {
            this.loading = true;
            this.error = "";
            try {
                this.runs = await listRuns();
            }
            catch (error) {
                this.error = error instanceof Error ? error.message : "failed to load runs";
            }
            finally {
                this.loading = false;
            }
        },
        upsertRun(summary) {
            const index = this.runs.findIndex((run) => run.run_id === summary.run_id);
            if (index >= 0) {
                this.runs[index] = { ...this.runs[index], ...summary };
                return;
            }
            this.runs.unshift(summary);
        },
        subscribeRun(runId) {
            if (this.subscriptions[runId]) {
                return;
            }
            const stop = connectRunProgress(runId, (event) => {
                const payload = (event.data || {});
                if (event.type === "snapshot" || event.type === "status" || event.type === "done") {
                    this.upsertRun(payload);
                    return;
                }
                if (event.type === "progress") {
                    this.upsertRun({
                        run_id: runId,
                        status: "running",
                        progress: Number(payload.progress || 0),
                        index: Number(payload.index || 0),
                        total: Number(payload.total || 0),
                        equity: Number(payload.equity || 0),
                        trades: Number(payload.trades || 0),
                        strategy_name: "",
                        symbol: "",
                        start: "",
                        end: "",
                    });
                    return;
                }
                if (event.type === "error") {
                    this.upsertRun({
                        run_id: runId,
                        status: "failed",
                        error: payload.message || "run failed",
                        strategy_name: "",
                        symbol: "",
                        start: "",
                        end: "",
                    });
                }
            });
            this.subscriptions[runId] = stop;
        },
        unsubscribeRun(runId) {
            const stop = this.subscriptions[runId];
            if (!stop) {
                return;
            }
            stop();
            delete this.subscriptions[runId];
        },
        disposeAllSubscriptions() {
            Object.values(this.subscriptions).forEach((stop) => stop());
            this.subscriptions = {};
        },
    },
});
