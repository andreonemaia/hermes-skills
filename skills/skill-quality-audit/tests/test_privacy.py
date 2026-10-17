"""Privacy checks: what the scanner sees, and how it reports confidence.

The scanner works on format, so it cannot tell a synthetic fixture value from
a real credential. These tests pin the behaviour the contract promises: the
path of a hit changes the *confidence* and asks the agent to confirm, while
the *severity* keeps the impact the value would have anywhere else.
"""

import json
import shutil
import tempfile
import unittest

import support

audit_skill = support.audit_skill
common = support.common

#: Built at runtime so this file never carries a literal that matches its own
#: patterns when the repository is audited.
GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
AWS_KEY = "AKIA" + "Q" * 16
PRIVATE_PATH = "C:" + "\\Users\\maria.silva\\notes.txt"
EMAIL = "pessoa" + "@" + "empresa-real.com.br"
SESSION = "20261017" + "_" + "120000" + "_" + "abcdef"
INTERNAL_HOST = "build" + ".corp"
PRIVATE_IP = "192" + ".168.10.20"


def _audit_files(files: dict[str, str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return support.audit(support.build_skill(tmp, files=files))


class TestPathClassificationTest(unittest.TestCase):
    def test_test_contexts_are_recognised(self):
        for rel in (
            "tests/test_leak.py",
            "tests/leak.py",
            "test/leak.py",
            "fixtures/leak.md",
            "fixture/leak.md",
            "test_leak.py",
            "scripts/leak_test.py",
        ):
            self.assertTrue(audit_skill._test_path(rel), rel)

    def test_ordinary_paths_are_not_treated_as_tests(self):
        for rel in (
            "latest/notes.md",
            "contest/entry.md",
            "scripts/testing.md",
            "docs/tests-overview.md",
            "SKILL.md",
        ):
            self.assertFalse(audit_skill._test_path(rel), rel)


class PrivacySignalTest(unittest.TestCase):
    def test_credential_in_documentation_is_confirmed(self):
        report = _audit_files({"docs/usage.md": f"token: {GITHUB_TOKEN}\n"})
        found = support.findings(report, "SQA-019")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["confidence"], "confirmed")
        self.assertFalse(found[0]["needs_agent_confirmation"])
        self.assertEqual(found[0]["severity"], "critical")
        self.assertEqual(support.status(report, "SQA-019"), "executed")

    def test_credential_in_a_test_file_keeps_severity_and_loses_certainty(self):
        report = _audit_files({"tests/test_leak.py": f"TOKEN = '{GITHUB_TOKEN}'\n"})
        found = support.findings(report, "SQA-019")
        self.assertEqual(len(found), 1)
        finding = found[0]
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(finding["confidence"], "signal")
        self.assertTrue(finding["needs_agent_confirmation"])
        self.assertIn("teste", finding["severity_justification"])
        self.assertEqual(finding["file"], "tests/test_leak.py")

    def test_severity_is_the_same_with_or_without_a_test_path(self):
        normal = support.findings(
            _audit_files({"docs/usage.md": f"key = {AWS_KEY}\n"}), "SQA-019"
        )
        fixture = support.findings(
            _audit_files({"fixtures/sample.md": f"key = {AWS_KEY}\n"}), "SQA-019"
        )
        self.assertEqual(len(normal), 1)
        self.assertEqual(len(fixture), 1)
        self.assertEqual(normal[0]["severity"], fixture[0]["severity"])
        self.assertEqual(normal[0]["id"], fixture[0]["id"])

    def test_one_location_yields_one_finding_per_code(self):
        report = _audit_files({"docs/usage.md": f"token: {GITHUB_TOKEN}\n"})
        found = support.findings(report, "SQA-019")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["line"], 1)
        self.assertIn("github_token", found[0]["evidence"])

    def test_the_value_is_never_reproduced(self):
        report = _audit_files(
            {
                "docs/usage.md": f"token: {GITHUB_TOKEN}\n",
                "tests/test_leak.py": f"TOKEN = '{GITHUB_TOKEN}'\n",
            }
        )
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(GITHUB_TOKEN, rendered)
        self.assertNotIn(AWS_KEY, rendered)
        for finding in support.findings(report, "SQA-019"):
            self.assertIn("suprimido", finding["evidence"])
            self.assertIn("comprimento", finding["evidence"])


