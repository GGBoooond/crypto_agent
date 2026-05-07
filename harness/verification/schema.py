"""Schema verification for strategy signals."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ValidationError, field_validator

from core.message import Confidence, Signal, SignalType


class SignalVerificationInput(BaseModel):
    """Canonical shape used by verification pipeline."""

    signal_type: str
    symbol: str
    confidence: str
    reason: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    amount: Optional[float] = None
    strategy_name: str = "unknown"
    weight: float = 1.0
    metadata: Dict[str, Any] = {}

    @field_validator("signal_type")
    @classmethod
    def validate_signal_type(cls, value: str) -> str:
        SignalType(value)
        return value

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: str) -> str:
        Confidence(value)
        return value

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if value < 0:
            raise ValueError("weight must be >= 0")
        return value


@dataclass
class VerificationResult:
    """Unified verification result."""

    passed: bool
    stage: str
    reason: str
    signal: Optional[Signal] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


def parse_signal_dict(signal_data: Dict[str, Any]) -> VerificationResult:
    """Validate signal payload and convert to Signal object."""
    try:
        payload = SignalVerificationInput.model_validate(signal_data)
        signal = Signal(
            signal_type=SignalType(payload.signal_type),
            symbol=payload.symbol,
            confidence=Confidence(payload.confidence),
            reason=payload.reason,
            stop_loss=payload.stop_loss,
            take_profit=payload.take_profit,
            amount=payload.amount,
            strategy_name=payload.strategy_name,
            weight=payload.weight,
            metadata=payload.metadata,
        )
        return VerificationResult(
            passed=True,
            stage="schema",
            reason="schema validation passed",
            signal=signal,
        )
    except ValidationError as exc:
        return VerificationResult(
            passed=False,
            stage="schema",
            reason="schema validation failed",
            details={"errors": exc.errors()},
        )
    except Exception as exc:
        return VerificationResult(
            passed=False,
            stage="schema",
            reason=str(exc),
        )

