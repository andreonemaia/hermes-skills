"""Graph construction and resume tests with an observable native-adapter double.

These are controlled simulations, not integrations with the live gateway.

Native facts the double encodes (verified against the installed CLI):

* ``create`` accepts repeated ``--parent`` flags and ``create_task`` inserts
  the task row and every link inside one write transaction, so a successful
  create never leaves an unlinked child behind;
* ``initial_task_state`` parks a child in ``todo`` while any parent is not
  ``done``, and ``--initial-status blocked`` wins and appends a sticky
  ``blocked`` event;
* ``recompute_ready`` promotes ``todo``/``blocked`` only when every parent is
  ``done``/``archived`` and the block is not sticky; a card with no parents is
  eligible, so an unlinked child would be dispatched immediately.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import FakeKanban, base_spec

import lro_spec
from lro_contract import HUMAN_APPROVAL_ASSIGNEE
from lro_objective import Divergence, apply_objective, load_state, save_state, state_path


class GraphTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.base = self.tmp
        self.state_dir = self.tmp / "state"
        self.spec = lro_spec.validate(base_spec(), self.base)
        self.board = FakeKanban()

    def tearDown(self):
        self._tmp.cleanup()

    def apply(self, spec=None, board=None, emit=lambda *_: None):
        return apply_objective(spec or self.spec, self.base, board or self.board,
                               self.state_dir, emit=emit)

    def seed(self, step_id, parents=(), initial_status=None):
        step = next(s for s in self.spec["steps"] if s["id"] == step_id)
        return self.board.create(
            title=f"[{self.spec['objective_id']}] {step['title']}",
            body=lro_spec.render_body(self.spec, step, base_dir=self.base),
            idempotency_key=f"lro:{self.spec['objective_id']}:{step_id}",
            parents=list(parents),
            initial_status=initial_status,
        )["id"]

    def write_state(self, cards):
        save_state(self.state_dir, {
            "schema_version": 1,
            "objective_id": self.spec["objective_id"],
            "spec_sha256": self.spec["spec_sha256"],
            "cards": cards,
            "applied_at": "2026-10-19T00:00:00+00:00",
        })

    def assert_inert(self, board):
        """Every existing card is the sticky spec gate or hangs from it."""
        spec_card = board.find("spec")
        self.assertIsNotNone(spec_card)
        self.assertEqual(board.cards[spec_card]["status"], "blocked")
        self.assertEqual(board.eligible_steps(), [])
        for card in board.cards.values():
            if card["id"] == spec_card:
                continue
            self.assertIn(spec_card, board.ancestors(card["id"]),
                          f"{card['id']} is not gated behind the spec card")

    def test_success_builds_all_cards_and_links_before_completing_spec(self):
        self.apply()
        self.assertEqual(len(self.board.cards), len(self.spec["steps"]))
        self.assertEqual(self.board.complete_calls, 1)
        self.assertEqual(self.board.cards[self.board.find("spec")]["status"], "done")
        self.assertEqual(self.board.parents_of("work-a"), [self.board.find("spec")])
        self.assertEqual(self.board.parents_of("work-b"), [self.board.find("work-a")])
        self.assertEqual(self.board.parents_of("review"),
                         [self.board.find("work-a"), self.board.find("work-b")])
        self.assertEqual(self.board.parents_of("approval"),
                         [self.board.find("consolidate")])
        # Native status resolution at creation time: children wait in todo,
        # the spec and the human gate are born sticky-blocked.
        expected_status = {
            "spec": "blocked",
            "work-a": "todo",
            "work-b": "todo",
            "review": "todo",
            "consolidate": "todo",
            "approval": "blocked",
        }
        self.assertEqual({step: state["status"]
                          for step, state in self.board.created_states.items()},
                         expected_status)
        self.assertEqual(self.board.created_states["work-a"]["parents"],
                         [self.board.find("spec")])
        self.assertEqual(self.board.created_states["work-b"]["parents"],
                         [self.board.find("work-a")])
        # No card is executable at any observed point during construction.
        for step, eligible in self.board.snapshots:
            self.assertEqual(eligible, [], f"{step} released {eligible} too early")
        # Spec is completed last: no complete call before every child exists.
        first_complete = next(i for i, call in enumerate(self.board.calls)
                              if call[0] == "complete")
        created_steps = {call[1] for call in self.board.calls[:first_complete]
                         if call[0] == "create"}
        self.assertEqual(created_steps, {s["id"] for s in self.spec["steps"]})
        # Every child is created with its parents in the same native call.
        # There is no create-then-link window for a child of this objective.
        by_step = {s["id"]: s for s in self.spec["steps"]}
        expected_parents = {
            "spec": [],
            "work-a": [self.board.find("spec")],
            "work-b": [self.board.find("work-a")],
            "review": [self.board.find("work-a"), self.board.find("work-b")],
            "consolidate": [self.board.find("review")],
            "approval": [self.board.find("consolidate")],
        }
        for call in self.board.calls:
            if call[0] == "create":
                self.assertEqual(call[2]["parents"], expected_parents[call[1]])
        # Closing the gate is what releases the first work card.
        self.assertEqual(self.board.eligible_steps(), ["work-a"])
        # The approval gate stays blocked and owned by the non-profile
        # sentinel, so no dispatcher can spawn an agent for it.
        approval = self.board.find("approval")
        self.assertEqual(self.board.cards[approval]["status"], "blocked")
        self.assertFalse(self.board.can_execute(approval))
        approval_create = next(call for call in self.board.calls
                               if call[0] == "create" and call[1] == "approval")
        self.assertEqual(approval_create[2]["assignee"], HUMAN_APPROVAL_ASSIGNEE)

    def test_can_execute_rule_matches_native_states(self):
        # The fake derives eligibility from status, stickiness and parent
        # completion, not from the graph the implementation wants.
        spec_id = self.seed("spec", initial_status="blocked")
        work_a = self.seed("work-a", [spec_id])
        work_b = self.seed("work-b", [work_a])
        self.assertFalse(self.board.can_execute(work_a))  # parent blocked
        self.assertFalse(self.board.can_execute(work_b))  # parent incomplete
        self.board.complete(spec_id, result="ok")
        self.assertTrue(self.board.can_execute(work_a))   # ready, parents done
        self.assertFalse(self.board.can_execute(work_b))  # still incomplete
        self.board.complete(work_a, result="ok")
        self.assertTrue(self.board.can_execute(work_b))

        # An explicit sticky block wins even when every parent is done.
        consolidate = self.seed("consolidate")
        self.board.complete(consolidate, result="ok")
        approval = self.seed("approval", [consolidate], initial_status="blocked")
        self.assertEqual(self.board.cards[approval]["status"], "blocked")
        self.assertFalse(self.board.can_execute(approval))

        # A card with no parents is dispatchable: this is why every child of
        # the objective must be created with its gate in the same call.
        orphan = self.seed("review")
        self.assertTrue(self.board.can_execute(orphan))

    def test_dependencies_point_forward_and_block_transitively(self):
        self.apply()
        spec_id = self.board.find("spec")
        work_a = self.board.find("work-a")
        work_b = self.board.find("work-b")
        review = self.board.find("review")
        consolidate = self.board.find("consolidate")
        approval = self.board.find("approval")
        # Direction: a child depends on its predecessor, never the reverse.
        self.assertIn(work_a, self.board.ancestors(work_b))
        self.assertNotIn(work_b, self.board.ancestors(work_a))
        self.assertIn(work_b, self.board.ancestors(review))
        self.assertIn(work_a, self.board.ancestors(review))
        # Transitivity: the gate blocks every descendant.
        self.assertIn(spec_id, self.board.ancestors(consolidate))
        self.assertIn(spec_id, self.board.ancestors(approval))
        self.assertIn(consolidate, self.board.ancestors(approval))

    def test_interruption_at_each_create_point_resumes_without_duplicates(self):
        # The native adapter is atomic per create, but the objective spans
        # several calls. Interrupt at every call boundary and resume from the
        # durable state; no card may be duplicated or released early.
        for step in ("spec", "work-a", "work-b", "review", "consolidate", "approval"):
            with self.subTest(step=step):
                self.board = FakeKanban()
                self.state_dir = self.tmp / f"state-{step}"
                self.board.interrupt_after_create_step = step
                with self.assertRaises(Exception):
                    self.apply()
                self.assertEqual(self.board.cards[self.board.find("spec")]["status"],
                                 "blocked")
                self.assertEqual(self.board.complete_calls, 0)
                self.assertEqual(self.board.eligible_steps(), [])
                self.board.interrupt_after_create_step = None
                result = self.apply()
                self.assertEqual(len(self.board.cards), len(self.spec["steps"]))
                self.assertEqual(len(set(self.board.by_key.values())),
                                 len(self.spec["steps"]))
                self.assertEqual(result["created"] + result["reused"],
                                 len(self.spec["steps"]))
                self.assertEqual(self.board.complete_calls, 1)
                self.assertEqual(self.board.eligible_steps(), ["work-a"])

    def test_external_release_before_parent_completion_stops_apply(self):
        self.apply()
        work_a_id = self.board.find("work-a")
        work_b_id = self.board.find("work-b")
        assert work_a_id is not None and work_b_id is not None
        work_a = self.board.cards[work_a_id]
        work_b = self.board.cards[work_b_id]
        # An external actor promoted work-b while work-a is still open. The
        # adapter cannot un-dispatch a running card, so apply must stop with a
        # diagnostic instead of linking or pretending the graph is healthy.
        work_a["status"] = "todo"
        work_b["status"] = "ready"
        before = {key: dict(card) for key, card in self.board.cards.items()}
        with self.assertRaises(Divergence) as caught:
            self.apply()
        self.assertIn("released before", " ".join(caught.exception.problems))
        for key, card in self.board.cards.items():
            self.assertEqual(card["status"], before[key]["status"])
            self.assertEqual(card["parents"], before[key]["parents"])

    def test_missing_parent_is_detected_without_repair(self):
        self.apply()
        work_b = self.board.find("work-b")
        work_a = self.board.find("work-a")
        assert work_b is not None and work_a is not None
        self.board.cards[work_b]["parents"].remove(work_a)
        with self.assertRaises(Divergence) as caught:
            self.apply()
        self.assertIn("parents", " ".join(caught.exception.problems).lower())
        self.assertNotIn(work_a, self.board.cards[work_b]["parents"])

    def test_deleted_parent_is_a_divergence_not_a_link_error(self):
        self.apply()
        work_a = self.board.find("work-a")
        work_b = self.board.find("work-b")
        assert work_a is not None and work_b is not None
        del self.board.cards[work_a]
        self.board.cards[work_b]["parents"].remove(work_a)
        with self.assertRaises(Divergence) as caught:
            self.apply()
        self.assertIn("no longer on the board", " ".join(caught.exception.problems))
        self.assertNotIn(work_a, self.board.cards[work_b]["parents"])

    def test_partial_create_failure_leaves_spec_blocked_and_nothing_released(self):
        self.board.fail_create_at = "work-b"
        with self.assertRaises(Exception):
            self.apply()
        self.assert_inert(self.board)
        self.assertEqual(self.board.complete_calls, 0)
        self.assertEqual(self.board.find("work-b"), None)
        self.assertEqual(self.board.cards[self.board.find("spec")]["status"], "blocked")
        self.assertEqual(self.board.cards[self.board.find("work-a")]["status"], "todo")
        self.assertEqual(self.board.parents_of("work-a"), [self.board.find("spec")])
        state = load_state(self.state_dir)
        self.assertIsNotNone(state)
        self.assertEqual(set(state["cards"]), {"spec", "work-a"})

    def test_link_repair_failure_keeps_spec_blocked_and_review_gated(self):
        # A possible failure: the review card exists with one of its two links
        # (external drift or a previous repair). The missing link fails. The
        # card still has a pending parent, so it cannot be promoted.
        spec_id = self.seed("spec", initial_status="blocked")
        work_a = self.seed("work-a", [spec_id])
        work_b = self.seed("work-b", [work_a])
        review = self.seed("review", [work_a])  # work-b link is missing
        self.write_state({"spec": spec_id, "work-a": work_a,
                          "work-b": work_b, "review": review})
        self.board.fail_link_child_step = "review"
        with self.assertRaises(Exception):
            self.apply()
        self.assert_inert(self.board)
        self.assertEqual(self.board.complete_calls, 0)
        self.assertEqual(self.board.cards[review]["status"], "todo")
        self.assertEqual(self.board.parents_of("review"), [work_a])
        self.assertNotIn(work_b, self.board.parents_of("review"))
        # Native eligibility: review still waits on work-a, which waits on the
        # blocked spec gate. No executable child is released by the failure.
        self.assertEqual(self.board.cards[review]["status"], "todo")
        self.assertEqual(self.board.cards[work_a]["status"], "todo")
        self.assertEqual(self.board.cards[work_b]["status"], "todo")
        self.assertEqual(self.board.eligible_steps(), [])
        self.assertFalse(self.board.can_execute(review))

    def test_atomic_create_rolls_back_when_a_parent_link_fails(self):
        # A link error inside create_task aborts its write transaction: no
        # half-created child survives to be dispatched without its parents.
        self.board.fail_link_child_step = "work-b"
        with self.assertRaises(Exception):
            self.apply()
        self.assertIsNone(self.board.find("work-b"))
        self.assertEqual(self.board.eligible_steps(), [])
        self.assertEqual(self.board.cards[self.board.find("spec")]["status"], "blocked")
        self.assertEqual(self.board.complete_calls, 0)
        state = load_state(self.state_dir)
        self.assertEqual(set(state["cards"]), {"spec", "work-a"})

    def test_interruption_after_create_persists_link_and_resume_finishes(self):
        # Native create is atomic: at the instant it returns, the child and its
        # links are durable. Crash before apply records the id.
        self.board.interrupt_after_create_step = "work-b"
        with self.assertRaises(Exception):
            self.apply()
        work_b = self.board.find("work-b")
        self.assertIsNotNone(work_b)
        self.assertEqual(self.board.cards[work_b]["status"], "todo")
        self.assertEqual(self.board.parents_of("work-b"), [self.board.find("work-a")])
        self.assertFalse(self.board.can_execute(work_b))
        self.assertEqual(self.board.eligible_steps(), [])
        self.assertEqual(self.board.cards[self.board.find("spec")]["status"], "blocked")
        self.assertEqual(self.board.complete_calls, 0)
        state = load_state(self.state_dir)
        self.assertEqual(set(state["cards"]), {"spec", "work-a"})
        self.assertTrue(state.get("partial"))

        self.board.interrupt_after_create_step = None
        result = self.apply()
        self.assertEqual(self.board.find("work-b"), work_b)
        self.assertEqual(len(self.board.cards), len(self.spec["steps"]))
        self.assertEqual(len(set(self.board.by_key.values())), len(self.spec["steps"]))
        self.assertGreaterEqual(result["reused"], 2)
        self.assertEqual(result["created"], 4)
        # The resumed apply closes the gate once and releases only the first
        # work card; the rest of the chain stays gated.
        self.assertEqual(self.board.cards[self.board.find("spec")]["status"], "done")
        self.assertEqual(self.board.eligible_steps(), ["work-a"])
        self.assertEqual(self.board.complete_calls, 1)
        self.assertEqual(self.board.parents_of("work-b"), [self.board.find("work-a")])
        self.assertEqual(self.board.cards[self.board.find("spec")]["status"], "done")

    def test_resume_reuses_ids_and_finishes_missing_links(self):
        spec_id = self.seed("spec", initial_status="blocked")
        work_a = self.seed("work-a", [spec_id])
        work_b = self.seed("work-b", [work_a])
        review = self.seed("review", [work_a])
        self.write_state({"spec": spec_id, "work-a": work_a,
                          "work-b": work_b, "review": review})
        self.board.fail_link_child_step = "review"
        with self.assertRaises(Exception):
            self.apply()
        self.board.fail_link_child_step = None

        result = self.apply()
        self.assertEqual(self.board.find("spec"), spec_id)
        self.assertEqual(self.board.find("work-a"), work_a)
        self.assertEqual(self.board.find("review"), review)
        self.assertGreaterEqual(result["reused"], 4)
        self.assertEqual(self.board.complete_calls, 1)
        self.assertEqual(self.board.cards[spec_id]["status"], "done")
        self.assertEqual(set(self.board.parents_of("review")), {work_a, work_b})

    def test_replay_does_not_duplicate_cards_or_completion(self):
        self.apply()
        cards_after_first = dict(self.board.by_key)
        creates_after_first = len([c for c in self.board.calls if c[0] == "create"])
        result = self.apply()
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["reused"], len(self.spec["steps"]))
        self.assertEqual(len(self.board.cards), len(cards_after_first))
        self.assertEqual(self.board.by_key, cards_after_first)
        self.assertEqual(self.board.complete_calls, 1)
        self.assertEqual(
            len([c for c in self.board.calls if c[0] == "create"]) - creates_after_first,
            len(self.spec["steps"]),
        )

    def test_divergent_content_for_same_identity_is_an_explicit_conflict(self):
        self.apply()
        cards_before = set(self.board.cards)
        keys_before = dict(self.board.by_key)
        raw = base_spec()
        raw["goal"] = "Objetivo alterado."
        changed = lro_spec.validate(raw, self.base)
        with self.assertRaises(Divergence) as ctx:
            self.apply(spec=changed)
        message = str(ctx.exception)
        self.assertIn("divergent contract marker", message)
        self.assertIn("use a new objective_id", message)
        self.assertEqual(set(self.board.cards), cards_before)
        self.assertEqual(self.board.by_key, keys_before)
        self.assertEqual(self.board.complete_calls, 1)

    def test_completed_spec_blocks_partial_rebuild(self):
        self.apply()
        state = load_state(self.state_dir)
        del state["cards"]["work-b"]
        state_path(self.state_dir).write_text(json.dumps(state), encoding="utf-8")
        cards_before = {step: self.board.find(step) for step in
                        ("spec", "work-a", "work-b", "review", "consolidate", "approval")}
        calls_before = [call for call in self.board.calls if call[0] == "create"]
        creates_before = len(calls_before)
        with self.assertRaises(Divergence) as ctx:
            self.apply()
        self.assertIn("partially built graph", str(ctx.exception))
        self.assertEqual({step: self.board.find(step) for step in cards_before},
                         cards_before)
        self.assertIsNotNone(self.board.find("work-b"))
        self.assertEqual([call for call in self.board.calls if call[0] == "create"],
                         calls_before)
        self.assertEqual(self.board.complete_calls, 1)

    def test_reused_divergent_card_is_rejected_before_any_completion(self):
        # Simulate a card that already exists under the same idempotency key
        # with a body from another spec version.
        self.apply()
        card_id = self.board.find("work-a")
        self.board.cards[card_id]["body"] = "corpo divergente"
        self.board.complete_calls = 0
        with self.assertRaises(Divergence):
            self.apply()
        self.assertEqual(self.board.complete_calls, 0)


if __name__ == "__main__":
    unittest.main()
