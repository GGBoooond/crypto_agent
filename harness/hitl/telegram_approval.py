"""Telegram approval gate (minimal implementation)."""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ApprovalResult:
    approved: bool
    reason: str


class TelegramApprovalGate:
    """Stub gate to be connected with real telegram bot later."""

    def __init__(self, enabled: bool = False, max_auto_ratio: float = 0.2):
        self.enabled = enabled
        self.max_auto_ratio = max_auto_ratio

    def requires_approval(self, signal_payload: Dict, account_balance: float) -> bool:
        amount = float(signal_payload.get("amount") or 0)
        price = float(signal_payload.get("current_price") or 0)
        value = amount * price
        if account_balance <= 0:
            return True
        return (value / account_balance) > self.max_auto_ratio

    async def request(self, signal_payload: Dict, account_balance: float) -> ApprovalResult:
        if not self.enabled:
            return ApprovalResult(approved=True, reason="telegram gate disabled")
        if not self.requires_approval(signal_payload, account_balance):
            return ApprovalResult(approved=True, reason="below approval threshold")
        return ApprovalResult(approved=False, reason="manual approval required")

