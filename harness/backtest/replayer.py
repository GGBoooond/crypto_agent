"""Replay historical LLM outputs from trace sqlite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class LLMReplayer:
    """Lookup nearest historical LLM response by symbol/timestamp."""

    def __init__(self, db_path: str = "memory/trades.db"):
        self.db_path = Path(db_path)

    def find(
        self, symbol: str, ts: datetime, max_drift_seconds: int = 90
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        target = ts.isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT trace_id, timestamp, llm_response, prompt_hash, model_id
                FROM traces
                WHERE symbol = ?
                  AND llm_response IS NOT NULL
                ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC
                LIMIT 1
                """,
                (symbol, target),
            ).fetchone()
        if row is None:
            return None
        row_ts = self._parse_iso(row["timestamp"])
        if row_ts is None:
            return None
        if abs((row_ts - ts).total_seconds()) > max_drift_seconds:
            return None
        parsed_response = self._parse_llm_response(row["llm_response"])
        return {
            "trace_id": row["trace_id"],
            "timestamp": row["timestamp"],
            "llm_response": parsed_response,
            "prompt_hash": row["prompt_hash"],
            "model_id": row["model_id"],
        }

    @staticmethod
    def _parse_iso(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None

    @staticmethod
    def _parse_llm_response(text: Any) -> Dict[str, Any]:
        if text is None:
            return {}
        raw = str(text)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    return {"raw": raw}
            return {"raw": raw}
