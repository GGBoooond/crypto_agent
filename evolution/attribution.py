"""Offline skill attribution from recorded execution traces."""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_DEFAULT_DB_PATH = "memory/trades.db"
_DEFAULT_OUTPUT_PATH = "reports/skill_performance.csv"
_DEFAULT_REGIME_OUTPUT_PATH = "reports/regime_accuracy.csv"
_PERFORMANCE_TABLE = "skill_performance"
_REGIME_ACCURACY_TABLE = "skill_regime_accuracy"
_SUPPORTED_WINDOWS = {"5m", "30m", "4h"}
_CONFIDENCE_MAP = {"LOW": 0.3, "MEDIUM": 0.6, "HIGH": 0.9}


@dataclass
class SkillPerformance:
    skill_id: str
    regime: str
    fine_regime: str
    sample_size: int
    win_rate: float
    profit_factor: float
    avg_fwd_5m: float
    avg_fwd_30m: float
    avg_fwd_4h: float
    avg_holding_minutes: float
    max_drawdown: float
    sharpe_lite: float
    ic_pearson: float
    last_sample_at: str


@dataclass
class RegimeAccuracy:
    regime: str
    fine_regime: str
    predicted_up: int
    predicted_down: int
    actual_up: int
    actual_down: int
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    accuracy: float


