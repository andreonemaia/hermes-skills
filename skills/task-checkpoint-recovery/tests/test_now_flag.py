"""Regression tests for the ``--now`` override on every subcommand.

The frozen specification file (``test_task_checkpoint_recovery.py``) pins time
through the ``TCR_NOW``/``TCR_EPOCH`` environment variables and never passes
``--now`` on the command line, so the flag itself was untested. These tests
assert that ``--now`` is a deterministic override that wins over the
environment for ``save``, ``verify``, ``resume-report`` and ``record-op``.

Run with::

    python -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import json
import unittest

from test_task_checkpoint_recovery import NOW, Base

STAMP = "2020-01-01T00:00:00+00:00"
NEAR = "2020-01-01T00:05:00+00:00"
FAR = "2026-10-13T12:00:00+00:00"


def _ids(payload):
    return [f["id"] for f in payload.get("findings", [])]


class TestNowOverride(Base):
    """``--now`` must beat the environment and drive staleness."""

    def test_save_honours_now_over_the_environment(self) -> None:
        self.run_cli("save", "--task", "demo", "--objective", "pin time",
                     "--now", STAMP, expect=0)
        cp = self.checkpoint()
        self.assertEqual(cp["task"]["created_at"], STAMP)
        self.assertEqual(cp["task"]["updated_at"], STAMP)
        self.assertNotEqual(cp["task"]["created_at"], NOW)

    def test_verify_now_flag_controls_staleness(self) -> None:
        self.run_cli("save", "--task", "demo", "--objective", "pin time",
                     "--now", STAMP, expect=0)
        near = self.run_cli("verify", "--task", "demo", "--now", NEAR, "--json", expect=0)
        self.assertNotIn("stale_checkpoint", _ids(self.payload(near)))
        far = self.run_cli("verify", "--task", "demo", "--now", FAR, "--json", expect=1)
        self.assertIn("stale_checkpoint", _ids(self.payload(far)))

    def test_resume_report_now_flag_controls_staleness(self) -> None:
        self.run_cli("save", "--task", "demo", "--objective", "pin time",
                     "--now", STAMP, expect=0)
        near = self.run_cli("resume-report", "--task", "demo", "--now", NEAR,
                            "--json", expect=0)
        self.assertNotIn("stale_checkpoint", _ids(self.payload(near)))
        far = self.run_cli("resume-report", "--task", "demo", "--now", FAR,
                           "--json", expect=1)
        self.assertIn("stale_checkpoint", _ids(self.payload(far)))

    def test_record_op_now_flag_stamps_the_ledger(self) -> None:
        self.run_cli("save", "--task", "demo", "--objective", "pin time", expect=0)
        self.run_cli("record-op", "--task", "demo", "--kind", "push",
                     "--target", "origin/main", "--state", "verified",
                     "--verification", "git ls-remote origin main",
                     "--now", STAMP, expect=0)
        ledger = self.store / "demo" / "operations.jsonl"
        ops = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(ops[-1]["at"], STAMP)

    def test_bare_epoch_now_is_normalised_in_the_store(self) -> None:
        """A bare epoch must be stored as ISO-8601, never echoed verbatim.

        Echoing it verbatim used to write ``"updated_at": "1577836800"``, which
        the store contract (ISO-8601) cannot read back.
        """
        self.run_cli("save", "--task", "demo", "--objective", "pin time",
                     "--now", "1577836800", expect=0)
        cp = self.checkpoint()
        self.assertEqual(cp["task"]["created_at"], STAMP)
        self.assertEqual(cp["task"]["updated_at"], STAMP)

    def test_bare_epoch_now_still_controls_staleness(self) -> None:
        """A bare epoch must not silently disable the staleness check.

        ``_now_epoch`` reads the override through ``_timestamp_epoch``, which
        only understood ISO-8601; an epoch collapsed to ``0.0``, making the age
        negative and hiding a stale checkpoint behind ``exit 0``.
        """
        self.run_cli("save", "--task", "demo", "--objective", "pin time",
                     "--now", STAMP, expect=0)
        far = self.run_cli("verify", "--task", "demo", "--now", "1791892800",
                           "--json", expect=1)
        self.assertIn("stale_checkpoint", _ids(self.payload(far)))

    def test_store_holding_a_legacy_epoch_is_read_back(self) -> None:
        """A store written before the normalisation is still read correctly.

        Reading the epoch back as ``0.0`` dated such a checkpoint to 1970 and
        reported it stale for ever; the tolerant reader must not.
        """
        self.run_cli("save", "--task", "demo", "--objective", "pin time", expect=0)
        path = self.store / "demo" / "checkpoint.json"
        cp = json.loads(path.read_text(encoding="utf-8"))
        cp["task"]["updated_at"] = "1791892800"  # 2026-10-13T12:00:00+00:00
        path.write_text(json.dumps(cp), encoding="utf-8")
        near = self.run_cli("verify", "--task", "demo",
                            "--now", "2026-10-13T12:01:00+00:00",
                            "--json", expect=0)
        self.assertNotIn("stale_checkpoint", _ids(self.payload(near)))
