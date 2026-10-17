"""Consistency between the catalogue, the generated docs and the CLI."""

from __future__ import annotations

import io
import json
import pathlib
import re
import unittest
from contextlib import redirect_stdout

import support

CODE_RE = re.compile(r"^SQA-\d{3}$")


def _run_checks_cli(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = support.audit_skill.main(["checks", *argv])
    return code, out.getvalue()


class CatalogueTest(unittest.TestCase):
    def test_codes_are_unique_and_well_formed(self):
        codes = [check.code for check in support.checks.CATALOGUE]
        self.assertEqual(len(codes), len(set(codes)))
        for code in codes:
            self.assertRegex(code, CODE_RE)

    def test_declared_properties_are_known(self):
        for check in support.checks.CATALOGUE:
            self.assertIn(check.default_severity, support.checks.SEVERITIES, check.code)
            self.assertIn(check.method, support.checks.METHODS, check.code)
            self.assertTrue(check.summary.strip(), check.code)
            self.assertTrue(check.how.strip(), check.code)
            self.assertTrue(check.limits.strip(), check.code)

    def test_every_structural_check_is_wired_to_an_implementation(self):
        registry = support.audit_skill.REGISTRY
        integrity = support.audit_skill.INTEGRITY_CODE
        for check in support.checks.CATALOGUE:
            if check.method == "structural" and check.code != integrity:
                self.assertIn(check.code, registry, f"{check.code} sem implementação")

    def test_integrity_check_is_reported_outside_the_registry(self):
        self.assertNotIn(support.audit_skill.INTEGRITY_CODE, support.audit_skill.REGISTRY)
        self.assertEqual(
            support.checks.by_code(support.audit_skill.INTEGRITY_CODE).code,
            support.audit_skill.INTEGRITY_CODE,
        )

    def test_registry_has_no_check_outside_the_catalogue(self):
        known = {check.code for check in support.checks.CATALOGUE}
        self.assertEqual(set(support.audit_skill.REGISTRY) - known, set())

    def test_structural_and_semantic_counts_match_the_cli(self):
        code, out = _run_checks_cli("--format", "json")
        self.assertEqual(code, support.common.EXIT_OK)
        rows = json.loads(out)["checks"]
        self.assertEqual([row["code"] for row in rows],
                         [check.code for check in support.checks.CATALOGUE])
        for row in rows:
            expected = support.checks.by_code(row["code"])
            self.assertEqual(row["method"], expected.method)
            self.assertEqual(row["default_severity"], expected.default_severity)

    def test_method_filter_returns_only_that_method(self):
        code, out = _run_checks_cli("--method", "semantic", "--format", "json")
        self.assertEqual(code, support.common.EXIT_OK)
        rows = json.loads(out)["checks"]
        self.assertTrue(rows)
        self.assertEqual({row["method"] for row in rows}, {"semantic"})


class GeneratedDocumentTest(unittest.TestCase):
    def test_render_is_deterministic_and_cwd_independent(self):
        first = support.render_checks.render()
        with support.working_directory(pathlib.Path(__file__).resolve().parent):
            second = support.render_checks.render()
        self.assertEqual(first, second)
        self.assertEqual(first, support.render_checks.render())

    def test_committed_document_is_up_to_date(self):
        path = support.render_checks.target_path()
        self.assertTrue(path.exists(), f"{path} não existe")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            support.render_checks.render(),
            "references/checks.md desatualizado: rode python scripts/render_checks.py",
        )

    def test_check_mode_agrees_with_the_committed_file(self):
        self.assertEqual(support.render_checks.main(["--check"]), 0)

    def test_header_cites_the_command_that_produces_the_file(self):
        header = support.render_checks.HEADER
        cited = re.search(r"`python (scripts/[\w_]+\.py)", header)
        self.assertIsNotNone(cited, "cabeçalho não cita o comando de geração")
        script = support.SCRIPTS / pathlib.Path(cited.group(1)).name
        self.assertTrue(script.exists(), f"comando citado aponta para {script}, que não existe")
        self.assertEqual(script.name, "render_checks.py")

    def test_document_lists_every_catalogue_code_once(self):
        text = support.render_checks.render()
        table = [line for line in text.splitlines() if line.startswith("| SQA-")]
        self.assertEqual(len(table), len(support.checks.CATALOGUE))
        for check in support.checks.CATALOGUE:
            self.assertEqual(text.count(f"### {check.code} — "), 1, check.code)

    def test_document_summary_matches_catalogue_size(self):
        text = support.render_checks.render()
        total = len(support.checks.CATALOGUE)
        structural = sum(1 for c in support.checks.CATALOGUE if c.method == "structural")
        semantic = total - structural
        self.assertIn(
            f"{total} verificações: {structural} estruturais", text
        )
        self.assertIn(f"{semantic} semânticas", text)

    def test_regeneration_ignores_the_working_tree(self):
        """A stray file in the cwd must not change the rendered document."""
        with support.working_directory(support.SCRIPTS.parent):
            baseline = support.render_checks.render()
        self.assertEqual(baseline, support.render_checks.render())


class FindingFormatTest(unittest.TestCase):
    REQUIRED = {"id", "check", "category", "method", "severity",
                "severity_justification", "file", "line", "location", "evidence",
                "impact", "recommendation", "validation", "confidence",
                "needs_agent_confirmation", "target"}

    def _finding(self, **overrides):
        kwargs = dict(
            code="SQA-004",
            file="SKILL.md",
            line=1,
            evidence="version",
            impact="o agente não sabe qual contrato a skill declara",
            recommendation="declarar version no frontmatter",
            severity_justification="metadado obrigatório ausente",
        )
        kwargs.update(overrides)
        return support.common.finding(**kwargs)

    def test_finding_carries_every_documented_field(self):
        finding = self._finding()
        self.assertTrue(self.REQUIRED.issubset(finding), self.REQUIRED - set(finding))
        self.assertEqual(finding["id"], "SQA-004")
        self.assertIn(finding["severity"], support.checks.SEVERITIES)
        self.assertEqual(finding["location"], "SKILL.md:1")
        self.assertFalse(finding["needs_agent_confirmation"])

    def test_signal_confidence_asks_for_agent_confirmation(self):
        finding = self._finding(confidence="signal", validation="unverifiable")
        self.assertTrue(finding["needs_agent_confirmation"])
        self.assertEqual(finding["validation"], "unverifiable")

    def test_severity_can_be_raised_but_not_invented(self):
        raised = self._finding(severity="critical")
        self.assertEqual(raised["severity"], "critical")
        with self.assertRaises(support.common.UsageError):
            self._finding(severity="urgente")

    def test_evidence_is_truncated_to_the_documented_budget(self):
        finding = self._finding(evidence="x" * (support.common.MAX_EVIDENCE + 50))
        self.assertLessEqual(len(finding["evidence"]), support.common.MAX_EVIDENCE)

    def test_invalid_validation_state_is_refused(self):
        with self.assertRaises(support.common.UsageError):
            self._finding(validation="talvez")

    def test_report_records_every_catalogue_check(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            report = support.audit(support.build_skill(tmp))
        recorded = {entry["code"] for entry in report["checks_run"]}
        expected = {
            check.code
            for check in support.checks.CATALOGUE
            if check.method == "structural"
        }
        self.assertEqual(recorded, expected)


if __name__ == "__main__":
    unittest.main()
