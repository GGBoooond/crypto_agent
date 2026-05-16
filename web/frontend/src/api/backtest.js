async function readJson(response) {
    if (!response.ok) {
        const payload = (await response.json().catch(() => null));
        throw new Error(payload?.detail || `HTTP ${response.status}`);
    }
    return (await response.json());
}
export async function fetchMeta() {
    const response = await fetch("/api/backtest/meta");
    return readJson(response);
}
export async function createRun(payload) {
    const response = await fetch("/api/backtest/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    return readJson(response);
}
export async function listRuns() {
    const response = await fetch("/api/backtest/runs");
    const payload = await readJson(response);
    return payload.runs;
}
export async function getRunResult(runId) {
    const response = await fetch(`/api/backtest/runs/${runId}`);
    return readJson(response);
}
export async function getRunSummary(runId) {
    const response = await fetch(`/api/backtest/runs/${runId}/summary`);
    return readJson(response);
}
export async function deleteRun(runId) {
    const response = await fetch(`/api/backtest/runs/${runId}`, { method: "DELETE" });
    await readJson(response);
}
export function downloadTradesCsv(runId) {
    window.open(`/api/backtest/runs/${runId}/trades.csv`, "_blank", "noopener");
}
export function connectRunProgress(runId, onEvent) {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/ws/backtest/runs/${runId}`);
    socket.onmessage = (message) => {
        const parsed = JSON.parse(message.data);
        onEvent(parsed);
    };
    socket.onerror = () => {
        onEvent({ type: "error", data: { message: "websocket disconnected" } });
    };
    return () => socket.close();
}
