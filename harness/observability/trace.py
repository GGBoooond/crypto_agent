"""Execution trace recorder backed by SQLite + FTS5."""
import json
import sqlite3
import uuid
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class TraceRecorder:
    """Record strategy -> risk -> execution chain with trace ids."""

    # Columns added since the original schema. Append-only — never remove an
    # entry once it ships, otherwise we'd break old databases on upgrade.
    _ADDITIONAL_COLUMNS: Dict[str, str] = {
        "prompt_tokens": "INTEGER",
        "completion_tokens": "INTEGER",
        "kline_compression_ratio": "REAL",
    }

    def __init__(self, db_path: str = "memory/trades.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT,
                    regime TEXT,
                    skill_used TEXT,
                    stage TEXT,
                    llm_prompt TEXT,
                    llm_response TEXT,
                    verification_result TEXT,
                    risk_decision TEXT,
                    side TEXT,
                    qty REAL,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    holding_minutes REAL,
                    raw_payload TEXT
                )
                """
            )
            self._migrate_columns(conn)
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts
                USING fts5(trace_id, symbol, regime, skill_used, llm_prompt, llm_response, verification_result, risk_decision, raw_payload)
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS traces_ai AFTER INSERT ON traces
                BEGIN
                    INSERT INTO traces_fts(trace_id, symbol, regime, skill_used, llm_prompt, llm_response, verification_result, risk_decision, raw_payload)
                    VALUES (new.trace_id, new.symbol, new.regime, new.skill_used, new.llm_prompt, new.llm_response, new.verification_result, new.risk_decision, new.raw_payload);
                END
                """
            )

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(traces)").fetchall()}
        for column, ddl_type in self._ADDITIONAL_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE traces ADD COLUMN {column} {ddl_type}")

    def new_trace_id(self) -> str:
        return f"trace_{uuid.uuid4().hex}"

    @staticmethod
    def _json_default(value: Any) -> Any:
        """Convert non-JSON-native objects to serializable forms."""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return value.to_dict()
        if hasattr(value, "__dict__"):
            return value.__dict__
        return str(value)

    def record(self, payload: Dict[str, Any]) -> None:
        trace_id = payload.get("trace_id") or self.new_trace_id()
        now = datetime.utcnow().isoformat()
        timestamp = payload.get("timestamp", now)
        if isinstance(timestamp, (datetime, date)):
            timestamp = timestamp.isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces (
                    trace_id, timestamp, symbol, regime, skill_used, stage,
                    llm_prompt, llm_response, verification_result, risk_decision,
                    side, qty, entry_price, exit_price, pnl, holding_minutes,
                    raw_payload, prompt_tokens, completion_tokens, kline_compression_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    timestamp,
                    payload.get("symbol"),
                    payload.get("regime"),
                    payload.get("skill_used"),
                    payload.get("stage"),
                    payload.get("llm_prompt"),
                    payload.get("llm_response"),
                    payload.get("verification_result"),
                    payload.get("risk_decision"),
                    payload.get("side"),
                    payload.get("qty"),
                    payload.get("entry_price"),
                    payload.get("exit_price"),
                    payload.get("pnl"),
                    payload.get("holding_minutes"),
                    json.dumps(payload, ensure_ascii=True, default=self._json_default),
                    payload.get("prompt_tokens"),
                    payload.get("completion_tokens"),
                    payload.get("kline_compression_ratio"),
                ),
            )

    def search(self, query: str, limit: int = 20):
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT t.* FROM traces_fts f
                JOIN traces t ON t.trace_id = f.trace_id
                WHERE traces_fts MATCH ?
                ORDER BY t.timestamp DESC
                LIMIT ?
                """,
                (query, limit),
            )
            return [dict(row) for row in cur.fetchall()]
