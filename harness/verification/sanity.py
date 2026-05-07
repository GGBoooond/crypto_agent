"""Sanity checks for verified signals."""
from typing import Any, Dict, Optional

from core.message import Signal, SignalType

from .schema import VerificationResult


class SanityVerifier:
    """Rule-based sanity validation before risk checks."""

    def verify(
        self,
        signal: Signal,
        current_price: float,
        market_snapshot: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        if current_price <= 0:
            return VerificationResult(
                passed=False,
                stage="sanity",
                reason="invalid current price",
            )

        if signal.signal_type in (SignalType.BUY, SignalType.SELL):
            if signal.amount is not None and signal.amount <= 0:
                return VerificationResult(
                    passed=False,
                    stage="sanity",
                    reason="amount must be positive",
                )

            if signal.signal_type == SignalType.BUY and signal.stop_loss is not None:
                if signal.stop_loss >= current_price:
                    return VerificationResult(
                        passed=False,
                        stage="sanity",
                        reason="BUY stop_loss must be below current price",
                    )

            if signal.signal_type == SignalType.SELL and signal.stop_loss is not None:
                if signal.stop_loss <= current_price:
                    return VerificationResult(
                        passed=False,
                        stage="sanity",
                        reason="SELL stop_loss must be above current price",
                    )

            if signal.take_profit is not None and signal.stop_loss is not None:
                if signal.signal_type == SignalType.BUY and signal.take_profit <= signal.stop_loss:
                    return VerificationResult(
                        passed=False,
                        stage="sanity",
                        reason="BUY take_profit must be above stop_loss",
                    )
                if signal.signal_type == SignalType.SELL and signal.take_profit >= signal.stop_loss:
                    return VerificationResult(
                        passed=False,
                        stage="sanity",
                        reason="SELL take_profit must be below stop_loss",
                    )

            if market_snapshot:
                atr = float(market_snapshot.get("atr") or 0)
                if atr > 0 and signal.stop_loss is not None:
                    distance = abs(current_price - signal.stop_loss)
                    if distance > atr * 8:
                        return VerificationResult(
                            passed=False,
                            stage="sanity",
                            reason="stop_loss distance too large against ATR",
                            details={"distance": distance, "atr": atr},
                        )

        return VerificationResult(
            passed=True,
            stage="sanity",
            reason="sanity checks passed",
            signal=signal,
        )

