"""Memory add/replace/remove with basic safety checks."""
from pathlib import Path
from typing import Literal


Target = Literal["memory", "user"]
Action = Literal["add", "replace", "remove"]


class MemoryTool:
    def __init__(
        self,
        memory_path: str = "memory/MEMORY.md",
        user_path: str = "memory/USER.md",
        memory_limit: int = 3000,
        user_limit: int = 1500,
    ):
        self.memory_path = Path(memory_path)
        self.user_path = Path(user_path)
        self.memory_limit = memory_limit
        self.user_limit = user_limit

    def _target_path(self, target: Target) -> Path:
        return self.memory_path if target == "memory" else self.user_path

    def _limit(self, target: Target) -> int:
        return self.memory_limit if target == "memory" else self.user_limit

    def _scan(self, content: str) -> None:
        blocked_keywords = ["guaranteed profit", "暴涨", "暴跌", "private key", "seed phrase"]
        lowered = content.lower()
        for keyword in blocked_keywords:
            if keyword.lower() in lowered:
                raise ValueError(f"unsafe memory content blocked: {keyword}")

    def apply(self, action: Action, target: Target, content: str = "", old_text: str = "") -> str:
        path = self._target_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8") if path.exists() else ""

        if action == "add":
            self._scan(content)
            updated = current.strip() + ("\n" if current.strip() else "") + f"- {content.strip()}\n"
        elif action == "replace":
            if old_text not in current:
                raise ValueError("old_text not found for replace")
            self._scan(content)
            updated = current.replace(old_text, content, 1)
        elif action == "remove":
            if old_text not in current:
                raise ValueError("old_text not found for remove")
            updated = current.replace(old_text, "", 1)
        else:
            raise ValueError(f"unsupported action: {action}")

        if len(updated) > self._limit(target):
            raise ValueError(f"memory limit exceeded for {target}")

        path.write_text(updated, encoding="utf-8")
        return updated

