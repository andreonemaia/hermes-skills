"""Watchdog behavior: read-only observation, progress and alerts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import FakeKanban, base_spec

from lro_objective import apply_objective, load_state, save_state
from lro_spec import validate
from lro_watchdog import tick


START = "2026-01-01T00:00:00+00:00"


def step_entry(record, step):
    return next(item for item in record["steps"] if item["step"] == step)


def at(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return moment.isoformat()


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)
        self.state_dir = self.base / ".lro"
        self.board = FakeKanban()
        self.spec = validate(base_spec(), self.base)
        apply_objective(self.spec, self.base, self.board, self.state_dir, emit=lambda *_: None)
        self.cards = load_state(self.state_dir)["cards"]
        self.board.calls.clear()
        self.board.complete_calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def running(self, step_id="work-a", started=START):
        card = self.board.cards[self.cards[step_id]]
        card["status"] = "running"
        card["runs"] = [{"id": "run-1", "outcome": "running", "started_at": started}]

    def test_heartbeats_do_not_count_as_progress(self):
        self.running()
        records = []
        for seconds in (0, 10, 20):
            self.board.cards[self.cards["work-a"]]["event_log"].append(
                {"kind": "heartbeat", "created_at": at(seconds), "payload": {}})
            records.append(tick(self.spec, self.board, self.state_dir,
                                at=at(seconds), emit=lambda *_: None))
        entry = step_entry(records[-1], "work-a")
        self.assertEqual(entry["stall_ticks"], 3)
        self.assertEqual(entry["state"], "running_no_progress")
        self.assertIn("stagnation", records[-1]["alerts"])

    def test_progress_event_resets_stagnation(self):
        self.running()
        tick(self.spec, self.board, self.state_dir, at=at(0), emit=lambda *_: None)
        tick(self.spec, self.board, self.state_dir, at=at(10), emit=lambda *_: None)
        self.board.cards[self.cards["work-a"]]["event_log"].append(
            {"kind": "progress", "created_at": at(20), "payload": {"note": "step"}})
        record = tick(self.spec, self.board, self.state_dir, at=at(20),
                      emit=lambda *_: None)
        self.assertEqual(step_entry(record, "work-a")["stall_ticks"], 1)
        self.assertNotIn("stagnation", record["alerts"])

    def test_runtime_exhaustion_alerts_once(self):
        self.running()
        record = tick(self.spec, self.board, self.state_dir, at=at(1861),
                      emit=lambda *_: None)
        entry = step_entry(record, "work-a")
        self.assertTrue(entry["runtime_exhausted"])
        self.assertEqual(entry["state"], "runtime_exhausted")
        self.assertIn("runtime_exhausted", record["alerts"])
        alerts = [json.loads(line) for line in
                  (self.state_dir / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
        runtime_alert = next(item for item in alerts
                             if item["kind"] == "runtime_exhausted")
        self.assertEqual(runtime_alert["at"], at(1861))
        again = tick(self.spec, self.board, self.state_dir, at=at(1871),
                     emit=lambda *_: None)
        self.assertNotIn("runtime_exhausted", again["alerts"])

    def test_ended_run_on_running_card_is_unknown_not_done(self):
        card = self.board.cards[self.cards["work-a"]]
        card["status"] = "running"
        card["runs"] = [{"id": "run-1", "outcome": "completed", "started_at": START,
                         "ended_at": at(5)}]
        record = tick(self.spec, self.board, self.state_dir, at=at(10),
                      emit=lambda *_: None)
        entry = step_entry(record, "work-a")
        self.assertEqual(entry["state"], "unknown")
        self.assertIn("already ended", entry["note"])
        self.assertIn("unknown_state", record["alerts"])
        self.assertNotEqual(record["state"], "done")

    def test_gateway_failure_is_reported_without_mutation(self):
        self.board.fail_show_ids.add(self.cards["work-a"])
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        self.assertFalse(record["gateway_ok"])
        self.assertEqual(record["state"], "gateway_unavailable")
        self.assertIn("gateway_unavailable", record["alerts"])
        self.assertEqual(self.board.complete_calls, 0)

    def test_missing_card_is_drift_not_gateway_outage(self):
        del self.board.cards[self.cards["work-a"]]
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        entry = step_entry(record, "work-a")
        self.assertTrue(record["gateway_ok"])
        self.assertEqual(record["state"], "drift")
        self.assertIn("not on the board", entry["note"])
        self.assertIn("drift", record["alerts"])
        self.assertNotIn("gateway_unavailable", record["alerts"])

    def test_marker_drift_is_reported(self):
        self.board.cards[self.cards["work-a"]]["body"] = "tampered"
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        self.assertEqual(record["state"], "drift")
        self.assertIn("drift", record["alerts"])

    def test_card_released_before_parent_is_drift(self):
        # External promotion: work-b is ready while work-a is still todo.
        self.board.cards[self.cards["work-a"]]["status"] = "todo"
        self.board.cards[self.cards["work-b"]]["status"] = "ready"
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        entry = step_entry(record, "work-b")
        self.assertEqual(record["state"], "drift")
        self.assertIn("released before parents completed", entry["note"])
        self.assertIn("drift", record["alerts"])

    def test_missing_dependency_card_is_drift(self):
        state = load_state(self.state_dir)
        del state["cards"]["work-a"]
        save_state(self.state_dir, state)
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        entry = step_entry(record, "work-b")
        self.assertEqual(record["state"], "drift")
        self.assertIn("missing dependency cards: work-a", entry["note"])

    def test_parent_lookup_failure_is_gateway_not_drift(self):
        self.board.cards[self.cards["work-b"]]["status"] = "ready"
        self.board.fail_show_ids.add(self.cards["work-a"])
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        entry = step_entry(record, "work-b")
        self.assertFalse(record["gateway_ok"])
        self.assertEqual(record["state"], "gateway_unavailable")
        self.assertIn("parent lookup failed", entry["note"])
        self.assertNotIn("drift", record["alerts"])

    def test_approval_gate_running_is_drift(self):
        card = self.board.cards[self.cards["approval"]]
        card["status"] = "running"
        card["runs"] = [{"id": "run-gate", "outcome": "running", "started_at": START}]
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        entry = step_entry(record, "approval")
        self.assertEqual(record["state"], "drift")
        self.assertIn("human approval gate is running", entry["note"])
        self.assertIn("drift", record["alerts"])

    def test_approval_gate_reassigned_is_drift(self):
        self.board.cards[self.cards["approval"]]["assignee"] = "default"
        record = tick(self.spec, self.board, self.state_dir, at=at(0),
                      emit=lambda *_: None)
        entry = step_entry(record, "approval")
        self.assertEqual(record["state"], "drift")
        self.assertIn("expected lro-human-approval", entry["note"])
        self.assertIn("drift", record["alerts"])

    def test_watchdog_only_reads(self):
        tick(self.spec, self.board, self.state_dir, at=at(0), emit=lambda *_: None)
        self.assertTrue(self.board.calls)
        self.assertEqual({call[0] for call in self.board.calls}, {"show"})
        self.assertEqual(self.board.complete_calls, 0)

    def test_journal_and_latest_are_written(self):
        tick(self.spec, self.board, self.state_dir, at=at(0), emit=lambda *_: None)
        self.assertTrue((self.state_dir / "watchdog.jsonl").is_file())
        latest = json.loads((self.state_dir / "watchdog-latest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(latest["spec_sha256"], self.spec["spec_sha256"])


if __name__ == "__main__":
    unittest.main()
