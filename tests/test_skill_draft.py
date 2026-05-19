import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from evolution.postmortem import PostmortemEngine
from harness.context.prompt_builder import PromptBuilder
from memory.skill_manage import SkillManager
from web.app import create_app


_DRAFT_SKILL = """---
name: draft_skill
description: draft
metadata:
  quant:
    regime: [ranging]
---

# Draft

## When to Use
- ranging

## Procedure
- wait

## Pitfalls
- draft
"""


class SkillDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manager = SkillManager(root=str(self.root / "memory" / "skills"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prompt_builder_excludes_draft_skills(self) -> None:
        self.manager.create_draft("draft_skill", _DRAFT_SKILL)
        builder = PromptBuilder(memory_dir=str(self.root / "memory"))
        snapshot = builder.rebuild_static_snapshot(force=True)
        self.assertEqual(snapshot.skill_payloads, [])

    def test_web_promote_endpoint_moves_draft(self) -> None:
        self.manager.create_draft("draft_skill", _DRAFT_SKILL)
        cwd = Path.cwd()
        try:
            import os

            os.chdir(self.root)
            app = create_app()
            client = TestClient(app)
            response = client.post("/admin/skill_drafts/draft_skill/promote")
        finally:
            os.chdir(cwd)
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.root / "memory" / "skills" / "draft_skill" / "SKILL.md").exists())

    def test_postmortem_creates_draft_only_after_gate_passes(self) -> None:
        traces = [
            {
                "trace_id": f"trace_{index}",
                "regime": "ranging",
                "fine_regime": "range_bounce",
                "forward_return_30m": 0.02,
            }
            for index in range(30)
        ]
        engine = PostmortemEngine(
            skill_manager=self.manager,
            drafts_dir=str(self.root / "drafts"),
            dry_run=False,
        )
        drafts = engine.analyze_traces(traces)
        self.assertTrue(any(draft.kind == "skill_candidate" for draft in drafts))
        self.assertTrue(
            list((self.root / "memory" / "skills" / ".draft").glob("*/SKILL.md"))
        )


if __name__ == "__main__":
    unittest.main()
