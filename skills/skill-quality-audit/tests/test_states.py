"""``Context.text_state`` distinctions and the textual signal behind SQA-016.

A missing file, a path that is not a file, an unreadable file, a binary file and
a file that is not valid UTF-8 are five different facts. Collapsing them into a
single ``None`` would let the report claim "file absent" for a file that exists.
"""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
