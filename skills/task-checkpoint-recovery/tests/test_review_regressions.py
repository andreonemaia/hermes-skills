"""Regressions for the findings confirmed during the independent review.

Each test reproduces a defect observed on the unfixed code and now guards the
fix. Time is pinned through ``TCR_NOW``/``TCR_EPOCH`` exactly as in
``test_task_checkpoint_recovery.py``, whose fixture is reused here.

Run with::

    python -m unittest discover -s tests -p "test_*.py" -v
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
CLI = SCRIPTS / "checkpoint.py"
VALIDATE = SCRIPTS / "validate.py"

sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


FIX = _load("tcr_fixture_regressions", HERE / "test_task_checkpoint_recovery.py")

NOW = FIX.NOW
NOW_EPOCH = FIX.NOW_EPOCH
LATER = "2026-10-13T12:30:00-03:00"


class RegressionBase(FIX.Base):
    """Fixture plus a runner for the validator, which is a separate script."""

    def validate(self, *args: str, expect: int = 0, project: Path | None = None
                 ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["TCR_NOW"] = NOW
        env["TCR_EPOCH"] = NOW_EPOCH
        env.pop("PYTHONPATH", None)
        target = self.project if project is None else project
        proc = subprocess.run(
            [sys.executable, str(VALIDATE), "--project", str(target), *args],
            capture_output=True, text=True, env=env, cwd=str(target),
        )
        self.assertEqual(
            proc.returncode, expect,
            "exit %s != %s\nstdout:\n%s\nstderr:\n%s"
            % (proc.returncode, expect, proc.stdout, proc.stderr),
        )
        return proc

    def ledger(self, task: str = "demo") -> list[dict]:
        path = self.store / task / "operations.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]


# ---------------------------------------------------------------------------
# 1. environment dumps are matched strictly (no prose false positive)
# ---------------------------------------------------------------------------

class TestEnvironmentDumpDetection(RegressionBase):
    def test_prose_containing_name_equals_value_is_not_a_dump(self) -> None:
        # "GPU=90% during the load test" used to be counted as an environment
        # dump, so an ordinary note was refused as a secret.
        for prose in ("GPU=90% during the load test",
                      "we saw CPU=100% and DISK=full on the runner",
                      "the log said TIMEOUT=30 seconds"):
            self.assertIsNone(C.ENV_DUMP_RE.search(prose), prose)

    def test_real_dump_is_still_detected(self) -> None:
        self.assertTrue(C.ENV_DUMP_RE.findall("API_KEY=abc123\nSECRET_TOKEN=def456\n"))
        self.assertTrue(C.ENV_DUMP_RE.findall("HOME=/root PATH=/usr/bin\n"))
        self.assertTrue(C.ENV_DUMP_RE.findall("  DB_HOST=localhost DB_PORT=5432\n"))

    def test_prose_note_is_accepted_by_save(self) -> None:
        self.save("--title", "T", "--note", "GPU=90% during the load test")
        notes = self.checkpoint().get("notes") or []
        self.assertTrue(any("GPU=90%" in str(item) for item in notes), notes)


# ---------------------------------------------------------------------------
# 2. timestamps are portable and never crash the CLI
# ---------------------------------------------------------------------------

class TestTimestampHandling(RegressionBase):
    def test_now_iso_round_trips_through_fromisoformat(self) -> None:
        stamp = C.now_iso()
        # %z produced "+0000" on POSIX and a timezone *name* on Windows, which
        # datetime.fromisoformat cannot read back.
        self.assertRegex(stamp, r"[+-]\d{2}:\d{2}$", stamp)
        self.assertIsInstance(datetime.datetime.fromisoformat(stamp), datetime.datetime)

    def test_offsets_without_a_colon_are_still_accepted(self) -> None:
        self.assertTrue(C.parse_timestamp("2026-10-13T12:00:00-0300"))

    def test_non_finite_and_out_of_range_epochs_raise_value_error(self) -> None:
        for bad in ("nan", "inf", "-inf", "1e18", "99999999999999"):
            with self.assertRaises(ValueError, msg=bad):
                C.parse_timestamp(bad)

    def test_out_of_range_epoch_never_escapes_as_overflow(self) -> None:
        proc = self.run_cli("save", "--task", "demo", "--title", "T",
                            "--objective", "o", "--now", "1e18", expect=C.EXIT_USAGE)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertFalse((self.store / "demo" / "checkpoint.json").exists())


# ---------------------------------------------------------------------------
# 3. usage errors use the documented exit code (4), help stays 0
# ---------------------------------------------------------------------------

class TestUsageExitCodes(RegressionBase):
    def test_unknown_flag_is_a_usage_error(self) -> None:
        proc = self.run_cli("--definitely-not-a-flag", expect=C.EXIT_USAGE)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unknown_subcommand_is_a_usage_error(self) -> None:
        self.run_cli("definitely-not-a-command", expect=C.EXIT_USAGE)

    def test_validator_unknown_flag_is_a_usage_error(self) -> None:
        self.validate("--definitely-not-a-flag", expect=C.EXIT_USAGE)

    def test_help_is_not_an_error(self) -> None:
        self.run_cli("--help", expect=0)
        self.validate("--help", expect=0)


# ---------------------------------------------------------------------------
# 4. the ledger is part of the checkpoint: record-op advances the revision
# ---------------------------------------------------------------------------

class TestRecordOpAdvancesRevision(RegressionBase):
    def test_record_op_bumps_the_revision_and_keeps_history(self) -> None:
        self.save("--title", "T")
        before = self.checkpoint()
        self.assertEqual(before["revision"], 1)
        self.assertEqual(self.history(), [])
        self.assertEqual(before["operations_summary"]["started"], 0)

        proc = self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                            "--target", "abc123", "--state", "started", now=LATER)
        self.assertEqual(proc.returncode, 0)
        after = self.checkpoint()

        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["task"]["updated_at"], LATER)
        self.assertEqual(after["operations_summary"]["started"], 1)
        self.assertEqual(len(self.history()), 1)

    def test_history_preserves_the_version_just_written(self) -> None:
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "push",
                     "--target", "origin", "--state", "verified", now=LATER)
        snapshots = self.history()
        self.assertTrue(snapshots, "record-op must leave a history snapshot")
        newest = json.loads((self.store / "demo" / "history" / snapshots[-1])
                            .read_text(encoding="utf-8"))
        # recover_from_history restores the state that existed before the live
        # file was damaged, so the snapshot is the version just written.
        self.assertEqual(newest["revision"], self.checkpoint()["revision"])
        self.assertEqual(newest["operations_summary"]["verified"], 1)

    def test_unchanged_content_is_not_snapshotted_twice(self) -> None:
        self.save("--title", "T")
        for _ in range(2):
            self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                         "--target", "abc", "--state", "started", now=LATER)
        # Both calls advance the revision (each is a real ledger write) but the
        # checkpoint content is identical, so only one snapshot is kept.
        self.assertEqual(self.checkpoint()["revision"], 3)
        self.assertEqual(len(self.history()), 1)


# ---------------------------------------------------------------------------
# 5. operations are addressed, not collapsed into one ledger entry
# ---------------------------------------------------------------------------

class TestOperationIdentityIsRequired(RegressionBase):
    def test_record_op_without_target_or_id_is_refused(self) -> None:
        self.save("--title", "T")
        proc = self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                            "--state", "started", expect=C.EXIT_USAGE)
        self.assertIn("--op-id", proc.stderr)
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.checkpoint()["revision"], 1)

    def test_check_op_without_target_or_id_is_refused(self) -> None:
        self.save("--title", "T")
        self.run_cli("check-op", "--task", "demo", "--kind", "commit",
                     expect=C.EXIT_USAGE)

    def test_two_operations_of_one_kind_are_recorded_separately(self) -> None:
        self.save("--title", "T")
        for target in ("first", "second"):
            self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                         "--target", target, "--state", "started")
        events = self.ledger()
        self.assertEqual(sorted(e["target"] for e in events), ["first", "second"])
        self.assertEqual(len({e["op_id"] for e in events}), 2)
        self.assertEqual(self.checkpoint()["operations_summary"]["started"], 2)


# ---------------------------------------------------------------------------
# 6. a hand-edited store is reported, never raised
# ---------------------------------------------------------------------------

class TestHandEditedStoreIsReported(RegressionBase):
    def test_scalar_payload_is_reported_not_raised(self) -> None:
        self.save("--title", "T")
        (self.store / "demo" / "checkpoint.json").write_text("[1, 2, 3]\n", encoding="utf-8")
        proc = self.validate("--task", "demo", "--json", expect=C.EXIT_CORRUPT)
        self.assertNotIn("Traceback", proc.stderr)
        report = json.loads(proc.stdout)
        self.assertFalse(report["ok"])
        self.assertTrue(report["results"][0]["problems"], report)

    def test_unknown_task_is_invalid_and_empty_project_is_missing(self) -> None:
        self.save("--title", "T")
        # A named task with no checkpoint on disk is an invalid task (2)...
        self.validate("--task", "nope", expect=C.EXIT_CORRUPT)
        # ...while a project with no checkpoints at all is a missing store (3).
        empty = Path(tempfile.mkdtemp(prefix="tcr-empty-"))
        self.addCleanup(shutil.rmtree, empty, True)
        self.validate(project=empty, expect=C.EXIT_MISSING)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
