"""Health checks and loop watchdog."""
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class AgentHealth:
    status: str
    reason: str
    checked_at: str


class HealthMonitor:
    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds
        self._last_progress_at = datetime.utcnow()

    def mark_progress(self) -> None:
        self._last_progress_at = datetime.utcnow()

    def check(self) -> AgentHealth:
        now = datetime.utcnow()
        if now - self._last_progress_at > timedelta(seconds=self.timeout_seconds):
            return AgentHealth(
                status="degraded",
                reason="no progress heartbeat",
                checked_at=now.isoformat(),
            )
        return AgentHealth(status="healthy", reason="ok", checked_at=now.isoformat())

