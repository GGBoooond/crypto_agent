import type {
  BacktestMeta,
  BacktestResult,
  LaunchRequest,
  RunSummary,
  WsEvent,
} from "@/types/backtest";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchMeta(): Promise<BacktestMeta> {
  const response = await fetch("/api/backtest/meta");
  return readJson<BacktestMeta>(response);
}

export async function createRun(payload: LaunchRequest): Promise<{ run_id: string; status: string }> {
  const response = await fetch("/api/backtest/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson<{ run_id: string; status: string }>(response);
}

export async function listRuns(): Promise<RunSummary[]> {
  const response = await fetch("/api/backtest/runs");
  const payload = await readJson<{ runs: RunSummary[] }>(response);
  return payload.runs;
}

export async function getRunResult(runId: string): Promise<BacktestResult> {
  const response = await fetch(`/api/backtest/runs/${runId}`);
  return readJson<BacktestResult>(response);
}

export async function getRunSummary(runId: string): Promise<RunSummary> {
  const response = await fetch(`/api/backtest/runs/${runId}/summary`);
  return readJson<RunSummary>(response);
}

export async function deleteRun(runId: string): Promise<void> {
  const response = await fetch(`/api/backtest/runs/${runId}`, { method: "DELETE" });
  await readJson<{ status: string }>(response);
}

export function downloadTradesCsv(runId: string): void {
  window.open(`/api/backtest/runs/${runId}/trades.csv`, "_blank", "noopener");
}

export function connectRunProgress(runId: string, onEvent: (event: WsEvent) => void): () => void {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/backtest/runs/${runId}`);
  socket.onmessage = (message) => {
    const parsed = JSON.parse(message.data) as WsEvent;
    onEvent(parsed);
  };
  socket.onerror = () => {
    onEvent({ type: "error", data: { message: "websocket disconnected" } });
  };
  return () => socket.close();
}
