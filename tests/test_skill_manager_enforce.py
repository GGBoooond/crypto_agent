import tempfile
import unittest
from pathlib import Path

from memory.skill_manage import PatchRejected, SkillManager


_SKILL = """---
name: sample
metadata:
  quant:
    regime: [ranging]
---

# Sample

## When to Use
- ranging

## Procedure
1. do the thing

## Pitfalls
- old pitfall
"""


class SkillManagerEnforceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = SkillManager(root=str(Path(self.temp_dir.name) / "skills"))
        self.manager.create("sample", _SKILL)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_patch_pitfalls_writes_changelog(self) -> None:
        path = self.manager.patch_with_audit(
            "sample",
            "- old pitfall",
            "- new pitfall",
            {"trace_id": "trace_1", "reviewer_a_score": 0.9, "reviewer_b_score": 0.8},
        )
        self.assertIn("- new pitfall", path.read_text(encoding="utf-8"))
        changelog = path.parent / "CHANGELOG.md"
        self.assertIn("trace_1", changelog.read_text(encoding="utf-8"))

    def test_patch_procedure_is_rejected(self) -> None:
        with self.assertRaises(PatchRejected):
            self.manager.patch("sample", "1. do the thing", "1. do another thing")

    def test_patch_frontmatter_is_rejected(self) -> None:
        with self.assertRaises(PatchRejected):
            self.manager.patch("sample", "regime: [ranging]", "regime: [trend]")


if __name__ == "__main__":
    unittest.main()
