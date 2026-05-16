"""Offline skill attribution from recorded execution traces."""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_DEFAULT_DB_PATH = "memory/trades.db"
_DEFAULT_OUTPUT_PATH = "reports/skill_performance.csv"
_PERFORMANCE_TABLE = "skill_performance"


@dataclass
class SkillPerformance:
    skill_id: str
    regime: str
    fine_regime: str
    sample_size: int
    win_rate: float
    profit_factor: float
    avg_forward_return: float
    avg_holding_minutes: float


class SkillAttributionJob:
    """Aggregate skill x regime performance for offline evolution."""

    def __init__(
        self,
        db_path: str = _DEFAULT_DB_PATH,
        output_path: str = _DEFAULT_OUTPUT_PATH,
    ):
        self.db_path = Path(db_path)
        self.output_path = Path(output_path)

    def run(self, persist_csv: bool = True) -> List[SkillPerformance]:
        rows = self._load_rows()
        grouped = self._group_rows(rows)
        performance = [
            self._summarize_group(skill_id, regime, fine_regime, values)
            for (skill_id, regime, fine_regime), values in grouped.items()
        ]
        self._persist_table(performance)
        if persist_csv:
            self._persist_csv(performance)
        return performance

    def _load_rows(self) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT skill_used, regime, fine_regime, forward_return_30m,
                       forward_return_5m, forward_return_4h, pnl, holding_minutes
                FROM traces
                WHERE skill_used IS NOT NULL
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def _group_rows(
        self, rows: Iterable[Dict[str, Any]]
    ) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
        grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            for skill_id in self._skill_ids(row.get("skill_used")):
                key = (
                    skill_id,
                    str(row.get("regime") or "unknown"),
                    str(row.get("fine_regime") or "unknown"),
                )
                grouped.setdefault(key, []).append(row)
        return grouped

    def _summarize_group(
        self,
        skill_id: str,
        regime: str,
        fine_regime: str,
        rows: List[Dict[str, Any]],
    ) -> SkillPerformance:
        returns = [self._forward_return(row) for row in rows]
        returns = [value for value in returns if value is not None]
        positives = [value for value in returns if value > 0]
        negatives = [value for value in returns if value < 0]
        profit_factor = sum(positives) / abs(sum(negatives)) if negatives else float(len(positives) > 0)
        holding = [self._safe_float(row.get("holding_minutes")) or 0.0 for row in rows]
        return SkillPerformance(
            skill_id=skill_id,
            regime=regime,
            fine_regime=fine_regime,
            sample_size=len(returns),
            win_rate=round(len(positives) / len(returns), 4) if returns else 0.0,
            profit_factor=round(profit_factor, 4),
            avg_forward_return=round(sum(returns) / len(returns), 6) if returns else 0.0,
            avg_holding_minutes=round(sum(holding) / len(holding), 3) if holding else 0.0,
        )

    def _persist_table(self, performance: List[SkillPerformance]) -> None:
        if not self.db_path.exists():
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_PERFORMANCE_TABLE} (
                    skill_id TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    fine_regime TEXT NOT NULL,
                    sample_size INTEGER,
                    win_rate REAL,
                    profit_factor REAL,
                    avg_forward_return REAL,
                    avg_holding_minutes REAL,
                    updated_at TEXT,
                    PRIMARY KEY(skill_id, regime, fine_regime)
                )
                """
            )
            for item in performance:
                conn.execute(
                    f"""
                    INSERT OR REPLACE INTO {_PERFORMANCE_TABLE}
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.skill_id,
                        item.regime,
                        item.fine_regime,
                        item.sample_size,
                        item.win_rate,
                        item.profit_factor,
                        item.avg_forward_return,
                        item.avg_holding_minutes,
                        datetime.utcnow().isoformat(),
                    ),
                )

    def _persist_csv(self, performance: List[SkillPerformance]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(SkillPerformance.__annotations__))
            writer.writeheader()
            for item in performance:
                writer.writerow(item.__dict__)

    @staticmethod
    def _skill_ids(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]

    @staticmethod
    def _forward_return(row: Dict[str, Any]) -> Optional[float]:
        for key in ("forward_return_30m", "forward_return_5m", "forward_return_4h", "pnl"):
            value = SkillAttributionJob._safe_float(row.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
