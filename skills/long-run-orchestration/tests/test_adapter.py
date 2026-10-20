"""Adapter tests: allow-list, read-only guard, error mapping, argv safety."""

from __future__ import annotations

import json
import subprocess
import unittest

from helpers import FakeKanban  # noqa: F401  (keeps the shared fixture importable)

import lro_adapter
from lro_adapter import AdapterError, KanbanCLI


class Recorder:
    def __init__(self, *, stdout: str = "{}", returncode: int = 0,
                 stderr: str = "", exc: Exception | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.exc = exc
        self.argv = []

    def __call__(self, argv):
        self.argv = argv
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def cli(recorder, **kwargs):
    return KanbanCLI(["hermes", "kanban"], runner=recorder, **kwargs)


class AdapterTests(unittest.TestCase):
    def test_create_uses_native_signature(self):
        rec = Recorder(stdout='{"id": "t1"}')
        result = cli(rec).create(
            title="Titulo", body="Corpo", idempotency_key="lro:obj:step",
            parents=["p1", "p2"], assignee="default", workspace="worktree:/tmp/wt",
            branch="wt/x", max_runtime="30m", max_retries=2, skills=["github"],
            model="m", provider="custom", completion_contract="o/r",
            goal=True, goal_max_turns=4, initial_status="blocked",
        )
        self.assertEqual(result["id"], "t1")
        argv = rec.argv
        self.assertEqual(argv[:2], ["hermes", "kanban"])
        self.assertEqual(argv[2], "create")
        self.assertEqual(argv[3], "Titulo")
        self.assertIn("--idempotency-key", argv)
        self.assertEqual(argv[argv.index("--idempotency-key") + 1], "lro:obj:step")
        self.assertEqual(argv.count("--parent"), 2)
        self.assertEqual(argv[argv.index("--workspace") + 1], "worktree:/tmp/wt")
        self.assertEqual(argv[argv.index("--initial-status") + 1], "blocked")
        self.assertEqual(argv[-1], "--json")

    def test_create_without_id_raises_invalid_json(self):
        rec = Recorder(stdout='{"ok": true}')
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).create(title="T", body="B", idempotency_key="k")
        self.assertEqual(ctx.exception.kind, "invalid_json")

    def test_call_text_preserves_plain_text(self):
        rec = Recorder(stdout="linked t2 -> t1\n")
        text = cli(rec).call_text("link", "t1", "t2")
        self.assertEqual(text, "linked t2 -> t1\n")

    def test_call_wraps_invalid_json(self):
        rec = Recorder(stdout="not json")
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).call("show", "t1", "--json")
        self.assertEqual(ctx.exception.kind, "invalid_json")

    def test_call_does_not_turn_error_code_into_success(self):
        rec = Recorder(stdout='{"id": "t1"}', returncode=2, stderr="boom")
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).create(title="T", body="B", idempotency_key="k")
        self.assertEqual(ctx.exception.kind, "cli_error")
        self.assertEqual(ctx.exception.returncode, 2)

    def test_timeout_is_classified(self):
        rec = Recorder(exc=subprocess.TimeoutExpired(["hermes"], 1))
        with self.assertRaises(AdapterError) as ctx:
            cli(rec, timeout=1).call_text("show", "t1")
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_missing_executable_is_classified(self):
        rec = Recorder(exc=FileNotFoundError("hermes"))
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).call_text("show", "t1")
        self.assertEqual(ctx.exception.kind, "cli_error")

    def test_missing_task_is_not_a_gateway_outage(self):
        rec = Recorder(returncode=1, stderr="no such task: t404")
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).show("t404")
        self.assertEqual(ctx.exception.kind, "not_found")
        self.assertEqual(ctx.exception.returncode, 1)

    def test_missing_task_only_applies_to_show(self):
        rec = Recorder(returncode=1, stderr="no such task: t404")
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).link("t404", "t1")
        self.assertEqual(ctx.exception.kind, "cli_error")

    def test_show_unexpected_shape_is_invalid_json_not_not_found(self):
        rec = Recorder(stdout='{"id": "t1"}')
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).show("t1")
        self.assertEqual(ctx.exception.kind, "invalid_json")

    def test_read_only_blocks_mutations(self):
        rec = Recorder(stdout="{}")
        adapter = cli(rec, read_only=True)
        for call in (
            lambda: adapter.create(title="T", body="B", idempotency_key="k"),
            lambda: adapter.link("a", "b"),
            lambda: adapter.complete("a", result="ok"),
        ):
            with self.assertRaises(AdapterError) as ctx:
                call()
            self.assertEqual(ctx.exception.kind, "forbidden_command")
        self.assertEqual(rec.argv, [])

    def test_allow_list_rejects_unexpected_commands(self):
        rec = Recorder(stdout="{}")
        with self.assertRaises(AdapterError) as ctx:
            cli(rec).call_text("delete", "t1")
        self.assertEqual(ctx.exception.kind, "forbidden_command")
        self.assertEqual(rec.argv, [])

    def test_objective_content_is_a_single_argument(self):
        rec = Recorder(stdout='{"id": "t1"}')
        hostile = "x; rm -rf / # --parent evil\n$(whoami) `id`"
        cli(rec).create(title="T", body=hostile, idempotency_key="k")
        argv = rec.argv
        self.assertIn(hostile, argv)
        self.assertEqual(argv[argv.index("--body") + 1], hostile)
        # No shell was involved: the hostile text never becomes its own argv.
        self.assertFalse(any(a == "rm" for a in argv))
        self.assertFalse(any(a == "--parent" for a in argv))

    def test_board_flag_precedes_subcommand(self):
        rec = Recorder(stdout="{}")
        cli(rec, board="proj").call_text("show", "t1")
        self.assertEqual(rec.argv, ["hermes", "kanban", "--board", "proj", "show", "t1"])

    def test_default_prefix_rejects_missing_hermes(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LRO_KANBAN_CMD", None)
            with mock.patch.object(lro_adapter.shutil, "which", return_value=None):
                with self.assertRaises(AdapterError) as ctx:
                    lro_adapter.default_prefix()
        self.assertEqual(ctx.exception.kind, "cli_error")


if __name__ == "__main__":
    unittest.main()
