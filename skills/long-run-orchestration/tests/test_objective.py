"""End-to-end evaluation: verified execution, review and pending approval."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import FakeKanban, base_spec

from lro_objective import (Divergence, apply_objective, evaluate, load_state,
                           save_state, verify_exit_code)
from lro_spec import validate


def criteria(criterion_id: str) -> list:
    return [{"id": criterion_id, "met": True, "proof": "verified output"}]


def work_meta(step_id: str) -> dict:
    criterion = {"work-a": "a1", "work-b": "b1", "consolidate": "c1"}[step_id]
    return {"notes": "executed", "acceptance": criteria(criterion)}


def review_meta() -> dict:
    return {"notes": "reviewed", "acceptance": criteria("r1"),
            "verdict": "approve", "reviewer_model": "reviewer-model"}


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)
        self.state_dir = self.base / ".lro"
        self.board = FakeKanban()
        spec = base_spec()
        self.spec = validate(spec, self.base)
        apply_objective(self.spec, self.base, self.board, self.state_dir, emit=lambda *_: None)
        self.cards = load_state(self.state_dir)["cards"]

    def tearDown(self):
        self.tmp.cleanup()

    def complete_work(self):
        for step_id in ("work-a", "work-b"):
            self.board.complete(self.cards[step_id], result="done",
                                metadata=work_meta(step_id))
        self.board.complete(self.cards["review"], result="approved",
                            metadata=review_meta())
        self.board.complete(self.cards["consolidate"], result="consolidated",
                            metadata=work_meta("consolidate"))

    def test_verified_execution_waits_for_human_approval(self):
        self.complete_work()
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        self.assertEqual(report["state"], "awaiting_approval")
        self.assertEqual(verify_exit_code(report), 2)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertTrue(rows["work-a"]["verified"])
        self.assertTrue(rows["review"]["verified"])
        self.assertFalse(rows["approval"]["executed"])

    def test_approval_closes_the_objective(self):
        self.complete_work()
        self.board.complete(self.cards["approval"], result="approved")
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        self.assertEqual(report["state"], "closed")
        self.assertEqual(verify_exit_code(report), 0)

    def test_declared_result_without_evidence_is_not_verified(self):
        self.board.complete(self.cards["work-a"], result="done", metadata={})
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertTrue(rows["work-a"]["executed"])
        self.assertFalse(rows["work-a"]["verified"])
        self.assertTrue(any(p.startswith("evidence") for p in rows["work-a"]["problems"]))
        self.assertEqual(report["state"], "in_progress")
        self.assertEqual(verify_exit_code(report), 1)

    def test_review_in_progress_is_not_approval(self):
        self.board.complete(self.cards["work-a"], result="done",
                            metadata=work_meta("work-a"))
        self.board.cards[self.cards["review"]]["status"] = "review"
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        self.assertNotIn(report["state"], ("awaiting_approval", "closed"))
        self.assertNotEqual(verify_exit_code(report), 0)

    def test_state_from_another_objective_is_rejected(self):
        state = load_state(self.state_dir)
        state["objective_id"] = "other-objective"
        save_state(self.state_dir, state)
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        self.assertEqual(report["state"], "drift")
        self.assertNotEqual(verify_exit_code(report), 0)
        self.assertTrue(any("objective" in problem for problem in report["problems"]))

    def test_missing_card_is_drift_not_an_exception(self):
        del self.board.cards[self.cards["work-a"]]
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        self.assertEqual(report["state"], "drift")
        self.assertNotEqual(verify_exit_code(report), 0)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertIn("card missing", rows["work-a"]["problems"])

    def test_corrupt_ledger_is_drift_not_not_applied(self):
        (self.state_dir / "objective.json").write_text("{not json", encoding="utf-8")
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        self.assertEqual(report["state"], "drift")
        self.assertNotEqual(verify_exit_code(report), 0)
        self.assertTrue(any("unreadable" in problem for problem in report["problems"]))

    def test_report_is_json_serializable(self):
        report = evaluate(self.spec, self.base, self.board, self.state_dir)
        json.dumps(report)

    def test_lost_state_cannot_rebuild_children_under_a_done_spec(self):
        # Keep the native cards (spec is already done) but lose the local
        # ledger. Re-apply must not recreate children under an open gate.
        spec_card = self.cards["spec"]
        for card_id in list(self.board.cards):
            if card_id != spec_card:
                del self.board.cards[card_id]
        self.board.by_key = {key: card_id for key, card_id in self.board.by_key.items()
                             if card_id == spec_card}
        (self.state_dir / "objective.json").unlink()
        with self.assertRaises(Divergence) as caught:
            apply_objective(self.spec, self.base, self.board, self.state_dir,
                            emit=lambda *_: None)
        self.assertIn("spec gate", str(caught.exception))
        self.assertEqual(set(self.board.cards), {spec_card})
        self.assertEqual(load_state(self.state_dir)["cards"], {"spec": spec_card})


if __name__ == "__main__":
    unittest.main()
