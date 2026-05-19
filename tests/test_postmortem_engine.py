import json
import tempfile
import unittest
from pathlib import Path

from evolution.postmortem import PostmortemEngine
from memory.skill_manage import SkillManager


class FakeReviewerClient:
    is_configured = True

    def __init__(self, labels):
        self.labels = list(labels)

    def chat_json_sync(self, messages, *, temperature=0.0, max_tokens=500):
        return self.labels.pop(0)


class PostmortemEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.skill_manager = SkillManager(root=str(root / "skills"))
        self.skill_manager.create(
            "sample_skill",
            "# Sample\n\n## When to Use\n- ranging\n\n## Procedure\n1. wait\n\n## Pitfalls\n- old\n",
        )
        self.drafts_dir = root / "drafts"
        self.review_dir = root / "review"
        self.shocks_path = root / "shocks.log"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_routes_market_shock_to_shocks_log(self) -> None:
        reviewer = FakeReviewerClient(
            [{"category": "market_shock", "confidence": 0.9, "evidence": "forward_return_5m"}]
        )
        engine = PostmortemEngine(
            skill_manager=self.skill_manager,
            reviewer_client=reviewer,
            drafts_dir=str(self.drafts_dir),
            review_dir=str(self.review_dir),
            shocks_path=str(self.shocks_path),
            dry_run=False,
            db_path=str(Path(self.temp_dir.name) / "missing.db"),
        )
        drafts = engine.analyze_traces(
            [
                {
                    "trace_id": "trace_1",
                    "symbol": "DOGE",
                    "forward_return_5m": -0.08,
                    "forward_return_30m": -0.08,
                }
            ]
        )
        self.assertEqual(drafts[0].kind, "market_shock")
        self.assertIn("trace_1", self.shocks_path.read_text(encoding="utf-8"))

    def test_routes_regime_misjudge_to_memory_draft(self) -> None:
        reviewer = FakeReviewerClient(
            [{"category": "regime_misjudge", "confidence": 0.8, "evidence": "regime"}]
        )
        engine = PostmortemEngine(
            skill_manager=self.skill_manager,
            reviewer_client=reviewer,
            drafts_dir=str(self.drafts_dir),
            dry_run=False,
            db_path=str(Path(self.temp_dir.name) / "missing.db"),
        )
        drafts = engine.analyze_traces(
            [{"trace_id": "trace_2", "forward_return_30m": -0.02, "regime": "ranging"}]
        )
        self.assertEqual(drafts[0].kind, "memory_add")
        self.assertTrue(list(self.drafts_dir.glob("memory_*.md")))

    def test_fixture_accuracy_is_at_least_70_percent(self) -> None:
        fixture = Path("tests/fixtures/postmortem_labels.json")
        labels = json.loads(fixture.read_text(encoding="utf-8"))
        engine = PostmortemEngine(dry_run=True)
        correct = 0
        for item in labels:
            predicted = engine._deterministic_label(item["trace"])["category"]
            if predicted == item["category"]:
                correct += 1
        self.assertGreaterEqual(correct / len(labels), 0.7)


if __name__ == "__main__":
    unittest.main()
