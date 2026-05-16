"""Skill health metrics."""
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable


@dataclass
class SkillHealth:
    trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        return self.gross_win / self.gross_loss if self.gross_loss > 0 else 999.0


class SkillHealthTracker:
    def compute(self, traces: Iterable[Dict]) -> Dict[str, SkillHealth]:
        result: Dict[str, SkillHealth] = defaultdict(SkillHealth)
        for row in traces:
            skill = row.get("skill_used")
            pnl = row.get("pnl")
            if not skill or pnl is None:
                continue
            pnl = float(pnl)
            health = result[skill]
            health.trades += 1
            health.total_pnl += pnl
            if pnl > 0:
                health.wins += 1
                health.gross_win += pnl
            elif pnl < 0:
                health.gross_loss += abs(pnl)
        return dict(result)

