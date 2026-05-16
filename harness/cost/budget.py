"""Cost envelope manager for LLM calls."""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict


@dataclass
class BudgetCheckResult:
    allowed: bool
    reason: str
    remaining_daily_tokens: int
    remaining_call_tokens: int


class CostBudgetManager:
    """Simple in-memory token budget manager."""

    def __init__(self, daily_token_limit: int = 200_000, per_call_limit: int = 4_000):
        self.daily_token_limit = daily_token_limit
        self.per_call_limit = per_call_limit
        self._usage_by_date: Dict[str, int] = {}

    def check_before_call(self, expected_tokens: int) -> BudgetCheckResult:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        used = self._usage_by_date.get(today, 0)
        remaining_daily = max(self.daily_token_limit - used, 0)
        remaining_call = max(self.per_call_limit - expected_tokens, 0)

        if expected_tokens > self.per_call_limit:
            return BudgetCheckResult(
                allowed=False,
                reason="per-call token budget exceeded",
                remaining_daily_tokens=remaining_daily,
                remaining_call_tokens=remaining_call,
            )
        if used + expected_tokens > self.daily_token_limit:
            return BudgetCheckResult(
                allowed=False,
                reason="daily token budget exceeded",
                remaining_daily_tokens=remaining_daily,
                remaining_call_tokens=remaining_call,
            )
        return BudgetCheckResult(
            allowed=True,
            reason="budget passed",
            remaining_daily_tokens=remaining_daily,
            remaining_call_tokens=remaining_call,
        )

    def record_usage(self, used_tokens: int) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        self._usage_by_date[today] = self._usage_by_date.get(today, 0) + max(used_tokens, 0)

    def get_daily_usage(self) -> int:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return self._usage_by_date.get(today, 0)

