"""Objective contract tests: PR evidence and review semantics."""

from __future__ import annotations

import unittest
from pathlib import Path

from helpers import base_spec

from lro_objective import StepView, check_evidence, check_review, delivery_status


def step(**overrides):
    data = {
        "id": "work-a",
        "kind": "work",
        "title": "Trabalho",
        "body": "x",
        "depends_on": [],
        "acceptance": [],
        "evidence_required": ["notes", "pr_url"],
        "completion_contract": "local-only",
    }
    data.update(overrides)
    return data


def view(step_data, run=None, result="", body="", status="done"):
    payload = {
        "task": {"id": "t1", "status": status, "body": body, "result": result},
        "runs": [run] if run else [],
    }
    return StepView({**step_data, "_spec_sha256": "0" * 64, "_objective_id": "obj-test"},
                    "t1", payload, [])


class PrEvidenceTests(unittest.TestCase):
    def test_valid_pr_url_proves_publication(self):
        run = {"id": "r1", "outcome": "completed",
               "metadata": {"notes": "ok", "pr_url": "https://github.com/o/r/pull/7"}}
        result = check_evidence(step(), run, [Path(".")])
        self.assertTrue(result["ok"], result)

    def test_published_pr_is_accepted_when_pr_url_is_absent(self):
        run = {"id": "r1", "outcome": "completed",
               "metadata": {"notes": "ok",
                            "published_pr": "https://github.com/o/r/pull/8"}}
        result = check_evidence(step(), run, [Path(".")])
        self.assertTrue(result["ok"], result)

    def test_empty_values_do_not_prove_publication(self):
        run = {"id": "r1", "outcome": "completed",
               "metadata": {"notes": "ok", "pr_url": "", "published_pr": ""}}
        result = check_evidence(step(), run, [Path(".")])
        self.assertFalse(result["ok"])
        self.assertIn("pr_url", result["missing"])

    def test_invalid_pr_url_does_not_prove_publication(self):
        run = {"id": "r1", "outcome": "completed",
               "metadata": {"notes": "ok", "pr_url": "not-a-pr"}}
        result = check_evidence(step(), run, [Path(".")])
        self.assertFalse(result["ok"])
        self.assertTrue(result["problems"])

    def test_valid_published_pr_wins_over_invalid_pr_url(self):
        run = {"id": "r1", "outcome": "completed",
               "metadata": {"notes": "ok", "pr_url": "nope",
                            "published_pr": "https://github.com/o/r/pull/9"}}
        result = check_evidence(step(), run, [Path(".")])
        self.assertTrue(result["ok"], result)

    def test_missing_pr_evidence_is_reported(self):
        run = {"id": "r1", "outcome": "completed", "metadata": {"notes": "ok"}}
        result = check_evidence(step(), run, [Path(".")])
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], ["pr_url"])

    def test_delivery_status_reads_runtime_metadata(self):
        run = {"id": "r1", "outcome": "completed",
               "metadata": {"published_pr": "https://github.com/o/r/pull/10"}}
        step_data = step(completion_contract="o/r")
        status = delivery_status(step_data, view(step_data, run=run))
        self.assertTrue(status["ok"])
        self.assertEqual(status["pr_url"], "https://github.com/o/r/pull/10")

    def test_delivery_status_rejects_invalid_metadata_url(self):
        run = {"id": "r1", "outcome": "completed", "metadata": {"pr_url": "ftp://x"}}
        step_data = step(completion_contract="o/r")
        status = delivery_status(step_data, view(step_data, run=run))
        self.assertFalse(status["ok"])
        self.assertEqual(status["pr_url"], "")

    def test_delivery_status_can_read_url_from_result(self):
        step_data = step(completion_contract="o/r")
        status = delivery_status(
            step_data,
            view(step_data, result="PR: https://github.com/o/r/pull/11"),
        )
        self.assertTrue(status["ok"])


class ReviewSemanticsTests(unittest.TestCase):
    def spec(self):
        spec = base_spec()
        return spec

    def run_meta(self, **metadata):
        return {"id": "r1", "outcome": "completed", "metadata": metadata}

    def test_review_approval_requires_distinct_reviewer(self):
        spec = self.spec()
        result = check_review(spec["steps"][3], self.run_meta(
            verdict="approve", reviewer_model="reviewer-model"), spec)
        self.assertTrue(result["ok"], result)

    def test_review_rejects_executor_as_reviewer(self):
        spec = self.spec()
        result = check_review(spec["steps"][3], self.run_meta(
            verdict="approve", reviewer_model=spec["executor"]["model"]), spec)
        self.assertFalse(result["ok"])

    def test_review_in_progress_is_not_completion(self):
        spec = self.spec()
        step_data = spec["steps"][3]
        payload = {"task": {"id": "t1", "status": "review", "body": ""}, "runs": []}
        view_ = StepView({**step_data, "_spec_sha256": "0" * 64,
                          "_objective_id": "obj-test"}, "t1", payload, [])
        self.assertFalse(view_.executed)
        self.assertIsNone(view_.run)


class TestsRunEvidenceTests(unittest.TestCase):
    def check(self, value):
        return check_evidence(step(evidence_required=["tests_run"]),
                              {"id": "r1", "outcome": "completed",
                               "metadata": {"tests_run": value}},
                              [Path(".")])

    def test_non_empty_records_with_exit_zero_are_accepted(self):
        result = self.check([{"cmd": "python -m unittest", "exit": 0}])
        self.assertTrue(result["ok"], result)

    def test_empty_list_is_rejected(self):
        self.assertFalse(self.check([])["ok"])

    def test_string_is_rejected(self):
        self.assertFalse(self.check("all tests passed")["ok"])

    def test_missing_or_nonzero_exit_is_rejected(self):
        self.assertFalse(self.check([{"cmd": "pytest"}])["ok"])
        self.assertFalse(self.check([{"cmd": "pytest", "exit": 1}])["ok"])


if __name__ == "__main__":
    unittest.main()
