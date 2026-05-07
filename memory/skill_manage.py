"""Skill CRUD with progressive discovery."""
from pathlib import Path
from typing import Dict, List, Optional


class SkillManager:
    def __init__(self, root: str = "memory/skills"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".archive").mkdir(parents=True, exist_ok=True)

    def skills_list(self) -> List[Dict[str, str]]:
        items = []
        for skill_md in self.root.glob("*/SKILL.md"):
            items.append({"name": skill_md.parent.name, "path": str(skill_md)})
        return sorted(items, key=lambda x: x["name"])

    def skill_view(self, name: str, relative_path: Optional[str] = None) -> str:
        base = self.root / name
        target = base / "SKILL.md" if not relative_path else base / relative_path
        if not target.exists():
            raise FileNotFoundError(target)
        return target.read_text(encoding="utf-8")

    def create(self, name: str, content: str) -> Path:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        return path

    def patch(self, name: str, old_string: str, new_string: str) -> Path:
        path = self.root / name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        if old_string not in text:
            raise ValueError("old_string not found")
        path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return path

    def edit(self, name: str, content: str) -> Path:
        path = self.root / name / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        return path

    def delete(self, name: str) -> None:
        src = self.root / name
        if not src.exists():
            return
        dst = self.root / ".archive" / name
        if dst.exists():
            for file in dst.rglob("*"):
                if file.is_file():
                    file.unlink()
        src.rename(dst)

