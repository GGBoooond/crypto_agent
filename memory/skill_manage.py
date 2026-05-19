"""Skill CRUD with progressive discovery."""
import difflib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class PatchRejected(ValueError):
    """Raised when an automated skill patch touches a protected section."""


class SkillManager:
    def __init__(self, root: str = "memory/skills"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".archive").mkdir(parents=True, exist_ok=True)
        (self.root / ".draft").mkdir(parents=True, exist_ok=True)

    def skills_list(self) -> List[Dict[str, str]]:
        items = []
        for skill_md in self.root.glob("*/SKILL.md"):
            if skill_md.parent.name.startswith("."):
                continue
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
        return self.patch_with_audit(name, old_string, new_string, audit={})

    def patch_with_audit(
        self,
        name: str,
        old_string: str,
        new_string: str,
        audit: Dict[str, Any],
    ) -> Path:
        path = self.root / name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        if old_string not in text:
            raise ValueError("old_string not found")
        self._assert_section_whitelist(text, old_string, new_string)
        updated = text.replace(old_string, new_string, 1)
        path.write_text(updated, encoding="utf-8")
        self._append_changelog(name, text, updated, audit)
        return path

    def _assert_section_whitelist(
        self,
        text: str,
        old_string: str,
        new_string: str,
    ) -> None:
        start = text.index(old_string)
        section = self._section_name_at(text, start)
        if section not in {"Pitfalls", "When to Use"}:
            raise PatchRejected(
                "automated patch can only edit ## Pitfalls or ## When to Use"
            )
        forbidden_markers = ("metadata:", "## Procedure", "## Verification")
        if any(marker in new_string for marker in forbidden_markers):
            raise PatchRejected("automated patch attempted to change protected content")

    @staticmethod
    def _section_name_at(text: str, index: int) -> str:
        before = text[:index]
        section_line = ""
        for line in before.splitlines():
            if line.startswith("## "):
                section_line = line
        current_line = text[index : text.find("\n", index) if "\n" in text[index:] else len(text)]
        if current_line.startswith("## "):
            section_line = current_line
        return section_line.replace("## ", "", 1).strip()

    def _append_changelog(
        self,
        name: str,
        old_text: str,
        new_text: str,
        audit: Dict[str, Any],
    ) -> None:
        changelog = self.root / name / "CHANGELOG.md"
        diff = "\n".join(
            difflib.unified_diff(
                old_text.splitlines(),
                new_text.splitlines(),
                fromfile="before/SKILL.md",
                tofile="after/SKILL.md",
                lineterm="",
            )
        )
        entry = (
            f"\n## {datetime.now(timezone.utc).isoformat()}\n\n"
            f"trace_id: {audit.get('trace_id', '')}\n"
            f"reviewer_a_score: {audit.get('reviewer_a_score', '')}\n"
            f"reviewer_b_score: {audit.get('reviewer_b_score', '')}\n\n"
            "```diff\n"
            f"{diff}\n"
            "```\n"
        )
        if changelog.exists():
            existing = changelog.read_text(encoding="utf-8")
        else:
            existing = "# Skill Changelog\n"
        changelog.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")

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

    def create_draft(self, name: str, content: str) -> Path:
        directory = self.root / ".draft" / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        return path

    def list_drafts(self) -> List[Dict[str, str]]:
        draft_root = self.root / ".draft"
        if not draft_root.exists():
            return []
        items = [
            {"name": skill_md.parent.name, "path": str(skill_md)}
            for skill_md in draft_root.glob("*/SKILL.md")
        ]
        return sorted(items, key=lambda item: item["name"])

    def promote_draft(self, name: str) -> Path:
        src = self.root / ".draft" / name
        dst = self.root / name
        if not src.exists():
            raise FileNotFoundError(src)
        if dst.exists():
            raise FileExistsError(dst)
        shutil.move(str(src), str(dst))
        return dst / "SKILL.md"

    def reject_draft(self, name: str) -> Path:
        src = self.root / ".draft" / name
        dst = self.root / ".archive" / f"draft_{name}"
        if not src.exists():
            raise FileNotFoundError(src)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        return dst

