"""Checkpoint persistence for critical runtime status."""
import json
from pathlib import Path
from typing import Any, Dict


class CheckpointStore:
    def __init__(self, path: str = "memory/checkpoint.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

