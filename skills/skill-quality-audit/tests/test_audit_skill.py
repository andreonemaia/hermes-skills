"""Structural checks exercised end to end through ``audit_target``."""

from __future__ import annotations

import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import support


class WellFormedSkillTest(unittest.TestCase):
    def test_complete_skill_produces_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            report = support.audit(root)
            self.assertEqual(
                report["findings"],
                [],
                msg=f"achados inesperados: {json.dumps(report['findings'], ensure_ascii=False)}",
            )
            self.assertEqual(report["exit_code"], support.common.EXIT_OK)
            self.assertTrue(report["target_unchanged"])

    def test_manifest_covers_every_written_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            report = support.audit(root)
            covered = set(report["manifest"]["entries"])
            expected = {
                str(path.relative_to(root)).replace("\\", "/")
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertTrue(expected.issubset(covered), expected - covered)


class MissingMetadataTest(unittest.TestCase):
    def test_missing_required_keys_are_critical(self):
        skill_md = "---\nname: sample-skill\n---\n\n# Exemplo\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, skill_md=skill_md)
            report = support.audit(root)
            missing = support.findings(report, "SQA-004")
            evidence = " ".join(item["evidence"] for item in missing)
            self.assertIn("description", evidence)
            self.assertIn("version", evidence)
            self.assertIn("platforms", evidence)
            self.assertEqual({item["severity"] for item in missing}, {"critical", "high"})
            self.assertEqual(report["exit_code"], support.common.EXIT_FINDINGS)

    def test_unreadable_frontmatter_is_not_a_clean_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, skill_md="# sem frontmatter\n\nTexto.\n")
            report = support.audit(root)
            self.assertIn("SQA-002", support.codes(report))
            self.assertEqual(support.status(report, "SQA-004"), "not_run")
            self.assertNotEqual(report["exit_code"], support.common.EXIT_OK)


class DocumentationConsistencyTest(unittest.TestCase):
    def test_broken_relative_reference_is_reported_with_line(self):
        skill_md = (
            "---\nname: sample-skill\ndescription: Use when testing references.\n"
            "version: 0.1.0\nplatforms: [linux]\nmetadata:\n  hermes:\n"
            "    tags: [sample]\n    category: testing\n---\n\n"
            "# Exemplo\n\nVer [o contrato](references/missing.md) para detalhes.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, skill_md=skill_md)
            report = support.audit(root)
            broken = support.findings(report, "SQA-013")
            self.assertEqual(len(broken), 1)
            self.assertEqual(broken[0]["file"], "SKILL.md")
            self.assertIsNotNone(broken[0]["line"])

    def test_version_divergence_between_readme_and_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            readme = (root / "README.md").read_text(encoding="utf-8")
            (root / "README.md").write_text(
                readme.replace("Versão documentada: 0.1.0.", "Versão documentada: 0.2.0."),
                encoding="utf-8",
            )
            report = support.audit(root)
            divergence = support.findings(report, "SQA-023")
            self.assertEqual(len(divergence), 1)
            self.assertIn("0.2.0", divergence[0]["evidence"])

    def test_documented_script_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            readme = (root / "README.md").read_text(encoding="utf-8")
            (root / "README.md").write_text(
                readme + "\nRode `python scripts/ghost.py` para começar.\n",
                encoding="utf-8",
            )
            report = support.audit(root)
            self.assertIn("SQA-017", support.codes(report))

    def test_subcommand_divergence_between_docs_and_parser(self):
        cli = (
            '"""Fixture CLI."""\n\nimport argparse\n\n\n'
            "def main(argv=None):\n"
            "    parser = argparse.ArgumentParser()\n"
            '    sub = parser.add_subparsers(dest="cmd")\n'
            '    sub.add_parser("audit")\n'
            "    parser.parse_args(argv)\n"
            "    return 0\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(
                tmp, files={"scripts/cli.py": cli}
            )
            readme = (root / "README.md").read_text(encoding="utf-8")
            (root / "README.md").write_text(
                readme + "\nRode `python scripts/cli.py clean` para limpar.\n",
                encoding="utf-8",
            )
            report = support.audit(root)
            divergences = support.findings(report, "SQA-017")
            self.assertEqual(len(divergences), 1)
            self.assertIn("clean", divergences[0]["evidence"])


class PlatformEvidenceTest(unittest.TestCase):
    def test_declared_platform_without_evidence_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, platforms="macos")
            report = support.audit(root)
            missing = support.findings(report, "SQA-018")
            self.assertEqual(len(missing), 1)
            self.assertIn("macos", missing[0]["evidence"])
            self.assertEqual(missing[0]["validation"], "unverifiable")

    def test_declared_platform_with_evidence_is_reported_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            report = support.audit(root)
            self.assertEqual(support.findings(report, "SQA-018"), [])
            locations = [item["location"] for item in report["reported_only"] if item["id"] == "SQA-018"]
            self.assertTrue(locations, "menção de validação deveria ir para reported_only")


