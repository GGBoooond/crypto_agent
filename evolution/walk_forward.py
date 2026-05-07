"""Simple walk-forward validator over recent traces."""
from typing import Dict, Iterable, List


class WalkForwardEngine:
    """Weekly validation placeholder for active skills."""

    def validate(self, traces: Iterable[Dict], skill_name: str, min_samples: int = 10) -> Dict[str, float]:
        rows: List[Dict] = [r for r in traces if r.get("skill_used") == skill_name and r.get("pnl") is not None]
        if len(rows) < min_samples:
            return {"passed": 0.0, "samples": float(len(rows)), "profit_factor": 0.0, "ic_proxy": 0.0}

        pnls = [float(r["pnl"]) for r in rows]
        wins = [x for x in pnls if x > 0]
        losses = [abs(x) for x in pnls if x < 0]
        pf = (sum(wins) / sum(losses)) if losses else 999.0
        win_rate = len(wins) / len(pnls)
        ic_proxy = win_rate - (1 - win_rate)
        passed = 1.0 if pf >= 1.2 and ic_proxy >= 0 else 0.0
        return {
            "passed": passed,
            "samples": float(len(rows)),
            "profit_factor": float(pf),
            "ic_proxy": float(ic_proxy),
        }