class SkillAttributionJob:
    """Aggregate skill x regime performance for offline evolution."""

    def __init__(
        self,
        db_path: str = _DEFAULT_DB_PATH,
        output_path: str = _DEFAULT_OUTPUT_PATH,
        regime_output_path: str = _DEFAULT_REGIME_OUTPUT_PATH,
    ):
        self.db_path = Path(db_path)
        self.output_path = Path(output_path)
        self.regime_output_path = Path(regime_output_path)

    def run(
        self,
        persist_csv: bool = True,
        since: Optional[str] = None,
    ) -> List[SkillPerformance]:
        rows = self._load_rows(since=since)
        grouped = self._group_rows(rows)
        performance = [
            self._summarize_group(skill_id, regime, fine_regime, values)
            for (skill_id, regime, fine_regime), values in grouped.items()
        ]
        self._persist_table(performance)
        if persist_csv:
            self._persist_csv(performance)
        return performance

    def aggregate_fine_regime_accuracy(
        self,
        window: str = "30m",
        persist_csv: bool = True,
        since: Optional[str] = None,
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        if window not in _SUPPORTED_WINDOWS:
            raise ValueError(f"Unsupported window: {window}")
        rows = self._load_rows(since=since)
        grouped: Dict[Tuple[str, str], Dict[str, int]] = {}
        return_key = f"forward_return_{window}"
        for row in rows:
            actual = self._safe_float(row.get(return_key))
            predicted = self._predicted_direction(row)
            if actual is None or actual == 0 or predicted == 0:
                continue
            key = (
                str(row.get("regime") or "unknown"),
                str(row.get("fine_regime") or "unknown"),
            )
            bucket = grouped.setdefault(
                key,
                {
                    "predicted_up": 0,
                    "predicted_down": 0,
                    "actual_up": 0,
                    "actual_down": 0,
                    "true_positive": 0,
                    "true_negative": 0,
                    "false_positive": 0,
                    "false_negative": 0,
                },
            )
            predicted_up = predicted > 0
            actual_up = actual > 0
            bucket["predicted_up" if predicted_up else "predicted_down"] += 1
            bucket["actual_up" if actual_up else "actual_down"] += 1
            if predicted_up and actual_up:
                bucket["true_positive"] += 1
            elif (not predicted_up) and (not actual_up):
                bucket["true_negative"] += 1
            elif predicted_up and not actual_up:
                bucket["false_positive"] += 1
            else:
                bucket["false_negative"] += 1

        result: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for key, value in grouped.items():
            total = (
                value["true_positive"]
                + value["true_negative"]
                + value["false_positive"]
                + value["false_negative"]
            )
            payload: Dict[str, Any] = dict(value)
            payload["accuracy"] = round(
                (value["true_positive"] + value["true_negative"]) / total,
                4,
            ) if total else 0.0
            result[key] = payload
        self._persist_regime_accuracy(result)
        if persist_csv:
            self._persist_regime_csv(result)
        return result

    def _load_rows(self, since: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        self._ensure_trace_columns()
        query = """
            SELECT trace_id, timestamp, skill_used, regime, fine_regime,
                   forward_return_5m, forward_return_30m, forward_return_4h,
                   pnl, holding_minutes, side, llm_response, raw_payload
            FROM traces
            WHERE skill_used IS NOT NULL
        """
        params: Tuple[Any, ...] = ()
        if since:
            query += " AND timestamp >= ?"
            params = (since,)
        query += " ORDER BY timestamp ASC"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def _ensure_trace_columns(self) -> None:
        required = {
            "trace_id": "TEXT",
            "timestamp": "TEXT",
            "skill_used": "TEXT",
            "regime": "TEXT",
            "fine_regime": "TEXT",
            "forward_return_5m": "REAL",
            "forward_return_30m": "REAL",
            "forward_return_4h": "REAL",
            "pnl": "REAL",
            "holding_minutes": "REAL",
            "side": "TEXT",
            "llm_response": "TEXT",
            "raw_payload": "TEXT",
        }
        with sqlite3.connect(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "traces" not in tables:
                return
            existing = {row[1] for row in conn.execute("PRAGMA table_info(traces)")}
            for column, ddl_type in required.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE traces ADD COLUMN {column} {ddl_type}")

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
        returns_5m = self._returns(rows, "forward_return_5m")
        returns_30m = self._returns(rows, "forward_return_30m")
        returns_4h = self._returns(rows, "forward_return_4h")
        positives = [value for value in returns_30m if value > 0]
        negatives = [value for value in returns_30m if value < 0]
        holding = [self._safe_float(row.get("holding_minutes")) or 0.0 for row in rows]
        return SkillPerformance(
            skill_id=skill_id,
            regime=regime,
            fine_regime=fine_regime,
            sample_size=len(returns_30m),
            win_rate=round(len(positives) / len(returns_30m), 4) if returns_30m else 0.0,
            profit_factor=round(self._profit_factor(positives, negatives), 4),
            avg_fwd_5m=self._avg(returns_5m),
            avg_fwd_30m=self._avg(returns_30m),
            avg_fwd_4h=self._avg(returns_4h),
            avg_holding_minutes=round(sum(holding) / len(holding), 3) if holding else 0.0,
            max_drawdown=round(self._max_drawdown(returns_30m), 6),
            sharpe_lite=round(self._sharpe_lite(returns_30m), 6),
            ic_pearson=round(self._ic_pearson(rows), 6),
            last_sample_at=self._last_timestamp(rows),
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
                    sample_size INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    profit_factor REAL NOT NULL DEFAULT 0,
                    avg_fwd_5m REAL NOT NULL DEFAULT 0,
                    avg_fwd_30m REAL NOT NULL DEFAULT 0,
                    avg_fwd_4h REAL NOT NULL DEFAULT 0,
                    avg_holding_minutes REAL NOT NULL DEFAULT 0,
                    max_drawdown REAL NOT NULL DEFAULT 0,
                    sharpe_lite REAL NOT NULL DEFAULT 0,
                    ic_pearson REAL NOT NULL DEFAULT 0,
                    last_sample_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, regime, fine_regime)
                )
                """
            )
            self._migrate_performance_table(conn)
            updated_at = datetime.now(timezone.utc).isoformat()
            for item in performance:
                conn.execute(
                    f"""
                    INSERT OR REPLACE INTO {_PERFORMANCE_TABLE}
                    (skill_id, regime, fine_regime, sample_size, win_rate,
                     profit_factor, avg_fwd_5m, avg_fwd_30m, avg_fwd_4h,
                     avg_holding_minutes, max_drawdown, sharpe_lite,
                     ic_pearson, last_sample_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.skill_id,
                        item.regime,
                        item.fine_regime,
                        item.sample_size,
                        item.win_rate,
                        item.profit_factor,
                        item.avg_fwd_5m,
                        item.avg_fwd_30m,
                        item.avg_fwd_4h,
                        item.avg_holding_minutes,
                        item.max_drawdown,
                        item.sharpe_lite,
                        item.ic_pearson,
                        item.last_sample_at,
                        updated_at,
                    ),
                )

    def _migrate_performance_table(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({_PERFORMANCE_TABLE})")}
        migrations = {
            "avg_fwd_5m": "REAL NOT NULL DEFAULT 0",
            "avg_fwd_30m": "REAL NOT NULL DEFAULT 0",
            "avg_fwd_4h": "REAL NOT NULL DEFAULT 0",
            "avg_holding_minutes": "REAL NOT NULL DEFAULT 0",
            "max_drawdown": "REAL NOT NULL DEFAULT 0",
            "sharpe_lite": "REAL NOT NULL DEFAULT 0",
            "ic_pearson": "REAL NOT NULL DEFAULT 0",
            "last_sample_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl_type in migrations.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE {_PERFORMANCE_TABLE} ADD COLUMN {column} {ddl_type}"
                )

    def _persist_csv(self, performance: List[SkillPerformance]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(SkillPerformance.__annotations__))
            writer.writeheader()
            for item in performance:
                writer.writerow(asdict(item))

    def _persist_regime_accuracy(
        self,
        accuracy: Dict[Tuple[str, str], Dict[str, Any]],
    ) -> None:
        if not self.db_path.exists():
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_REGIME_ACCURACY_TABLE} (
                    regime TEXT NOT NULL,
                    fine_regime TEXT NOT NULL,
                    predicted_up INTEGER NOT NULL DEFAULT 0,
                    predicted_down INTEGER NOT NULL DEFAULT 0,
                    actual_up INTEGER NOT NULL DEFAULT 0,
                    actual_down INTEGER NOT NULL DEFAULT 0,
                    true_positive INTEGER NOT NULL DEFAULT 0,
                    true_negative INTEGER NOT NULL DEFAULT 0,
                    false_positive INTEGER NOT NULL DEFAULT 0,
                    false_negative INTEGER NOT NULL DEFAULT 0,
                    accuracy REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(regime, fine_regime)
                )
                """
            )
            updated_at = datetime.now(timezone.utc).isoformat()
            for (regime, fine_regime), payload in accuracy.items():
                conn.execute(
                    f"""
                    INSERT OR REPLACE INTO {_REGIME_ACCURACY_TABLE}
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        regime,
                        fine_regime,
                        payload["predicted_up"],
                        payload["predicted_down"],
                        payload["actual_up"],
                        payload["actual_down"],
                        payload["true_positive"],
                        payload["true_negative"],
                        payload["false_positive"],
                        payload["false_negative"],
                        payload["accuracy"],
                        updated_at,
                    ),
                )

    def _persist_regime_csv(
        self,
        accuracy: Dict[Tuple[str, str], Dict[str, Any]],
    ) -> None:
        self.regime_output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(RegimeAccuracy.__annotations__)
        with self.regime_output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for (regime, fine_regime), payload in sorted(accuracy.items()):
                writer.writerow(
                    {
                        "regime": regime,
                        "fine_regime": fine_regime,
                        **payload,
                    }
                )

    @staticmethod
    def _skill_ids(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, dict):
            for key in ("skill_id", "name", "id"):
                if value.get(key):
                    return [str(value[key]).strip()]
            return []
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            if isinstance(parsed, dict):
                return SkillAttributionJob._skill_ids(parsed)
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]

    @staticmethod
    def _returns(rows: List[Dict[str, Any]], key: str) -> List[float]:
        values = [SkillAttributionJob._safe_float(row.get(key)) for row in rows]
        return [value for value in values if value is not None]

    @staticmethod
    def _profit_factor(positives: List[float], negatives: List[float]) -> float:
        if positives and not negatives:
            return 999.0
        if negatives and not positives:
            return 0.0
        if not positives and not negatives:
            return 0.0
        return sum(positives) / abs(sum(negatives))

    @staticmethod
    def _avg(values: List[float]) -> float:
        return round(sum(values) / len(values), 6) if values else 0.0

    @staticmethod
    def _max_drawdown(values: List[float]) -> float:
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return max_drawdown

    @staticmethod
    def _sharpe_lite(values: List[float]) -> float:
        if len(values) < 5:
            return 0.0
        mean_value = sum(values) / len(values)
        variance = sum((value - mean_value) ** 2 for value in values) / len(values)
        std_dev = math.sqrt(variance)
        return mean_value / std_dev if std_dev > 0 else 0.0

    @staticmethod
    def _ic_pearson(rows: List[Dict[str, Any]]) -> float:
        pairs: List[Tuple[float, float]] = []
        for row in rows:
            signal = SkillAttributionJob._signal_direction(row)
            forward_return = SkillAttributionJob._safe_float(row.get("forward_return_30m"))
            if signal is not None and forward_return is not None:
                pairs.append((signal, forward_return))
        return SkillAttributionJob._pearson(pairs)

    @staticmethod
    def _pearson(pairs: List[Tuple[float, float]]) -> float:
        if len(pairs) < 2:
            return 0.0
        xs = [pair[0] for pair in pairs]
        ys = [pair[1] for pair in pairs]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
        var_x = sum((x - mean_x) ** 2 for x in xs)
        var_y = sum((y - mean_y) ** 2 for y in ys)
        denominator = math.sqrt(var_x * var_y)
        return cov / denominator if denominator > 0 else 0.0

    @staticmethod
    def _last_timestamp(rows: List[Dict[str, Any]]) -> str:
        timestamps = [str(row.get("timestamp") or "") for row in rows]
        return max(timestamps) if timestamps else ""

    @staticmethod
    def _signal_direction(row: Dict[str, Any]) -> Optional[float]:
        direction = SkillAttributionJob._predicted_direction(row)
        if direction == 0:
            return None
        confidence = SkillAttributionJob._confidence_numeric(row)
        return direction * confidence

    @staticmethod
    def _predicted_direction(row: Dict[str, Any]) -> int:
        side = str(row.get("side") or "").upper()
        if side in {"BUY", "LONG", "EXECUTE_LONG"}:
            return 1
        if side in {"SELL", "SHORT", "EXECUTE_SHORT"}:
            return -1
        payload = SkillAttributionJob._json_payload(row.get("llm_response"))
        action = str(payload.get("action") or "").upper()
        if "LONG" in action or action == "BUY":
            return 1
        if "SHORT" in action or action == "SELL":
            return -1
        return 0

    @staticmethod
    def _confidence_numeric(row: Dict[str, Any]) -> float:
        payload = SkillAttributionJob._json_payload(row.get("llm_response"))
        confidence = payload.get("confidence")
        if isinstance(confidence, (int, float)):
            return max(0.0, min(float(confidence), 1.0))
        return _CONFIDENCE_MAP.get(str(confidence or "MEDIUM").upper(), 0.6)

    @staticmethod
    def _json_payload(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
