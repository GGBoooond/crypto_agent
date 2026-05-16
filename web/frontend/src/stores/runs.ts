import { defineStore } from "pinia";

import { connectRunProgress, listRuns } from "@/api/backtest";
import type { RunSummary, WsEvent } from "@/types/backtest";

type UnsubscribeMap = Record<string, () => void>;

export const useRunsStore = defineStore("runs", {
  state: () => ({
    runs: [] as RunSummary[],
    loading: false,
    error: "",
    subscriptions: {} as UnsubscribeMap,
  }),
  actions: {
    async refreshRuns(): Promise<void> {
      this.loading = true;
      this.error = "";
      try {
        this.runs = await listRuns();
      } catch (error) {
        this.error = error instanceof Error ? error.message : "failed to load runs";
      } finally {
        this.loading = false;
      }
    },
    upsertRun(summary: RunSummary): void {
      const index = this.runs.findIndex((run) => run.run_id === summary.run_id);
      if (index >= 0) {
        this.runs[index] = { ...this.runs[index], ...summary };
        return;
      }
      this.runs.unshift(summary);
    },
    subscribeRun(runId: string): void {
      if (this.subscriptions[runId]) {
        return;
      }
      const stop = connectRunProgress(runId, (event: WsEvent) => {
        const payload = (event.data || {}) as Partial<RunSummary> & { message?: string };
        if (event.type === "snapshot" || event.type === "status" || event.type === "done") {
          this.upsertRun(payload as RunSummary);
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
    unsubscribeRun(runId: string): void {
      const stop = this.subscriptions[runId];
      if (!stop) {
        return;
      }
      stop();
      delete this.subscriptions[runId];
    },
    disposeAllSubscriptions(): void {
      Object.values(this.subscriptions).forEach((stop) => stop());
      this.subscriptions = {};
    },
  },
});
