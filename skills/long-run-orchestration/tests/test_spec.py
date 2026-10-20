"""Spec contract tests: provider/model pairing, graph shape and workspace flags."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import helpers
from helpers import base_spec

import lro_spec
from lro_objective import workspace_flag


class SpecValidationTests(unittest.TestCase):
    def validate(self, spec):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            return lro_spec.validate(copy.deepcopy(spec), Path(tmp))

    def errors(self, spec):
        with self.assertRaises(lro_spec.SpecError) as ctx:
            self.validate(spec)
        return "; ".join(ctx.exception.errors)

    def test_valid_spec_normalizes_work_chain_and_order(self):
        spec = self.validate(base_spec())
        ids = [s["id"] for s in spec["steps"]]
        self.assertEqual(ids, ["spec", "work-a", "work-b", "review", "consolidate", "approval"])
        by_id = {s["id"]: s for s in spec["steps"]}
        self.assertEqual(by_id["work-a"]["depends_on"], ["spec"])
        self.assertEqual(by_id["work-b"]["depends_on"], ["work-a"])
        self.assertEqual(by_id["review"]["depends_on"], ["work-a", "work-b"])
        self.assertEqual(by_id["approval"]["initial_status"], "blocked")
        self.assertEqual(by_id["approval"]["assignee"], "lro-human-approval")
        self.assertEqual(by_id["spec"]["assignee"], "")
        self.assertEqual(spec["spec_sha256"], lro_spec.spec_hash(spec))

    def test_schema_version_true_is_not_an_integer_version(self):
        spec = base_spec()
        spec["schema_version"] = True
        self.assertIn("schema_version", self.errors(spec))

    def test_executor_provider_requires_model(self):
        spec = base_spec()
        spec["executor"] = {"profile": "default", "provider": "custom"}
        self.assertIn("executor.provider: requires executor.model", self.errors(spec))

    def test_review_provider_requires_model(self):
        spec = base_spec()
        spec["review"] = {"enabled": True, "provider": "custom"}
        self.assertIn("review.provider: requires review.model", self.errors(spec))

    def test_review_enabled_requires_executor_model(self):
        spec = base_spec()
        spec["executor"] = {"profile": "default"}
        self.assertIn("executor.model: required when review.enabled is true", self.errors(spec))

    def test_review_model_must_differ_from_executor(self):
        spec = base_spec()
        spec["review"]["model"] = spec["executor"]["model"]
        self.assertIn("review.model: must differ from executor.model", self.errors(spec))

    def test_review_step_model_must_differ_from_executor(self):
        spec = base_spec()
        for step in spec["steps"]:
            if step["id"] == "review":
                step["model"] = spec["executor"]["model"]
        self.assertIn("must differ from executor.model", self.errors(spec))

    def test_review_disabled_rejects_review_step(self):
        spec = base_spec()
        spec["review"] = {"enabled": False}
        self.assertIn("review step present but review.enabled is false", self.errors(spec))

    def test_work_steps_must_form_a_chain(self):
        spec = base_spec()
        for step in spec["steps"]:
            if step["id"] == "work-b":
                step["depends_on"] = ["spec"]
        self.assertIn("expected exactly ['work-a'] for kind=work", self.errors(spec))

    def test_unknown_dependency_is_rejected(self):
        spec = base_spec()
        for step in spec["steps"]:
            if step["id"] == "work-a":
                step["depends_on"] = ["ghost"]
        self.assertIn("unknown step 'ghost'", self.errors(spec))

    def test_self_dependency_is_rejected(self):
        spec = base_spec()
        for step in spec["steps"]:
            if step["id"] == "review":
                step["depends_on"] = ["work-a", "work-b", "review"]
        message = self.errors(spec)
        self.assertIn("a step cannot depend on itself", message)

    def test_disconnected_step_is_rejected(self):
        spec = base_spec()
        spec["steps"].append({
            "id": "work-c", "kind": "work", "title": "Solta", "body": "C.",
            "acceptance": [{"id": "c1", "criterion": "C", "evidence": "proof"}],
        })
        message = self.errors(spec)
        self.assertIn("work-c", message)
        self.assertIn("expected exactly", message)

    def test_reachability_excludes_isolated_steps(self):
        steps = [
            {"id": "spec", "kind": "spec", "depends_on": []},
            {"id": "work-a", "kind": "work", "depends_on": ["spec"]},
            {"id": "orphan", "kind": "work", "depends_on": []},
        ]
        self.assertEqual(lro_spec._reachable_from("spec", steps), {"spec", "work-a"})

    def test_reachability_starts_at_spec_regardless_of_declaration_order(self):
        spec = base_spec()
        spec["steps"] = list(reversed(spec["steps"]))
        normalized = self.validate(spec)
        self.assertEqual(normalized["steps"][0]["id"], "spec")

    def test_workspace_kinds_keep_their_native_meaning(self):
        self.assertEqual(workspace_flag({"kind": "scratch"}), "scratch")
        self.assertEqual(workspace_flag({"kind": "dir", "path": "/tmp/x"}), "dir:/tmp/x")
        self.assertEqual(workspace_flag({"kind": "worktree", "path": "/tmp/wt"}),
                         "worktree:/tmp/wt")
        # A worktree without a path is never downgraded to a shared directory.
        self.assertEqual(workspace_flag({"kind": "worktree", "path": ""}), "worktree")

    def test_workspace_requires_path_for_dir_and_worktree(self):
        for kind in ("dir", "worktree"):
            spec = base_spec()
            spec["workspace"] = {"kind": kind}
            self.assertIn(f"workspace.path: required when kind={kind}", self.errors(spec))

    def test_branch_only_valid_for_worktree(self):
        spec = base_spec()
        spec["workspace"] = {"kind": "scratch", "branch": "feat/x"}
        self.assertIn("workspace.branch: only valid with kind=worktree", self.errors(spec))


if __name__ == "__main__":
    unittest.main()
