"""Read-only evidence: before/after manifests, reference manifests, unreadable content.

The three states of ``target_unchanged``/``matches_reference`` are the contract:
``True`` (verified stable), ``False`` (verified difference) and ``None``
(unreadable content, so stability cannot be claimed). These tests pin that
``None`` never collapses into either boolean and that the reference comparison
stays independent from the before/after comparison.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import support


def write_manifest(root: pathlib.Path, destination: pathlib.Path) -> str:
    manifest = support.common.build_manifest(str(root))
    destination.write_text(json.dumps(manifest), encoding="utf-8")
    return str(destination)


class StableTargetTest(unittest.TestCase):
    def test_identical_readable_target_is_verified_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            report = support.audit(root)
            self.assertTrue(report["target_unchanged"])
            self.assertTrue(report["manifest_diff"]["identical"])
            self.assertTrue(report["manifest_diff"]["complete"])
            self.assertEqual(report["manifest_diff"]["unverifiable"], [])
            self.assertEqual(report["manifest_unreadable"], [])
            self.assertIsNone(report["matches_reference"])
            self.assertNotEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            self.assertEqual(support.status(report, "SQA-026"), "executed")

    def test_manifest_digests_match_when_nothing_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            before = support.common.build_manifest(str(root))
            after = support.common.build_manifest(str(root))
            diff = support.common.diff_manifests(before, after)
            self.assertTrue(diff["identical"])
            self.assertTrue(diff["complete"])
            self.assertEqual(diff["digest_before"], diff["digest_after"])

    def test_changed_content_is_reported_with_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            before = support.common.build_manifest(str(root))
            (root / "README.md").write_text("# outro texto\n", encoding="utf-8")
            after = support.common.build_manifest(str(root))
            diff = support.common.diff_manifests(before, after)
            self.assertFalse(diff["identical"])
            self.assertEqual(diff["changed"], ["README.md"])
            self.assertEqual(diff["unverifiable"], [])
            self.assertNotEqual(diff["digest_before"], diff["digest_after"])


class UnreadableContentTest(unittest.TestCase):
    """Unreadable content is simulated deterministically, not via host permissions."""

    def test_sha256_file_returns_none_when_open_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "arquivo.txt"
            path.write_text("conteúdo\n", encoding="utf-8")
            with mock.patch(
                "builtins.open", side_effect=PermissionError("simulado")
            ):
                self.assertIsNone(support.common.sha256_file(str(path)))

    def test_unreadable_entry_is_unverifiable_not_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            before = support.common.build_manifest(str(root))
            after = support.common.build_manifest(str(root))
            after["entries"]["README.md"]["sha256"] = None
            diff = support.common.diff_manifests(before, after)
            self.assertEqual(diff["unverifiable"], ["README.md"])
            self.assertEqual(diff["changed"], [])
            self.assertFalse(diff["complete"])
            self.assertIn("README.md", support.common.unreadable_entries(after["entries"]))

    def test_unreadable_content_makes_stability_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            real = support.common.sha256_file
            target = root / "README.md"

            def fake(path):
                if pathlib.Path(path) == target:
                    return None
                return real(path)

            with mock.patch.object(support.common, "sha256_file", side_effect=fake):
                report = support.audit(root)

            self.assertIsNone(report["target_unchanged"])
            self.assertEqual(report["manifest_diff"]["unverifiable"], ["README.md"])
            self.assertEqual(report["manifest_unreadable"], ["README.md"])
            self.assertNotEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            self.assertTrue(
                any(item["id"] == "SQA-026" for item in report["limits"]),
                msg="conteúdo inacessível precisa virar limite declarado",
            )
            self.assertEqual(support.status(report, "SQA-026"), "unverifiable")
            markdown = support.common.render_markdown(report)
            self.assertIn("inconclusivo", markdown)
            self.assertIn("Sem conteúdo legível", markdown)

    def test_unreadable_reference_is_inconclusive_not_divergent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            real = support.common.sha256_file
            target = root / "README.md"

            def fake(path):
                if pathlib.Path(path) == target:
                    return None
                return real(path)

            with mock.patch.object(support.common, "sha256_file", side_effect=fake):
                report = support.audit(root, reference_manifest=str(reference))

            self.assertIsNone(report["matches_reference"])
            self.assertTrue(report["reference_compared"])
            self.assertIsNone(report["target_unchanged"])
            self.assertNotIn("SQA-026", support.codes(report))
            self.assertNotEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            markdown = support.common.render_markdown(report)
            self.assertIn(
                "Confere com o manifesto de referência: inconclusivo", markdown
            )


class RunComparisonTest(unittest.TestCase):
    def test_change_during_the_run_exits_five(self):
        """A file appearing mid-run is an observed change, so exit 5."""
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            real = support.common.build_manifest
            calls = {"n": 0}

            def mutating(path):
                calls["n"] += 1
                if calls["n"] == 2:
                    # Deterministic mid-run mutation: no host permission games.
                    (pathlib.Path(path) / "novo.md").write_text("x\n", encoding="utf-8")
                return real(path)

            with mock.patch.object(support.common, "build_manifest", side_effect=mutating):
                report = support.audit(root)

            self.assertFalse(report["target_unchanged"])
            self.assertFalse(report["manifest_diff"]["identical"])
            self.assertEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            finding = support.findings(report, "SQA-026")[0]
            self.assertIn("durante a auditoria", finding["evidence"])
            self.assertEqual(support.status(report, "SQA-026"), "executed_with_findings")


class NotComparedTest(unittest.TestCase):
    def test_default_run_never_claims_a_reference_comparison(self):
        """Without ``--check-unchanged`` there is no reference fact to report."""
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            report = support.audit(root)

            self.assertIsNone(report["matches_reference"])
            self.assertFalse(report["reference_compared"])
            integrity = [
                row for row in report["checks_run"] if row["code"] == "SQA-026"
            ][0]
            self.assertNotIn("referência", integrity["reason"])
            self.assertNotIn("ilegível", integrity["reason"])
            # No reference was requested, so the integrity check ran and is not
            # "unverifiable": only an incomplete comparison may say that.
            self.assertEqual(integrity["status"], "executed")
            markdown = support.common.render_markdown(report)
            self.assertIn("não comparado", markdown)


class UnreadableSymlinkTest(unittest.TestCase):
    def test_symlink_with_unknown_target_is_unverifiable_not_equal(self):
        """``None`` target is an unknown value: two of them never prove equality."""
        before = {"entries": {"atalho": {"kind": "symlink", "target": None}}}
        after = {"entries": {"atalho": {"kind": "symlink", "target": None}}}
        self.assertIn("atalho", support.common.unreadable_entries(before["entries"]))
        diff = support.common.diff_manifests(before, after)
        self.assertEqual(diff["unverifiable"], ["atalho"])
        self.assertEqual(diff["changed"], [])
        self.assertFalse(diff["complete"])
        self.assertTrue(diff["identical"])


@unittest.skipUnless(os.name == "nt", "junctions are a Windows construct")
class JunctionTraversalTest(unittest.TestCase):
    """A junction is a reparse point: report it, never walk into it."""

    def test_junction_is_reported_and_its_target_is_not_audited(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = support.build_skill(tmp)
            outside_path = pathlib.Path(outside)
            (outside_path / "outside-secret.txt").write_text(
                "SEGREDO-EXTERNO", encoding="utf-8"
            )
            link = root / "references" / "linked"
            link.parent.mkdir(parents=True, exist_ok=True)
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if created.returncode != 0:
                self.skipTest(f"mklink /J indisponível: {created.stderr.strip()}")
            try:
                # Same arity as production (``audit_skill.Context``): the walk
                # error channel is passed explicitly, and an empty result pins
                # that the junction itself did not become a traversal failure.
                walk_errors = []
                entries = support.common.walk_target(str(root), walk_errors)
                self.assertEqual(walk_errors, [])
                by_rel = {entry["rel"]: entry for entry in entries}
                self.assertEqual(by_rel["references/linked"]["kind"], "symlink")
                self.assertFalse(
                    any(rel.startswith("references/linked/") for rel in by_rel)
                )
                report = support.audit(root)
                serialised = json.dumps(report)
                self.assertNotIn("outside-secret.txt", serialised)
                self.assertNotIn("SEGREDO-EXTERNO", serialised)
            finally:
                os.rmdir(link)


class ReferenceManifestTest(unittest.TestCase):
    def test_matching_reference_confirms_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            report = support.audit(root, reference_manifest=str(reference))
            self.assertTrue(report["matches_reference"])
            self.assertTrue(report["target_unchanged"])

    def test_stable_target_diverging_from_reference_is_reported(self):
        """The target stays put during the run but differs from an old manifest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            (root / "README.md").write_text("# versão nova\n", encoding="utf-8")

            report = support.audit(root, reference_manifest=str(reference))

            self.assertFalse(report["matches_reference"])
            # The target did not move while the audit ran: the run comparison
            # stays true and exit 5 stays reserved for an observed change.
            self.assertTrue(report["target_unchanged"])
            self.assertTrue(report["manifest_diff"]["identical"])
            self.assertEqual(report["exit_code"], support.common.EXIT_FINDINGS)
            finding = support.findings(report, "SQA-026")[0]
            self.assertIn("referência", finding["evidence"])
            self.assertNotIn("durante a auditoria", finding["evidence"])
            self.assertIn("não", support.common.render_markdown(report).split(
                "Confere com o manifesto de referência: "
            )[1].split("\n")[0])
            self.assertTrue(report["reference_compared"])

    def test_never_keeps_the_divergence_visible_with_a_zero_exit(self):
        """``never`` silences the exit code, not the reference fact."""
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            (root / "README.md").write_text("# versão nova\n", encoding="utf-8")

            report = support.audit(
                root, reference_manifest=str(reference), fail_on="never"
            )

            self.assertFalse(report["matches_reference"])
            self.assertTrue(report["reference_compared"])
            self.assertEqual(report["exit_code"], support.common.EXIT_OK)
            self.assertEqual(support.codes(report).count("SQA-026"), 1)
            self.assertEqual(support.findings(report, "SQA-026")[0]["severity"], "critical")

    def test_never_does_not_suppress_a_change_observed_during_the_run(self):
        """Exit 5 reports integrity, not a finding: ``never`` leaves it alone."""
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            real = support.common.build_manifest
            calls = {"n": 0}

            def mutating(path):
                calls["n"] += 1
                if calls["n"] == 2:
                    (pathlib.Path(path) / "novo.md").write_text("x\n", encoding="utf-8")
                return real(path)

            with mock.patch.object(support.common, "build_manifest", side_effect=mutating):
                report = support.audit(
                    root, reference_manifest=str(reference), fail_on="never"
                )

            self.assertFalse(report["target_unchanged"])
            self.assertEqual(calls["n"], 2, msg="premissa: um snapshot antes, um depois")
            self.assertEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)

    def test_divergence_and_run_change_stay_two_separate_findings(self):
        """Reference divergence never hides a change observed during the run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            (root / "README.md").write_text("# versão nova\n", encoding="utf-8")
            real = support.common.build_manifest
            calls = {"n": 0}

            def mutating(path):
                calls["n"] += 1
                if calls["n"] == 2:
                    (pathlib.Path(path) / "novo.md").write_text("x\n", encoding="utf-8")
                return real(path)

            with mock.patch.object(support.common, "build_manifest", side_effect=mutating):
                report = support.audit(root, reference_manifest=str(reference))

            self.assertFalse(report["matches_reference"])
            self.assertFalse(report["target_unchanged"])
            self.assertEqual(calls["n"], 2, msg="premissa: um snapshot antes, um depois")
            self.assertEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            integrity = support.findings(report, "SQA-026")
            self.assertEqual(len(integrity), 2)
            evidences = " ".join(item["evidence"] for item in integrity)
            self.assertIn("referência", evidences)
            self.assertIn("durante a auditoria", evidences)


class IntegrityStatusPrecedenceTest(unittest.TestCase):
    """An observed finding outranks a blind comparison in the check status."""

    def test_proven_change_with_inconclusive_reference_is_not_unverifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            write_manifest(root, reference)
            real_hash = support.common.sha256_file
            real_manifest = support.common.build_manifest
            unreadable = root / "README.md"
            calls = {"n": 0}

            def fake_hash(path):
                if pathlib.Path(path) == unreadable:
                    return None
                return real_hash(path)

            def mutating(path):
                calls["n"] += 1
                if calls["n"] == 2:
                    (pathlib.Path(path) / "novo.md").write_text("x\n", encoding="utf-8")
                return real_manifest(path)

            with mock.patch.object(support.common, "sha256_file", side_effect=fake_hash):
                with mock.patch.object(
                    support.common, "build_manifest", side_effect=mutating
                ):
                    report = support.audit(root, reference_manifest=str(reference))

            self.assertTrue(report["reference_compared"])
            self.assertIsNone(report["matches_reference"])
            self.assertIs(report["target_unchanged"], False)
            self.assertEqual(calls["n"], 2, msg="premissa: um snapshot antes, um depois")
            self.assertEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            self.assertEqual(len(support.findings(report, "SQA-026")), 1)
            self.assertEqual(
                support.status(report, "SQA-026"),
                "executed_with_findings",
                msg="mudança comprovada não pode ser relatada como lacuna de cobertura",
            )

    def test_inconclusive_reference_without_findings_stays_unverifiable(self):
        """No finding + blind reference: equality before/after is not enough."""

        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            reference = pathlib.Path(tmp) / "reference.json"
            real_hash = support.common.sha256_file
            unreadable = root / "README.md"

            def blind_reference_hash(path):
                # The reference manifest is written with a null hash for one
                # file, so comparing against it cannot be confirmed later.
                if pathlib.Path(path) == unreadable:
                    return None
                return real_hash(path)

            with mock.patch.object(
                support.common, "sha256_file", side_effect=blind_reference_hash
            ):
                write_manifest(root, reference)

            # The patch is deliberately out of scope here: only the reference
            # manifest is written blind, so the audit itself still reads the
            # target normally and the comparison stays inconclusive.
            report = support.audit(root, reference_manifest=str(reference))

            self.assertTrue(report["reference_compared"])
            self.assertIsNone(report["matches_reference"])
            self.assertIs(report["target_unchanged"], True)
            self.assertEqual(support.findings(report, "SQA-026"), [])
            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(
                support.status(report, "SQA-026"),
                "unverifiable",
                msg="referência ilegível mantém a verificação inconclusiva",
            )


if __name__ == "__main__":
    unittest.main()
