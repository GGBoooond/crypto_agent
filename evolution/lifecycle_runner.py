"""Apply offline attribution metrics to skill lifecycle state."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict

from .skill_lifecycle import SkillLifecycleManager


def apply_attribution_to_lifecycle(
    db_path: str = "memory/trades.db",
    state_path: str = "memory/skill_lifecycle.json",
) -> Dict[str, Dict[str, float]]:
    path = Path(db_path)
    if not path.exists():
        return {}
    rows = _load_performance_rows(path)
    manager = SkillLifecycleManager(state_path=state_path)
    result: Dict[str, Dict[str, float]] = {}
    for skill_id, payload in rows.items():
        samples = payload["samples"]
        if samples <= 0:
            continue
        profit_factor = payload["weighted_pf"] / samples
        ic_pearson = payload["weighted_ic"] / samples
        manager.on_health(skill_id, profit_factor, ic_pearson)
        result[skill_id] = {
            "profit_factor": profit_factor,
            "ic_pearson": ic_pearson,
            "samples": float(samples),
        }
    return result


def _load_performance_rows(path: Path) -> Dict[str, Dict[str, float]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT skill_id, sample_size, profit_factor, ic_pearson
            FROM skill_performance
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
    grouped: Dict[str, Dict[str, float]] = {}
    for row in rows:
        skill_id = str(row.get("skill_id") or "")
        if not skill_id:
            continue
        sample_size = float(row.get("sample_size") or 0.0)
        bucket = grouped.setdefault(
            skill_id,
            {"samples": 0.0, "weighted_pf": 0.0, "weighted_ic": 0.0},
        )
        bucket["samples"] += sample_size
        bucket["weighted_pf"] += float(row.get("profit_factor") or 0.0) * sample_size
        bucket["weighted_ic"] += float(row.get("ic_pearson") or 0.0) * sample_size
    return grouped