class ExamplesTest(unittest.TestCase):
    def test_synthetic_example_block_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            report = support.audit(root)
            self.assertEqual(support.status(report, "SQA-012"), "executed")
            self.assertEqual(support.findings(report, "SQA-012"), [])

    def test_missing_example_section_is_reported(self):
        skill_md = (
            "---\nname: sample-skill\ndescription: Use when testing examples.\n"
            "version: 0.1.0\nplatforms: [linux]\nmetadata:\n  hermes:\n"
            "    tags: [sample]\n    category: testing\n---\n\n"
            "# Sample\n\nTexto sem seção de exemplo.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, skill_md=skill_md, readme="# vazio\n")
            report = support.audit(root)
            self.assertIn("SQA-012", support.codes(report))


class CliTest(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = support.audit_skill.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_clean_target_exits_zero_and_emits_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            code, out, _ = self._run(["audit", "--target", str(root), "--format", "json"])
            self.assertEqual(code, support.common.EXIT_OK)
            payload = json.loads(out)
            self.assertEqual(payload["findings"], [])
            self.assertEqual(payload["exit_code"], support.common.EXIT_OK)

    def test_findings_exit_one(self):
        skill_md = (
            "---\nname: sample-skill\ndescription: Use when testing exit codes.\n"
            "platforms: [linux]\nmetadata:\n  hermes:\n"
            "    tags: [sample]\n    category: testing\n---\n\n"
            "# Sample\n\n## Exemplo\n\n```text\npedido: auditar a skill\n```\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, skill_md=skill_md)
            code, out, _ = self._run(["audit", "--target", str(root), "--format", "json"])
            self.assertEqual(code, support.common.EXIT_FINDINGS)
            self.assertTrue(json.loads(out)["findings"])

    def test_fail_on_never_suppresses_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, platforms="macos")
            report = support.audit(root, fail_on="never")
            self.assertEqual(report["exit_code"], support.common.EXIT_OK)
            self.assertTrue(report["findings"])

    def test_missing_target_exits_two(self):
        code, _, err = self._run(["audit", "--target", "nao-existe"])
        self.assertEqual(code, support.common.EXIT_TARGET_MISSING)
        self.assertIn("alvo", err)

    def test_manifest_missing_target_exits_two(self):
        code, out, err = self._run(["manifest", "--target", "nao-existe"])
        self.assertEqual(code, support.common.EXIT_TARGET_MISSING)
        self.assertEqual(out, "")
        self.assertIn("alvo", err)

    def test_output_inside_target_is_refused_as_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            code, _, err = self._run(
                ["audit", "--target", str(root), "--out", str(root / "relatorio.json")]
            )
            self.assertEqual(code, support.common.EXIT_USAGE)
            self.assertIn("dentro do alvo", err)
            self.assertFalse((root / "relatorio.json").exists())

    def test_relative_output_is_resolved_before_the_target_check(self):
        # ``--out relatorio.json`` with the process inside the target is the
        # same file as the absolute spelling; comparing raw strings let it pass.
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            with support.working_directory(root):
                code, _, err = self._run(
                    ["audit", "--target", ".", "--out", "relatorio.json"]
                )
            self.assertEqual(code, support.common.EXIT_USAGE, err)
            self.assertIn("dentro do alvo", err)
            self.assertFalse((root / "relatorio.json").exists())

    def test_output_reaching_the_target_through_parent_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            escaped = root / ".." / root.name / "relatorio.json"
            code, _, err = self._run(
                ["audit", "--target", str(root), "--out", str(escaped)]
            )
            self.assertEqual(code, support.common.EXIT_USAGE, err)
            self.assertFalse((root / "relatorio.json").exists())

    def test_output_through_a_link_into_the_target_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            link = pathlib.Path(tmp) / "atalho"
            try:
                link.symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"links não disponíveis neste sistema: {exc}")
            code, _, err = self._run(
                ["audit", "--target", str(root), "--out", str(link / "relatorio.json")]
            )
            self.assertEqual(code, support.common.EXIT_USAGE, err)
            self.assertFalse((root / "relatorio.json").exists())

    def test_unwritable_output_exits_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            code, _, err = self._run(
                [
                    "audit",
                    "--target",
                    str(root),
                    "--out",
                    str(pathlib.Path(tmp) / "sem" / "diretorio" / "relatorio.json"),
                ]
            )
            self.assertEqual(code, support.common.EXIT_OUTPUT)
            self.assertIn("saída", err)

    def test_invalid_fail_on_exits_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            code, _, err = self._run(
                ["audit", "--target", str(root), "--fail-on", "urgente"]
            )
            self.assertEqual(code, support.common.EXIT_USAGE)
            self.assertIn("fail-on", err)

    def test_reference_divergence_is_a_finding_not_a_run_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            manifest = pathlib.Path(tmp) / "manifest.json"
            code, _, _ = self._run(
                ["manifest", "--target", str(root), "--out", str(manifest)]
            )
            self.assertEqual(code, support.common.EXIT_OK)
            (root / "SKILL.md").write_text(
                (root / "SKILL.md").read_text(encoding="utf-8") + "\nnovo texto\n",
                encoding="utf-8",
            )
            report = support.audit(root, reference_manifest=str(manifest))
            # The edit predates the run, so the run comparison is clean and the
            # divergence shows up only against the reference manifest.
            self.assertTrue(report["target_unchanged"])
            self.assertFalse(report["matches_reference"])
            self.assertEqual(report["exit_code"], support.common.EXIT_FINDINGS)


if __name__ == "__main__":
    unittest.main()
