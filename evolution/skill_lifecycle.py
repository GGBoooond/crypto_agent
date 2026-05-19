"""Skill lifecycle with quarantine and sunset rules."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

from config import settings


_DEFAULT_QUARANTINE_DAYS = 14
_DEFAULT_SUNSET_DAYS = 30
_WEAK_PF_THRESHOLD = 0.8
_WEAK_DAYS_TO_SUNSET = 3


class SkillStage(str, Enum):
    QUARANTINE = "quarantine"
    ACTIVE = "active"
    PATCHED = "patched"
    SUNSET = "sunset"
    DISCARDED = "discarded"


@dataclass
class SkillLifecycle:
    stage: SkillStage = SkillStage.QUARANTINE
    quarantine_until: str = ""
    sunset_until: str = ""
    weak_days: int = 0
    strong_days: int = 0
    last_health_date: str = ""
    last_reviewed_at: str = ""
    profit_factor: float = 0.0
    ic_proxy: float = 0.0
    transition_history: List[Dict[str, Any]] = field(default_factory=list)


class SkillLifecycleManager:
    def __init__(self, state_path: str = "memory/skill_lifecycle.json"):
        self.state_path = Path(state_path)
        self.state: Dict[str, SkillLifecycle] = {}
        self._load()

    def ensure_skill(self, name: str) -> SkillLifecycle:
        if name not in self.state:
            until = self._now_plus_days(_DEFAULT_QUARANTINE_DAYS).isoformat()
            self.state[name] = SkillLifecycle(stage=SkillStage.QUARANTINE, quarantine_until=until)
            self._save()
        return self.state[name]

    def on_patch(self, name: str) -> SkillLifecycle:
        item = self.ensure_skill(name)
        old_stage = item.stage
        item.stage = SkillStage.PATCHED
        item.quarantine_until = (
            self._now_plus_days(_DEFAULT_QUARANTINE_DAYS)
        ).isoformat()
        item.weak_days = 0
        item.strong_days = 0
        self._record_transition(item, old_stage, item.stage, "patch_applied")
        self._save()
        return item

    def on_health(self, name: str, profit_factor: float, ic_proxy: float) -> SkillLifecycle:
        item = self.ensure_skill(name)
        now = datetime.now(timezone.utc)
        old_stage = item.stage
        self._record_health(item, profit_factor, ic_proxy, now)
        if (
            item.stage in (SkillStage.QUARANTINE, SkillStage.PATCHED)
            and item.strong_days >= settings.lifecycle_strong_days_to_active
        ):
            item.stage = SkillStage.ACTIVE
        if item.stage in (SkillStage.QUARANTINE, SkillStage.PATCHED):
            if item.quarantine_until and now >= self._parse_ts(item.quarantine_until):
                item.stage = SkillStage.ACTIVE if profit_factor >= 1.5 else SkillStage.DISCARDED
                self._record_transition(item, old_stage, item.stage, "quarantine_review")
                self._save()
            return item
        if item.stage == SkillStage.ACTIVE and item.weak_days >= _WEAK_DAYS_TO_SUNSET:
            item.stage = SkillStage.SUNSET
            item.sunset_until = (now + timedelta(days=_DEFAULT_SUNSET_DAYS)).isoformat()
        elif item.stage == SkillStage.SUNSET and item.sunset_until and now >= self._parse_ts(item.sunset_until):
            item.stage = SkillStage.DISCARDED
        self._record_transition(item, old_stage, item.stage, "health_review")
        self._save()
        return item

    def to_public_payload(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: self._to_payload(item)
            for name, item in sorted(self.state.items(), key=lambda pair: pair[0])
        }

    def _record_health(
        self,
        item: SkillLifecycle,
        profit_factor: float,
        ic_proxy: float,
        now: datetime,
    ) -> None:
        today = now.date().isoformat()
        item.profit_factor = float(profit_factor)
        item.ic_proxy = float(ic_proxy)
        item.last_reviewed_at = now.isoformat()
        if item.last_health_date == today:
            return
        item.last_health_date = today
        if profit_factor < _WEAK_PF_THRESHOLD:
            item.weak_days += 1
            item.strong_days = 0
        else:
            item.weak_days = 0
            item.strong_days = item.strong_days + 1 if profit_factor > 1.5 else 0

    def _record_transition(
        self,
        item: SkillLifecycle,
        old_stage: SkillStage,
        new_stage: SkillStage,
        reason: str,
    ) -> None:
        if old_stage == new_stage:
            return
        if item.transition_history is None:
            item.transition_history = []
        item.transition_history.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "from": old_stage.value,
                "to": new_stage.value,
                "reason": reason,
            }
        )
        item.transition_history = item.transition_history[-50:]

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, dict):
            return
        for name, payload in raw.items():
            if isinstance(payload, dict):
                self.state[name] = self._from_payload(payload)

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            name: self._to_payload(item)
            for name, item in sorted(self.state.items(), key=lambda pair: pair[0])
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _from_payload(payload: Dict[str, Any]) -> SkillLifecycle:
        try:
            stage = SkillStage(payload.get("stage", SkillStage.QUARANTINE.value))
        except ValueError:
            stage = SkillStage.QUARANTINE
        return SkillLifecycle(
            stage=stage,
            quarantine_until=str(payload.get("quarantine_until", "")),
            sunset_until=str(payload.get("sunset_until", "")),
            weak_days=int(payload.get("weak_days", 0) or 0),
            strong_days=int(payload.get("strong_days", 0) or 0),
            last_health_date=str(payload.get("last_health_date", "")),
            last_reviewed_at=str(payload.get("last_reviewed_at", "")),
            profit_factor=float(payload.get("profit_factor", 0.0) or 0.0),
            ic_proxy=float(payload.get("ic_proxy", 0.0) or 0.0),
            transition_history=list(payload.get("transition_history") or []),
        )

    @staticmethod
    def _to_payload(item: SkillLifecycle) -> Dict[str, Any]:
        payload = asdict(item)
        payload["stage"] = item.stage.value
        payload["transition_history"] = item.transition_history or []
        return payload

    @staticmethod
    def _now_plus_days(days: int) -> datetime:
        return datetime.now(timezone.utc) + timedelta(days=days)

    @staticmethod
    def _parse_ts(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

