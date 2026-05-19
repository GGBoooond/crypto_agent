import tempfile
import unittest
from pathlib import Path

from evolution.skill_lifecycle import SkillLifecycleManager, SkillStage


class LifecycleTransitionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = str(Path(self.temp_dir.name) / "skill_lifecycle.json")
        self.manager = SkillLifecycleManager(state_path=self.state_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_strong_days_promote_to_active(self) -> None:
        for _ in range(3):
            item = self.manager.on_health("skill_a", 1.8, 0.2)
            item.last_health_date = ""
        self.assertEqual(self.manager.state["skill_a"].stage, SkillStage.ACTIVE)
        self.assertTrue(self.manager.state["skill_a"].transition_history)

    def test_weak_days_move_active_to_sunset(self) -> None:
        item = self.manager.ensure_skill("skill_b")
        item.stage = SkillStage.ACTIVE
        for _ in range(3):
            item = self.manager.on_health("skill_b", 0.5, -0.2)
            item.last_health_date = ""
        self.assertEqual(self.manager.state["skill_b"].stage, SkillStage.SUNSET)

    def test_patch_returns_to_patched_quarantine(self) -> None:
        item = self.manager.on_patch("skill_c")
        self.assertEqual(item.stage, SkillStage.PATCHED)
        self.assertEqual(item.weak_days, 0)
        self.assertTrue(item.quarantine_until)


if __name__ == "__main__":
    unittest.main()
