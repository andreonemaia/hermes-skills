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
import pathlib
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
            self.assertIsNone(report["target_unchanged"])
            self.assertNotIn("SQA-026", support.codes(report))
            self.assertNotEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)


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
            self.assertFalse(report["target_unchanged"])
            self.assertTrue(report["manifest_diff"]["identical"])
            self.assertEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)
            finding = support.findings(report, "SQA-026")[0]
            self.assertIn("referência", finding["evidence"])
            self.assertNotIn("durante a auditoria", finding["evidence"])
            self.assertIn("não", support.common.render_markdown(report).split(
                "Confere com o manifesto de referência: "
            )[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
