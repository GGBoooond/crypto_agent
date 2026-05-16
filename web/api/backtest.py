"""Backtest REST and WebSocket endpoints."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from config import settings
from harness.backtest import BacktestConfig
from strategies import StrategyFactory

from .run_registry import run_registry


class LaunchBacktestRequest(BaseModel):
    strategy: str = Field(min_length=1)
    start: str
    end: str
    symbol: str = Field(min_length=1)
    llm_mode: Literal["replay", "rerun"] = "replay"
    initial_balance: float = Field(default=10000.0, gt=0.0)
    timeframe: str = Field(default="1m", min_length=1)
    output_dir: str = Field(default="reports", min_length=1)


def _parse_iso(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid {field_name}: {value}") from exc


def _sanitize_non_finite(value: Any) -> Any:
    """Convert NaN/Infinity to None to keep JSON compliant."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _sanitize_non_finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_non_finite(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_non_finite(item) for item in value]
    return value


def _safe_json_response(payload: Any) -> JSONResponse:
    return JSONResponse(_sanitize_non_finite(payload))


def register_backtest_routes(app: FastAPI) -> None:
    """Register backtest endpoints on a FastAPI app."""

    @app.get("/api/backtest/meta")
    async def backtest_meta():
        return _safe_json_response(
            {
                "strategies": StrategyFactory.list_available(),
                "default_symbol": settings.trading_symbol,
                "default_timeframe": settings.trading_timeframe,
                "llm_modes": ["replay", "rerun"],
                "timeframe_options": ["1m", "3m", "5m", "15m", "1h", "4h", "1d"],
            }
        )

    @app.post("/api/backtest/runs")
    async def create_backtest_run(payload: LaunchBacktestRequest):
        if payload.strategy not in StrategyFactory.list_available():
            raise HTTPException(status_code=400, detail=f"unknown strategy: {payload.strategy}")
        config = BacktestConfig(
            strategy_name=payload.strategy,
            start=_parse_iso(payload.start, "start"),
            end=_parse_iso(payload.end, "end"),
            symbol=payload.symbol,
            llm_mode=payload.llm_mode,
            initial_balance=payload.initial_balance,
            timeframe=payload.timeframe,
            output_dir=payload.output_dir,
        )
        if config.end <= config.start:
            raise HTTPException(status_code=422, detail="end must be greater than start")
        try:
            run_id = await run_registry.create(config)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _safe_json_response({"run_id": run_id, "status": "queued"})

    @app.get("/api/backtest/runs")
    async def list_backtest_runs(
        status: Optional[str] = Query(default=None),
        strategy: Optional[str] = Query(default=None),
        symbol: Optional[str] = Query(default=None),
    ):
        rows = run_registry.list_runs(status=status, strategy=strategy, symbol=symbol)
        return _safe_json_response({"runs": rows})

    @app.get("/api/backtest/runs/{run_id}")
    async def get_backtest_result(run_id: str):
        try:
            result = run_registry.get_result(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _safe_json_response(result)

    @app.get("/api/backtest/runs/{run_id}/summary")
    async def get_backtest_summary(run_id: str):
        summary = run_registry.get_summary(run_id)
        if not summary:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return _safe_json_response(summary)

    @app.get("/api/backtest/runs/{run_id}/trades.csv")
    async def download_backtest_trades(run_id: str):
        try:
            csv_path = run_registry.get_trades_csv_path(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path=csv_path, media_type="text/csv", filename=csv_path.name)

    @app.delete("/api/backtest/runs/{run_id}")
    async def delete_backtest_run(run_id: str):
        try:
            run_registry.delete(run_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _safe_json_response({"status": "deleted", "run_id": run_id})

    @app.websocket("/ws/backtest/runs/{run_id}")
    async def backtest_run_ws(websocket: WebSocket, run_id: str):
        await websocket.accept()
        queue = run_registry.subscribe(run_id)
        try:
            summary = run_registry.get_summary(run_id)
            if summary:
                await websocket.send_json(_sanitize_non_finite({"type": "snapshot", "data": summary}))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    await websocket.send_json(_sanitize_non_finite(event))
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "heartbeat"})
        except WebSocketDisconnect:
            return
        finally:
            run_registry.unsubscribe(run_id, queue)
