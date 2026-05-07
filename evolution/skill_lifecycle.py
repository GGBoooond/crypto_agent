"""Skill lifecycle with quarantine and sunset rules."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict


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


class SkillLifecycleManager:
    def __init__(self):
        self.state: Dict[str, SkillLifecycle] = {}

    def ensure_skill(self, name: str) -> SkillLifecycle:
        if name not in self.state:
            until = (datetime.utcnow() + timedelta(days=14)).isoformat()
            self.state[name] = SkillLifecycle(stage=SkillStage.QUARANTINE, quarantine_until=until)
        return self.state[name]

    def on_patch(self, name: str) -> SkillLifecycle:
        item = self.ensure_skill(name)
        item.stage = SkillStage.PATCHED
        item.quarantine_until = (datetime.utcnow() + timedelta(days=14)).isoformat()
        return item

    def on_health(self, name: str, profit_factor: float, ic_proxy: float) -> SkillLifecycle:
        item = self.ensure_skill(name)
        now = datetime.utcnow()
        if item.stage in (SkillStage.QUARANTINE, SkillStage.PATCHED):
            if item.quarantine_until and now >= datetime.fromisoformat(item.quarantine_until):
                item.stage = SkillStage.ACTIVE if profit_factor >= 1.5 else SkillStage.DISCARDED
            return item
        if item.stage == SkillStage.ACTIVE and (profit_factor < 1.0 or ic_proxy < 0.05):
            item.stage = SkillStage.SUNSET
            item.sunset_until = (now + timedelta(days=30)).isoformat()
        elif item.stage == SkillStage.SUNSET and item.sunset_until and now >= datetime.fromisoformat(item.sunset_until):
            item.stage = SkillStage.DISCARDED
        return item