class PrivacyClassTest(unittest.TestCase):
    def test_personal_path_and_email_are_reported(self):
        report = _audit_files(
            {
                "docs/setup.md": f"copie para {PRIVATE_PATH}\ncontato: {EMAIL}\n"
            }
        )
        found = support.findings(report, "SQA-020")
        self.assertEqual(len(found), 2)
        self.assertEqual({item["file"] for item in found}, {"docs/setup.md"})
        for item in found:
            self.assertNotIn(EMAIL, item["evidence"])
            self.assertNotIn("someone", item["evidence"])

    def test_generic_documentation_domains_are_ignored(self):
        report = _audit_files(
            {"docs/setup.md": "contato: user@example.com\noutro: dev@example.org\n"}
        )
        self.assertEqual(support.findings(report, "SQA-020"), [])
        self.assertEqual(support.status(report, "SQA-020"), "executed")

    def test_session_identifier_is_reported(self):
        report = _audit_files({"docs/log.md": f"sessão {SESSION}\n"})
        found = support.findings(report, "SQA-021")
        self.assertEqual(len(found), 1)
        self.assertNotIn(SESSION, found[0]["evidence"])

    def test_internal_infrastructure_is_reported(self):
        report = _audit_files(
            {"docs/infra.md": f"host {INTERNAL_HOST} e ip {PRIVATE_IP}\n"}
        )
        found = support.findings(report, "SQA-022")
        self.assertEqual(len(found), 2)
        self.assertEqual({item["severity"] for item in found}, {"medium"})
        for item in found:
            # Class and length are reported; the value never is.
            self.assertNotIn(INTERNAL_HOST, item["evidence"])
            self.assertNotIn(PRIVATE_IP, item["evidence"])

    def test_version_strings_are_not_private_addresses(self):
        report = _audit_files({"docs/changes.md": "corrigido na v3.10.0.1\n"})
        self.assertEqual(support.findings(report, "SQA-022"), [])

    def test_placeholders_are_not_findings(self):
        report = _audit_files(
            {
                "docs/setup.md": (
                    "password=<your-password>\n"
                    "api_key=$API_KEY\n"
                    "token: {{ token }}\n"
                    "secret: changeme\n"
                )
            }
        )
        self.assertEqual(support.findings(report, "SQA-019"), [])
        self.assertEqual(support.status(report, "SQA-019"), "executed")

    def test_clean_target_reports_the_check_as_executed(self):
        report = _audit_files({"docs/setup.md": "use `--token` na linha de comando\n"})
        for code in ("SQA-019", "SQA-020", "SQA-021", "SQA-022"):
            self.assertEqual(support.status(report, code), "executed")


class PrivacyDirectCallTest(unittest.TestCase):
    """Exercise ``_check_privacy`` itself, not only the report that wraps it."""

    def _context(self, files: dict[str, str]):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = support.build_skill(tmp, files=files)
        return audit_skill.Context(str(root))

    def test_check_privacy_reports_both_confidence_levels(self):
        ctx = self._context(
            {
                "docs/usage.md": f"token: {GITHUB_TOKEN}\n",
                "tests/test_leak.py": f"TOKEN = '{GITHUB_TOKEN}'\n",
            }
        )
        outcome = audit_skill._check_privacy(ctx, "SQA-019")
        self.assertEqual(outcome["status"], "executed")
        self.assertEqual(len(outcome["findings"]), 2)
        by_file = {item["file"]: item for item in outcome["findings"]}
        self.assertEqual(by_file["docs/usage.md"]["confidence"], "confirmed")
        self.assertEqual(by_file["tests/test_leak.py"]["confidence"], "signal")
        self.assertTrue(by_file["tests/test_leak.py"]["needs_agent_confirmation"])
        self.assertEqual(
            by_file["docs/usage.md"]["severity"],
            by_file["tests/test_leak.py"]["severity"],
        )

    def test_check_privacy_ignores_other_classes(self):
        ctx = self._context({"docs/infra.md": f"host {INTERNAL_HOST}\n"})
        outcome = audit_skill._check_privacy(ctx, "SQA-019")
        self.assertEqual(outcome["status"], "executed")
        self.assertEqual(outcome["findings"], [])

    def test_scan_is_memoised_per_context(self):
        ctx = self._context({"docs/usage.md": f"token: {GITHUB_TOKEN}\n"})
        first = audit_skill._check_privacy(ctx, "SQA-019")
        second = audit_skill._check_privacy(ctx, "SQA-019")
        self.assertEqual(first["findings"], second["findings"])


if __name__ == "__main__":
    unittest.main()
