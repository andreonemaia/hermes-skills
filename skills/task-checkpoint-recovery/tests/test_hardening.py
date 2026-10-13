"""Hardening regressions for task-checkpoint-recovery.

Every test below asserts behaviour the skill promises in its own docs: never
crash on a hand-edited store, never claim an effect that was not observed,
never destroy evidence. Written against v0.1.0 as a probe - it fails on the
unfixed code (that is the reproduction) and guards the behaviour afterwards.
"""

from __future__ import annotations

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

sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIX = _load("tcr_fixture", HERE / "test_task_checkpoint_recovery.py")
CK = _load("tcr_checkpoint", CLI)

NOW = FIX.NOW
NOW_EPOCH = FIX.NOW_EPOCH


class HardeningBase(unittest.TestCase):
    """Scratch project plus a CLI runner that owns every flag it passes."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tcr-hard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = Path(self.tmp)

    def cli(self, *args: str, expect: int = 0, now: str = NOW,
            epoch: str = NOW_EPOCH) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["TCR_NOW"] = now
        env["TCR_EPOCH"] = epoch
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *args],
            capture_output=True, text=True, env=env, cwd=str(self.project),
        )
        if expect is not None:
            self.assertEqual(
                proc.returncode, expect,
                "exit %s != %s\nstdout:\n%s\nstderr:\n%s"
                % (proc.returncode, expect, proc.stdout, proc.stderr),
            )
        return proc

    def save(self, *extra: str, expect: int = 0) -> subprocess.CompletedProcess:
        base = () if "--objective" in extra else ("--objective", "objective for tests")
        return self.cli("save", "--task", "demo", *base, *extra, expect=expect)

    def store(self) -> Path:
        return self.project / ".hermes" / "checkpoints"

    def checkpoint(self, task: str = "demo") -> dict:
        return json.loads((self.store() / task / "checkpoint.json").read_text(encoding="utf-8"))

    def store_files(self) -> list[str]:
        root = self.store()
        if not root.is_dir():
            return []
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class TestValidatorHardening(HardeningBase):
    """A hand-edited store must produce errors, never a traceback."""

    def setUp(self) -> None:
        super().setUp()
        self.save("--title", "Hardening")
        self.valid = self.checkpoint()

    def mutate(self, **over) -> dict:
        data = json.loads(json.dumps(self.valid))
        data.update(over)
        return data

    def test_scalar_container_is_reported_not_raised(self) -> None:
        for field in ("steps", "decisions", "blockers", "next_actions", "files",
                      "artifacts", "checks"):
            with self.subTest(field=field):
                problems = C.validate_checkpoint(self.mutate(**{field: 5}))
                self.assertTrue(
                    any(field in p for p in problems),
                    "no problem mentions %s: %r" % (field, problems),
                )

    def test_boolean_revision_is_rejected(self) -> None:
        problems = C.validate_checkpoint(self.mutate(revision=True))
        self.assertTrue(any("revision" in p for p in problems), problems)

    def test_boolean_check_exit_is_rejected(self) -> None:
        problems = C.validate_checkpoint(
            self.mutate(checks=[{"command": "pytest", "exit": True, "at": NOW}]))
        self.assertTrue(any("exit" in p for p in problems), problems)

    def test_artifact_state_and_identity_are_validated(self) -> None:
        problems = C.validate_checkpoint(
            self.mutate(artifacts=[{"kind": "push", "id": 7, "at": 5, "state": "bogus"}]))
        self.assertTrue(any("artifacts[0].state" in p for p in problems), problems)
        self.assertTrue(any("artifacts[0].id" in p for p in problems), problems)

    def test_traversal_in_files_path_is_rejected(self) -> None:
        problems = C.validate_checkpoint(
            self.mutate(files=[{"path": "../outside.txt", "state": "created"}]))
        self.assertTrue(any("files[0].path" in p for p in problems), problems)


class TestCliHardening(HardeningBase):
    def test_prune_refuses_negative_keep(self) -> None:
        self.save("--title", "one")
        self.save("--title", "two")
        before = self.store_files()
        self.cli("prune", "--task", "demo", "--keep", "-1", expect=C.EXIT_USAGE)
        self.assertEqual(self.store_files(), before, "prune --keep -1 destroyed history")

    def test_unparseable_now_is_refused_before_writing(self) -> None:
        self.cli("save", "--task", "demo", "--title", "T", "--objective", "o",
                 "--now", "not-a-timestamp", expect=C.EXIT_USAGE, now="not-a-timestamp")
        self.assertFalse(
            (self.store() / "demo" / "checkpoint.json").exists(),
            "save accepted an unparseable --now and wrote a checkpoint",
        )

    def test_verify_refuses_unparseable_now(self) -> None:
        self.save("--title", "T")
        self.cli("verify", "--task", "demo", "--now", "nope", expect=C.EXIT_USAGE, now="nope")

    def test_lock_release_never_deletes_a_foreign_lock(self) -> None:
        lock = self.store() / "demo" / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("pid=424242\nnonce=someone-else\n", encoding="utf-8")
        C.FileLock(str(lock)).release()
        self.assertTrue(lock.exists(), "release() deleted a lock this object never held")

    def test_lock_release_never_deletes_a_replaced_lock(self) -> None:
        lock = self.store() / "demo" / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        held = C.FileLock(str(lock)).acquire()
        self.addCleanup(held.release)
        # Another process reclaimed the lock and took it over.
        lock.write_text("pid=424242\nnonce=someone-else\n", encoding="utf-8")
        held.release()
        self.assertTrue(lock.exists(), "release() deleted the new holder's lock")
        self.assertIn("someone-else", lock.read_text(encoding="utf-8"))

    def test_quarantine_keeps_earlier_evidence(self) -> None:
        self.addCleanup(lambda: os.environ.pop("TCR_NOW", None))
        os.environ["TCR_NOW"] = NOW
        store = self.store() / "demo"
        store.mkdir(parents=True, exist_ok=True)
        corrupt = store / "checkpoint.json"
        corrupt.write_text("first-corrupt", encoding="utf-8")
        stamp = C.now_iso().replace(":", "").replace("-", "")
        collided = Path(str(corrupt) + ".corrupt-" + stamp)
        collided.write_text("earlier-evidence", encoding="utf-8")
        target = Path(C.quarantine(str(corrupt), "test"))
        self.assertNotEqual(os.path.abspath(str(target)), os.path.abspath(str(collided)),
                            "quarantine overwrote an earlier corrupt copy")
        self.assertEqual(collided.read_text(encoding="utf-8"), "earlier-evidence")
        self.assertEqual(target.read_text(encoding="utf-8"), "first-corrupt")

    def test_read_ops_survives_undecodable_bytes(self) -> None:
        store = self.store() / "demo"
        store.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"op_id": "commit:abc", "kind": "commit",
                           "state": "verified", "target": "abc"})
        (store / "operations.jsonl").write_bytes(
            (good + "\n").encode("utf-8")
            + b'{"op_id":"push:origin/main",\xff\xfe}\n'
            + (good + "\n").encode("utf-8")
        )
        events = C.read_ops(str(self.project), "demo")
        self.assertEqual(len(events), 2, "one undecodable line must not hide good evidence")


class TestPushVerification(HardeningBase):
    def test_push_is_never_verified_without_local_evidence(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        remote = Path(tempfile.mkdtemp(prefix="tcr-remote-"))
        self.addCleanup(shutil.rmtree, remote, True)
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)

        def run(*a: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *a], cwd=str(self.project),
                                  capture_output=True, text=True, check=True)

        run("init", "-q")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "tcr hardening")
        run("config", "commit.gpgsign", "false")
        (self.project / "seed.txt").write_text("seed\n", encoding="utf-8")
        run("add", "seed.txt")
        run("commit", "-q", "-m", "seed")
        run("remote", "add", "origin", str(remote))
        run("push", "-q", "origin", "HEAD:refs/heads/ghost")
        run("update-ref", "-d", "refs/remotes/origin/ghost")

        entry = {"op_id": "push:origin/ghost", "kind": "push",
                 "state": "started", "target": "origin/ghost"}
        state, detail = CK.verify_operation(str(self.project), entry, False)
        self.assertNotEqual(
            state, "verified",
            "push reported verified with no local evidence: %s" % detail,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
