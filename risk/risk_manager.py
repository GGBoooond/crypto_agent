"""Compatibility layer backed by harness policy gate."""

from harness.verification.policy_gate import (
    PolicyDecision as RiskDecision,
    PolicyGate as RiskManager,
    PolicyResult as RiskCheckResult,
)

__all__ = ["RiskManager", "RiskDecision", "RiskCheckResult"]
