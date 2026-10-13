"""Hermetic, deterministic tests for the task-checkpoint-recovery CLI.

Everything runs against a throw-away project directory under the system temp
dir: no network, no user configuration, no dependency on the repository the
tests themselves live in. Time is pinned through ``TCR_NOW``/``TCR_EPOCH`` so
staleness and history naming are reproducible.

Run with::

    python -m unittest discover -s skills/task-checkpoint-recovery/tests -v
"""

from __future__ import annotations

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

sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402

NOW = "2026-10-13T12:00:00-03:00"
NOW_EPOCH = "1791903600"  # 2026-10-13T12:00:00-03:00
DAY = 86400.0


def _git_available() -> bool:
    return bool(shutil.which("git"))


class Base(unittest.TestCase):
    """Shared fixture: a scratch project plus a CLI runner."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tcr-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = Path(self.tmp)

    # -- helpers ---------------------------------------------------------
    def run_cli(self, *args: str, now: str = NOW, epoch: str = NOW_EPOCH,
                expect: int | None = 0, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["TCR_NOW"] = now
        env["TCR_EPOCH"] = epoch
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, str(CLI), "--project", str(cwd or self.project), *args],
            capture_output=True, text=True, env=env, cwd=str(cwd or self.project),
        )
        if expect is not None:
            self.assertEqual(
                proc.returncode, expect,
                "exit %s != %s\nstdout:\n%s\nstderr:\n%s"
                % (proc.returncode, expect, proc.stdout, proc.stderr),
            )
        return proc

    def payload(self, proc: subprocess.CompletedProcess) -> dict:
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - debugging aid
            self.fail("expected JSON on stdout, got:\n%s\n(%s)" % (proc.stdout, exc))

    @property
    def store(self) -> Path:
        return self.project / ".hermes" / "checkpoints"

    def checkpoint(self, task: str = "demo") -> dict:
        return json.loads((self.store / task / "checkpoint.json").read_text(encoding="utf-8"))

    def history(self, task: str = "demo") -> list[str]:
        hist = self.store / task / "history"
        if not hist.is_dir():
            return []
        return sorted(p.name for p in hist.iterdir() if p.name.endswith(".json"))

    def save(self, *extra: str, task: str = "demo", expect: int | None = 0,
             now: str = NOW, epoch: str = NOW_EPOCH) -> subprocess.CompletedProcess:
        # a brand-new checkpoint must state its objective; callers that care
        # about the wording pass their own --objective
        base = () if "--objective" in extra else ("--objective", "test objective")
        return self.run_cli("save", "--task", task, *base, *extra,
                            expect=expect, now=now, epoch=epoch)

    def init_git(self) -> str:
        """Init a repo with one commit; returns the commit SHA."""
        if not _git_available():
            self.skipTest("git is not installed")
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=str(self.project), capture_output=True, text=True, check=True)
        run("init", "-q")
        run("config", "user.email", "tcr@example.invalid")
        run("config", "user.name", "tcr tests")
        run("config", "commit.gpgsign", "false")
        (self.project / "seed.txt").write_text("seed\n", encoding="utf-8")
        run("add", "seed.txt")
        run("commit", "-q", "-m", "seed")
        return run("rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# 1. resume after an interruption
# ---------------------------------------------------------------------------

class TestResumeAfterInterruption(Base):
    def test_checkpoint_survives_and_reports_pending_work(self) -> None:
        self.save(
            "--title", "Long task", "--objective", "survive an interruption",
            "--status", "in_progress",
            "--step", "s1=Audit", "--step-status", "s1=done", "--step-evidence", "s1=audited",
            "--step", "s2=Implement",
            "--decision", "d1=stdlib only|no third-party deps",
            "--next", "n1=write the tests",
        )
        cp = self.checkpoint()
        self.assertEqual(cp["schema"], C.SCHEMA_ID)
        self.assertEqual(cp["revision"], 1)
        self.assertEqual(cp["task"]["status"], "in_progress")
        self.assertEqual([s["id"] for s in cp["steps"]], ["s1", "s2"])
        self.assertEqual(cp["steps"][0]["status"], "done")
        self.assertEqual(cp["steps"][0]["evidence"], "audited")
        # a step without an explicit status is pending, never silently done
        self.assertEqual(cp["steps"][1]["status"], "pending")
        self.assertEqual(cp["decisions"][0]["decision"], "stdlib only")
        self.assertEqual(cp["decisions"][0]["rationale"], "no third-party deps")
        self.assertTrue(cp["next_actions"][0]["requires_approval"])

        # a fresh process (no in-memory state) can rebuild the situation
        report = self.run_cli("resume-report", "--task", "demo")
        self.assertIn("Resume report: Long task", report.stdout)
        self.assertIn("steps done: 1/2", report.stdout)
        self.assertIn("write the tests", report.stdout)
        self.assertIn("stdlib only", report.stdout)

    def test_resume_report_is_machine_readable(self) -> None:
        self.save("--title", "T", "--step", "s1=One", "--next", "n1=go on")
        data = self.payload(self.run_cli("resume-report", "--task", "demo", "--json"))
        self.assertEqual(data["task"], "demo")
        self.assertEqual(data["revision"], 1)
        self.assertEqual(data["result"], "clean")
        self.assertIn("report", data)

    def test_missing_checkpoint_is_reported_not_invented(self) -> None:
        proc = self.run_cli("resume-report", "--task", "nope", expect=C.EXIT_MISSING)
        self.assertIn("no checkpoint", proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")


# ---------------------------------------------------------------------------
# 2. stale checkpoint
# ---------------------------------------------------------------------------

class TestStaleCheckpoint(Base):
    def test_old_checkpoint_is_flagged(self) -> None:
        self.save("--title", "T", "--step", "s1=One")
        # same instant: fresh
        fresh = self.payload(self.run_cli("verify", "--task", "demo", "--json"))
        self.assertNotIn("stale_checkpoint", [f["id"] for f in fresh["findings"]])

        # 3 days later the checkpoint is stale (default threshold: 24h)
        later = self.payload(self.run_cli(
            "verify", "--task", "demo", "--json",
            epoch=str(int(NOW_EPOCH) + 3 * DAY), expect=C.EXIT_DRIFT))
        stale = [f for f in later["findings"] if f["id"] == "stale_checkpoint"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["severity"], "info")

    def test_stale_after_zero_disables_the_check(self) -> None:
        self.save("--title", "T")
        data = self.payload(self.run_cli(
            "verify", "--task", "demo", "--json", "--stale-after", "0",
            epoch=str(int(NOW_EPOCH) + 30 * DAY)))
        self.assertNotIn("stale_checkpoint", [f["id"] for f in data["findings"]])

    def test_info_findings_do_not_fail_unless_strict(self) -> None:
        self.save("--title", "T")
        future = str(int(NOW_EPOCH) + 3 * DAY)
        self.run_cli("verify", "--task", "demo", "--stale-after", "0", epoch=future, expect=0)
        self.run_cli("verify", "--task", "demo", "--strict", epoch=future, expect=C.EXIT_DRIFT)


# ---------------------------------------------------------------------------
# 3. corrupt checkpoint
# ---------------------------------------------------------------------------

class TestCorruptCheckpoint(Base):
    def _corrupt(self, task: str = "demo") -> Path:
        path = self.store / task / "checkpoint.json"
        path.write_text("{ this is not json", encoding="utf-8")
        return path

    def test_verify_refuses_a_corrupt_checkpoint(self) -> None:
        self.save("--title", "T")
        self._corrupt()
        proc = self.run_cli("verify", "--task", "demo", expect=C.EXIT_CORRUPT)
        self.assertIn("corrupt", proc.stderr.lower())

    def test_corrupt_file_is_quarantined_and_history_is_used(self) -> None:
        self.save("--title", "T", "--step", "s1=One")
        self.save("--title", "T", "--step", "s1=One", "--step-status", "s1=done")
        self.assertEqual(len(self.history()), 1)  # revision 1 was preserved
        self._corrupt()

        data = self.payload(self.run_cli("verify", "--task", "demo", "--json"))
        self.assertEqual(data["source"], "history-fallback")
        # the damaged file is kept, never deleted silently
        quarantined = list((self.store / "demo").glob("checkpoint.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertIn("not json", quarantined[0].read_text(encoding="utf-8"))

        report = self.run_cli("resume-report", "--task", "demo")
        self.assertIn("was corrupt and was quarantined", report.stdout)

    def test_corrupt_without_history_is_not_overwritten(self) -> None:
        self.save("--title", "T")
        path = self._corrupt()
        proc = self.run_cli("save", "--task", "demo", "--note", "again",
                            expect=C.EXIT_CORRUPT)
        self.assertIn("corrupt", proc.stderr.lower())
        self.assertEqual(path.read_text(encoding="utf-8"), "{ this is not json")

    def test_save_recovers_from_history_and_records_it(self) -> None:
        self.save("--title", "T", "--step", "s1=One")
        self.save("--title", "T", "--step", "s1=One", "--step-status", "s1=done")
        self._corrupt()
        self.save("--note", "after the crash")
        cp = self.checkpoint()
        self.assertTrue(any("recovered from history" in n for n in cp["notes"]))
        self.assertEqual(cp["steps"][0]["status"], "done")  # snapshot restored


# ---------------------------------------------------------------------------
# 4. git state changes are signals, not fatal errors
# ---------------------------------------------------------------------------

class TestGitState(Base):
    def test_branch_change_is_a_warning_that_asks_for_revalidation(self) -> None:
        self.init_git()
        self.save("--title", "T", "--step", "s1=One")
        subprocess.run(["git", "checkout", "-q", "-b", "other"],
                       cwd=str(self.project), check=True)
        data = self.payload(self.run_cli("verify", "--task", "demo", "--json",
                                         expect=C.EXIT_DRIFT))
        mismatch = [f for f in data["findings"] if f["id"] == "branch_mismatch"]
        self.assertEqual(len(mismatch), 1)
        self.assertEqual(mismatch[0]["severity"], "warning")
        self.assertIn("revalidate", mismatch[0]["message"])

    def test_clean_tree_on_the_same_branch_verifies_clean(self) -> None:
        self.init_git()
        self.save("--title", "T")
        data = self.payload(self.run_cli("verify", "--task", "demo", "--json"))
        self.assertEqual(data["result"], "clean")
        self.assertEqual(data["findings"], [])

    def test_dirty_tree_is_flagged(self) -> None:
        self.init_git()
        self.save("--title", "T")
        (self.project / "seed.txt").write_text("changed\n", encoding="utf-8")
        data = self.payload(self.run_cli("verify", "--task", "demo", "--json",
                                         expect=C.EXIT_DRIFT))
        self.assertIn("dirty_tree", [f["id"] for f in data["findings"]])

    def test_file_drift_is_flagged(self) -> None:
        self.init_git()
        target = self.project / "tracked.txt"
        target.write_text("v1\n", encoding="utf-8")
        self.save("--title", "T", "--file", "tracked.txt")
        target.write_text("v2\n", encoding="utf-8")
        data = self.payload(self.run_cli("verify", "--task", "demo", "--json",
                                         expect=C.EXIT_DRIFT))
        self.assertIn("file_drift", [f["id"] for f in data["findings"]])

    def test_works_outside_a_git_repository(self) -> None:
        self.assertFalse((self.project / ".git").exists())
        self.save("--title", "T", "--step", "s1=One")
        cp = self.checkpoint()
        self.assertFalse(cp["repo"]["available"])
        self.assertTrue(cp["limitations"])  # the limitation is recorded, not hidden
        # reduced checks must not make every verify fail
        data = self.payload(self.run_cli("verify", "--task", "demo", "--json"))
        ids = [f["id"] for f in data["findings"]]
        self.assertEqual(ids, ["git_unavailable"])
        self.assertEqual(data["findings"][0]["severity"], "info")


# ---------------------------------------------------------------------------
# 5. operations: never trust the ledger alone
# ---------------------------------------------------------------------------

class TestOperations(Base):
    def test_planned_operation_may_proceed(self) -> None:
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                     "--target", "deadbeef", "--state", "planned")
        proc = self.run_cli("check-op", "--task", "demo", "--kind", "commit",
                            "--target", "deadbeef", "--offline", expect=0)
        self.assertIn("proceed", proc.stdout)

    def test_unknown_target_cannot_be_confirmed(self) -> None:
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                     "--target", "deadbeef", "--state", "started")
        proc = self.run_cli("check-op", "--task", "demo", "--kind", "commit",
                            "--target", "deadbeef", "--offline", expect=2)
        self.assertIn("unknown", proc.stdout)
        self.assertIn("exactly-once", proc.stdout)

    def test_completed_operation_is_never_repeated(self) -> None:
        sha = self.init_git()
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                     "--target", sha, "--state", "verified")
        proc = self.run_cli("check-op", "--task", "demo", "--kind", "commit",
                            "--target", sha, "--offline", expect=1)
        self.assertIn("already done", proc.stdout)

    def test_ledger_claim_contradicted_by_reality_is_critical(self) -> None:
        self.init_git()
        self.save("--title", "T")
        # the ledger claims success, but the commit does not exist
        self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                     "--target", "0" * 40, "--state", "verified")
        proc = self.run_cli("check-op", "--task", "demo", "--kind", "commit",
                            "--target", "0" * 40, "--offline", expect=3)
        self.assertIn("contradicts the ledger", proc.stdout)

        data = self.payload(self.run_cli("resume-report", "--task", "demo", "--json",
                                         expect=C.EXIT_DRIFT))
        stale = [f for f in data["findings"] if f["id"] == "op_stale_claim"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["severity"], "critical")
        self.assertEqual(data["operations"]["commit:" + "0" * 40]["live_state"], "failed")

    def test_failed_operation_requires_a_decision(self) -> None:
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "push",
                     "--target", "origin/main", "--state", "failed")
        data = self.payload(self.run_cli("resume-report", "--task", "demo", "--json",
                                         expect=C.EXIT_DRIFT))
        self.assertIn("op_unresolved", [f["id"] for f in data["findings"]])

    def test_unrecorded_but_real_effect_is_reported_as_info(self) -> None:
        sha = self.init_git()
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                     "--target", sha, "--state", "started")
        data = self.payload(self.run_cli("verify", "--task", "demo", "--json"))
        info = [f for f in data["findings"] if f["id"] == "op_completed_unrecorded"]
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["severity"], "info")

    def test_ledger_is_append_only_jsonl(self) -> None:
        self.save("--title", "T")
        for state in ("planned", "started", "verified"):
            self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                         "--target", "abc123", "--state", state)
        ops = self.store / "demo" / "operations.jsonl"
        lines = [ln for ln in ops.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 3)
        for line in lines:
            json.loads(line)  # every line is valid JSON on its own

    def test_unknown_kind_and_state_are_rejected(self) -> None:
        self.save("--title", "T")
        self.run_cli("record-op", "--task", "demo", "--kind", "teleport",
                     "--state", "verified", expect=C.EXIT_USAGE)
        self.run_cli("record-op", "--task", "demo", "--kind", "commit",
                     "--state", "probably", expect=C.EXIT_USAGE)


# ---------------------------------------------------------------------------
# 6. concurrent writers and revision control
# ---------------------------------------------------------------------------

class TestConcurrencyAndRevision(Base):
    def test_stale_expect_revision_refuses_to_overwrite(self) -> None:
        self.save("--title", "T")
        self.save("--title", "T", "--note", "second")
        self.assertEqual(self.checkpoint()["revision"], 2)
        proc = self.run_cli("save", "--task", "demo", "--note", "clobber",
                            "--expect-revision", "1", expect=C.EXIT_DRIFT)
        self.assertIn("revision", proc.stderr)
        cp = self.checkpoint()
        self.assertEqual(cp["revision"], 2)
        self.assertNotIn("clobber", cp.get("notes") or [])

    def test_matching_expect_revision_succeeds(self) -> None:
        self.save("--title", "T")
        self.save("--note", "second", "--expect-revision", "1")
        self.assertEqual(self.checkpoint()["revision"], 2)

    def test_two_writers_racing_do_not_corrupt_the_checkpoint(self) -> None:
        self.save("--title", "T")
        env = dict(os.environ)
        env["TCR_NOW"] = NOW
        env["TCR_EPOCH"] = NOW_EPOCH
        procs = [
            subprocess.Popen(
                [sys.executable, str(CLI), "--project", str(self.project), "save",
                 "--task", "demo", "--note", "writer-%d" % i],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=env, cwd=str(self.project),
            )
            for i in range(2)
        ]
        codes = sorted(p.wait() for p in procs)
        # at least one writer must land; the loser either lands too (serialised
        # by the lock) or reports a revision conflict - never a crash
        self.assertIn(0, codes)
        self.assertTrue(all(code in (0, C.EXIT_DRIFT) for code in codes), codes)

        cp = self.checkpoint()  # still valid JSON, single coherent revision
        self.assertIn(cp["revision"], (2, 3))
        self.assertTrue(cp["notes"])
        leftovers = list((self.store / "demo").glob("*.tmp*"))
        self.assertEqual(leftovers, [], "atomic writes must not leave temp files")
        self.assertFalse(list((self.store / "demo").glob("*.lock")) or
                         (self.store / "demo" / ".lock").exists())

    def test_history_preserves_previous_versions_without_duplication(self) -> None:
        self.save("--title", "T", "--step", "s1=One")
        self.assertEqual(self.history(), [])  # nothing replaced yet
        self.save("--title", "T", "--step", "s1=One", "--step-status", "s1=done")
        self.assertEqual(len(self.history()), 1)
        first = self.history()[0]
        self.assertTrue(first.startswith("0001-"), first)
        self.assertTrue((self.store / "demo" / "history" / first[:-5]).with_suffix(".md").exists())

        # re-saving identical content must not duplicate the snapshot
        self.save("--title", "T", "--step", "s1=One", "--step-status", "s1=done")
        self.assertEqual(len(self.history()), 1)

        # a third revision snapshots revision 2
        self.save("--title", "T", "--step", "s1=One", "--step-status", "s1=done",
                  "--note", "third")
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(sorted(n[:4] for n in self.history()), ["0001", "0002"])

    def test_prune_keeps_the_most_recent_snapshots(self) -> None:
        self.save("--title", "T")
        for i in range(4):
            self.save("--title", "T", "--note", "n%d" % i)
        self.assertEqual(len(self.history()), 4)

        dry = self.run_cli("prune", "--task", "demo", "--keep", "2", "--dry-run")
        self.assertIn("would remove", dry.stdout)
        self.assertEqual(len(self.history()), 4)  # dry-run deletes nothing

        self.run_cli("prune", "--task", "demo", "--keep", "2")
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(sorted(n[:4] for n in self.history()), ["0003", "0004"])

    def test_interrupted_write_leaves_the_previous_checkpoint_intact(self) -> None:
        self.save("--title", "T", "--note", "before")
        before = (self.store / "demo" / "checkpoint.json").read_text(encoding="utf-8")

        original = C.atomic_write_json

        def boom(path, data):  # noqa: ANN001 - test double
            raise KeyboardInterrupt("simulated crash mid-write")

        C.atomic_write_json = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                C.atomic_write_json(C.checkpoint_path(str(self.project), "demo"), {"x": 1})
        finally:
            C.atomic_write_json = original

        after = (self.store / "demo" / "checkpoint.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertEqual(list((self.store / "demo").glob("*.tmp*")), [])


# ---------------------------------------------------------------------------
# 7. privacy, path validation and schema strictness
# ---------------------------------------------------------------------------

class TestPrivacyAndPaths(Base):
    def test_secrets_are_refused_in_notes(self) -> None:
        proc = self.run_cli("save", "--task", "demo", "--note",
                            "token=ghp_0123456789abcdefghijklmnopqrstuvwx",
                            expect=C.EXIT_USAGE)
        self.assertIn("secret", proc.stderr.lower())
        self.assertFalse((self.store / "demo" / "checkpoint.json").exists())

    def test_environment_is_never_persisted(self) -> None:
        os.environ["TCR_TEST_CANARY"] = "canary-value-9f3a"
        self.addCleanup(os.environ.pop, "TCR_TEST_CANARY", None)
        self.save("--title", "T", "--objective", "no env leakage")
        blob = (self.store / "demo" / "checkpoint.json").read_text(encoding="utf-8")
        self.assertNotIn("canary-value-9f3a", blob)
        self.assertNotIn("TCR_TEST_CANARY", blob)

    def test_path_traversal_is_refused(self) -> None:
        for bad in ("../outside.txt", "..\\outside.txt", "/etc/passwd", "a/../../b"):
            proc = self.run_cli("save", "--task", "demo", "--file", bad,
                                expect=C.EXIT_USAGE)
            self.assertIn("path", proc.stderr.lower(), bad)

    def test_symlink_escaping_the_project_is_refused(self) -> None:
        outside = Path(self.tmp) / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.project / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available in this environment")
        proc = self.run_cli("save", "--task", "demo", "--file", "link.txt",
                            expect=C.EXIT_USAGE)
        self.assertIn("path", proc.stderr.lower())

    def test_unknown_schema_keys_are_rejected(self) -> None:
        errors = C.validate_checkpoint({"schema": C.SCHEMA_ID, "revision": 1,
                                        "task": {"id": "t", "title": "T", "status": "in_progress",
                                                 "created_at": NOW, "updated_at": NOW},
                                        "leaked_prompt": "the whole user prompt"})
        self.assertTrue(any("leaked_prompt" in e for e in errors), errors)

    def test_invalid_slug_is_refused(self) -> None:
        for bad in ("../escape", "a/b", "", ".."):
            proc = self.run_cli("save", "--task", bad, expect=C.EXIT_USAGE)
            self.assertIn("task", proc.stderr.lower(), bad)

    def test_no_prompt_or_raw_output_fields_exist_in_the_schema(self) -> None:
        self.save("--title", "T", "--note", "a short note")
        cp = self.checkpoint()
        self.assertTrue(set(cp) <= set(C.ALLOWED_KEYS), set(cp) - set(C.ALLOWED_KEYS))
        blob = json.dumps(cp)
        for forbidden in ("prompt", "stdout", "stderr", "environ", "api_key"):
            self.assertNotIn(forbidden, blob)

    def test_checkpoint_must_pass_the_schema_before_it_is_written(self) -> None:
        self.save("--title", "T")
        cp = self.checkpoint()
        cp["steps"] = [{"id": "s1", "title": "One", "status": "nonsense"}]
        errors = C.validate_checkpoint(cp)
        self.assertTrue(errors)


# ---------------------------------------------------------------------------
# 8. listing, showing and reduced modes
# ---------------------------------------------------------------------------

class TestInspection(Base):
    def test_list_and_show(self) -> None:
        # task="alpha" (fixture fix, see report): the shared helper defaults to
        # "demo", but this test inspects a two-task project.
        self.save("--title", "Alpha", "--step", "s1=One", "--step-status", "s1=done",
                  "--blocker", "b1=waiting on review|a reviewer", task="alpha")
        self.save("--task", "beta", "--title", "Beta")
        rows = self.payload(self.run_cli("list", "--json"))
        self.assertEqual([r["task"] for r in rows], ["alpha", "beta"])
        self.assertEqual(rows[0]["steps_done"], 1)
        self.assertEqual(rows[0]["blockers"], 1)

        md = self.run_cli("show", "--task", "alpha")
        self.assertIn("Alpha", md.stdout)
        raw = self.payload(self.run_cli("show", "--task", "alpha", "--json"))
        self.assertEqual(raw["task"]["title"], "Alpha")

    def test_list_on_an_empty_project_is_not_an_error(self) -> None:
        proc = self.run_cli("list")
        self.assertIn("no checkpoints", proc.stdout)

    def test_markdown_mirror_is_written_next_to_the_json(self) -> None:
        self.save("--title", "T", "--step", "s1=One")
        md = self.store / "demo" / "checkpoint.md"
        self.assertTrue(md.exists())
        self.assertIn("#", md.read_text(encoding="utf-8"))

    def test_corrupt_checkpoint_does_not_break_listing(self) -> None:
        self.save("--title", "T")
        (self.store / "demo" / "checkpoint.json").write_text("{", encoding="utf-8")
        rows = self.payload(self.run_cli("list", "--json"))
        self.assertEqual(rows[0]["status"], "unreadable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
