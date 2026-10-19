"""``Context.text_state`` distinctions and the textual signal behind SQA-016.

A missing file, a path that is not a file, an unreadable file, a binary file and
a file that is not valid UTF-8 are five different facts. Collapsing them into a
single ``None`` would let the report claim "file absent" for a file that exists.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest import mock

import support

Context = support.audit_skill.Context


class TextStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = support.build_skill(self._tmp.name)
        self.ctx = Context(str(self.root))

    def test_readable_text_is_ok(self):
        text, state = self.ctx.text_state("README.md")
        self.assertEqual(state, "ok")
        self.assertIn("sample-skill", text)

    def test_absent_path_is_missing(self):
        text, state = self.ctx.text_state("NAO_EXISTE.md")
        self.assertIsNone(text)
        self.assertEqual(state, "missing")

    def test_directory_is_not_a_file(self):
        (self.root / "pasta").mkdir()
        ctx = Context(str(self.root))
        text, state = ctx.text_state("pasta")
        self.assertIsNone(text)
        self.assertEqual(state, "not_a_file")

    def test_unreadable_is_distinct_from_missing(self):
        real = support.common.read_text_state

        def fake(path):
            if pathlib.Path(path).name == "README.md":
                return None, "unreadable"
            return real(path)

        with mock.patch.object(support.common, "read_text_state", side_effect=fake):
            ctx = Context(str(self.root))
            text, state = ctx.text_state("README.md")
        self.assertIsNone(text)
        self.assertEqual(state, "unreadable")

    def test_excluded_suffix_is_not_counted_again_as_skipped(self):
        (self.root / "imagem.png").write_bytes(b"\x00\x01")
        ctx = Context(str(self.root))
        text, state = ctx.text_state("imagem.png")
        self.assertIsNone(text)
        self.assertEqual(state, "binary")
        self.assertNotIn("imagem.png", [item["rel"] for item in ctx.coverage["skipped"]])
        self.assertIn(
            "imagem.png",
            [item["rel"] for item in ctx.coverage["excluded_suffixes"]],
        )

    def test_symlink_is_declared_in_coverage(self):
        try:
            os.symlink("README.md", self.root / "atalho")
        except (OSError, NotImplementedError):
            self.skipTest("symlink indisponível neste host")
        ctx = Context(str(self.root))
        self.assertIn("atalho", ctx.coverage["symlinks"])

    def test_bom_is_stripped_from_decoded_text(self):
        (self.root / "bom.md").write_bytes("\ufeff# título\n".encode("utf-8"))
        text, state = Context(str(self.root)).text_state("bom.md")
        self.assertEqual(state, "ok")
        self.assertEqual(text, "# título\n")

    def test_binary_file_is_its_own_state(self):
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02")
        ctx = Context(str(self.root))
        text, state = ctx.text_state("blob.bin")
        self.assertIsNone(text)
        self.assertEqual(state, "binary")

    def test_undecodable_file_is_its_own_state(self):
        # cp1252 leaves 0x81, 0x8d and 0x9d undefined, so no encoding applies.
        (self.root / "ruido.md").write_bytes(b"\x81\x8d\x9d texto\n")
        ctx = Context(str(self.root))
        text, state = ctx.text_state("ruido.md")
        self.assertIsNone(text)
        self.assertEqual(state, "undecodable")

    def test_oversized_file_is_declared_instead_of_read(self):
        # The read is bounded: a text-looking file above the cap must become a
        # declared coverage gap, not an out-of-memory exit 6.
        (self.root / "grande.md").write_text("x" * 200, encoding="utf-8")
        ctx = Context(str(self.root))
        with mock.patch.object(support.common, "MAX_TEXT_BYTES", 64):
            text, state = ctx.text_state("grande.md")
        self.assertIsNone(text)
        self.assertEqual(state, "oversized")
        self.assertIn(
            {"rel": "grande.md", "reason": "oversized"}, ctx.coverage["skipped"]
        )

    def test_text_helper_matches_state(self):
        self.assertIsNone(self.ctx.text("NAO_EXISTE.md"))
        self.assertIsNotNone(self.ctx.text("README.md"))

    def test_unreadable_skill_md_is_not_reported_as_missing(self):
        real = support.common.read_text_state

        def fake(path):
            if pathlib.Path(path).name == "SKILL.md":
                return None, "unreadable"
            return real(path)

        with mock.patch.object(support.common, "read_text_state", side_effect=fake):
            report = support.audit(self.root)
        finding = support.findings(report, "SQA-001")[0]
        self.assertIn("não pôde ser decodificado", finding["evidence"])
        self.assertIn("unreadable", finding["evidence"])
        self.assertNotIn("ausente", finding["evidence"])


class TestsSignalTest(unittest.TestCase):
    """SQA-016 is a textual signal: it must not claim discovery or execution."""

    def test_mention_without_running_is_reported_as_textual_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(
                tmp,
                files={
                    "tests/test_mencao.py": (
                        "# este arquivo cita unittest mas não o importa\n"
                        "VALOR = 'unittest'\n"
                    )
                },
            )
            report = support.audit(root)
            self.assertNotIn("SQA-016", support.codes(report))
            reason = next(
                row["reason"] for row in report["checks_run"] if row["code"] == "SQA-016"
            )
            self.assertIn("sinal textual", reason)
            for claim in ("passou", "executou", "rodou", "verde"):
                self.assertNotIn(claim, reason)

    def test_no_framework_mention_produces_the_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(
                tmp,
                files={"tests/test_sem_framework.py": "def test_x():\n    assert True\n"},
                drop=("tests/test_run.py",),
            )
            report = support.audit(root)
            finding = support.findings(report, "SQA-016")[0]
            self.assertIn("sem import de unittest ou pytest", finding["evidence"])
            self.assertIn("não são executáveis", finding["impact"])

    def test_catalogue_declares_the_same_limit(self):
        catalogue = {item.code: item for item in support.checks.CATALOGUE}
        limits = catalogue["SQA-016"].limits
        self.assertIn("Sinal textual", limits)
        self.assertIn("não prova de descoberta", limits)


class UnreadableEvidenceTest(unittest.TestCase):
    """An absence claim must not rest on a file that could not be read."""

    def _unreadable(self, name: str):
        real = support.common.read_text_state

        def fake(path):
            if pathlib.Path(path).name == name:
                return None, "unreadable"
            return real(path)

        return mock.patch.object(support.common, "read_text_state", side_effect=fake)

    def test_only_unreadable_test_file_is_unverifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(
                tmp,
                files={"tests/test_x.py": "def test_x():\n    pass\n"},
                drop=("tests/test_run.py",),
            )
            with self._unreadable("test_x.py"):
                report = support.audit(root)
        self.assertNotIn("SQA-016", support.codes(report))
        self.assertEqual(support.status(report, "SQA-016"), "unverifiable")
        self.assertTrue(
            any(
                item["id"] == "SQA-016" and item["file"] == "tests/test_x.py"
                for item in report["limits"]
            )
        )

    def test_mixed_readable_and_unreadable_tests_qualify_the_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(
                tmp,
                files={
                    "tests/test_a.py": "VALOR = 1\n",
                    "tests/test_b.py": "VALOR = 2\n",
                },
                drop=("tests/test_run.py",),
            )
            with self._unreadable("test_a.py"):
                report = support.audit(root)
        finding = support.findings(report, "SQA-016")[0]
        self.assertIn("legível(is)", finding["evidence"])
        self.assertNotIn("2 arquivo(s)", finding["evidence"])
        self.assertTrue(
            any(item["id"] == "SQA-016" and item["file"] == "tests/test_a.py" for item in report["limits"])
        )

    def test_unreadable_document_limits_the_placeholder_absence_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            with self._unreadable("README.md"):
                report = support.audit(root)
        reason = next(
            row["reason"] for row in report["checks_run"] if row["code"] == "SQA-024"
        )
        self.assertIn("legível(is)", reason)
        self.assertTrue(
            any(item["id"] == "SQA-024" and item["file"] == "README.md" for item in report["limits"])
        )


class IntegrityStatusTest(unittest.TestCase):
    """A hole without an integrity finding is unverifiable, not a result."""

    def test_hole_without_finding_is_unverifiable(self):
        real = support.common.walk_target

        def fake(root, errors):
            entries = real(root, errors)
            errors.append({"rel": "references/privado", "reason": "PermissionError"})
            return entries

        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            with mock.patch.object(support.common, "walk_target", side_effect=fake):
                report = support.audit(root)
        self.assertNotIn("SQA-026", support.codes(report))
        self.assertEqual(support.status(report, "SQA-026"), "unverifiable")


if __name__ == "__main__":
    unittest.main()
