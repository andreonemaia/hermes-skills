"""Regressions pinned by the contract: what is a signal, and what is prose.

Each test names the distinction it protects, so a future heuristic that widens
or narrows detection fails here instead of silently changing the report.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import tempfile
import unittest
from unittest import mock

import support

#: Built at runtime so this file never carries a literal that matches the
#: scanner's own patterns when the repository is audited.
GITHUB_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
SYNTHETIC_CPF = "529" + ".982" + ".247" + "-25"

CLI_PY = '''#!/usr/bin/env python3
"""Script with subcommands, used to check documented invocations."""

import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sample CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("render")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _readme(body: str) -> str:
    return f"# sample-skill\n\nVersão documentada: 0.1.0.\n\n{body}\n"


def _skill_md(body: str, tags: str = "[audit]") -> str:
    return (
        "---\n"
        "name: sample-skill\n"
        'description: "Sample skill used by the audit fixtures."\n'
        "version: 0.1.0\n"
        "platforms: [linux]\n"
        f"tags: {tags}\n"
        "---\n\n"
        f"# sample-skill\n\n{body}\n"
    )


def _audit(**kwargs) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return support.audit(support.build_skill(tmp, **kwargs))


class PlaceholderTest(unittest.TestCase):
    def test_braced_placeholder_is_documentation(self):
        report = _audit(
            readme=_readme('Configure com `api_key: {TOKEN}` antes de rodar.')
        )
        self.assertEqual(support.findings(report, "SQA-019"), [])

    def test_source_expression_is_not_a_secret(self):
        report = _audit(
            files={"scripts/parse.py": 'token = marker.group(1)\nsecret = os.environ["X"]\n'}
        )
        self.assertEqual(support.findings(report, "SQA-019"), [])

    def test_placeholder_word_is_documentation(self):
        report = _audit(
            files={"tests/test_config.py": 'FIXTURE = "secret: changeme"\n'}
        )
        self.assertEqual(support.findings(report, "SQA-019"), [])

    def test_suspicious_literal_in_tests_still_signals(self):
        # The fixture text is assembled at runtime so this test file carries no
        # credential-shaped literal of its own; the target file does.
        body = 'TOKEN = "' + GITHUB_TOKEN + '"\n'
        report = _audit(files={"tests/test_leak.py": body})
        found = support.findings(report, "SQA-019")
        self.assertEqual(len(found), 1, found)
        self.assertNotIn(GITHUB_TOKEN, found[0]["evidence"])

    def test_suspicious_literal_survives_escape_suffix(self):
        # A trailing ``\n`` in the source is syntax; it must not turn a real
        # looking value into a placeholder.
        value = "pwZq7Kd2mN"
        body = 'FIXTURE = "secret: ' + value + '\\n"\n'
        report = _audit(files={"tests/test_config.py": body})
        found = support.findings(report, "SQA-019")
        self.assertEqual(len(found), 1, found)
        self.assertNotIn(value, found[0]["evidence"])

    def test_literal_in_fixtures_still_signals(self):
        report = _audit(
            files={"fixtures/payload.txt": f"api_key: {GITHUB_TOKEN}\n"}
        )
        found = support.findings(report, "SQA-019")
        self.assertEqual(len(found), 1, found)

    def test_synthetic_cpf_is_reported_but_repeated_pattern_is_not(self):
        report = _audit(
            files={
                "tests/test_cliente.py": f'CPF = "{SYNTHETIC_CPF}"\n',
                "docs/formato.md": "Use o formato 000.000.000-00 no cadastro.\n",
            }
        )
        found = support.findings(report, "SQA-020")
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["location"], "tests/test_cliente.py:1")
        self.assertNotIn(SYNTHETIC_CPF, found[0]["evidence"])


class CommandFormatTest(unittest.TestCase):
    def test_prose_mention_is_not_an_invocation(self):
        report = _audit(
            readme=_readme("O fluxo principal vive em scripts/cli.py e nada mais.")
        )
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_inline_span_is_an_invocation(self):
        report = _audit(
            files={"scripts/cli.py": CLI_PY},
            readme=_readme("Rode `python scripts/cli.py clean` para limpar."),
        )
        found = support.findings(report, "SQA-017")
        self.assertEqual(len(found), 1, found)
        self.assertIn("clean", found[0]["evidence"])

    def test_valid_subcommand_is_accepted(self):
        report = _audit(
            files={"scripts/cli.py": CLI_PY},
            readme=_readme("Rode `python scripts/cli.py render` para gerar."),
        )
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_fenced_block_is_an_invocation(self):
        report = _audit(
            files={"scripts/cli.py": CLI_PY},
            readme=_readme(
                "Exemplo:\n\n```bash\npython scripts/cli.py clean\n```\n"
            ),
        )
        found = support.findings(report, "SQA-017")
        self.assertEqual(len(found), 1, found)

    def test_prompt_prefixed_line_is_an_invocation(self):
        report = _audit(
            files={"scripts/cli.py": CLI_PY},
            readme=_readme("```console\n$ python scripts/cli.py clean\n```\n"),
        )
        found = support.findings(report, "SQA-017")
        self.assertEqual(len(found), 1, found)

    def test_missing_script_is_reported(self):
        report = _audit(readme=_readme("Rode `python scripts/ausente.py audit`."))
        found = support.findings(report, "SQA-017")
        self.assertEqual(len(found), 1, found)

    def test_other_interpreters_are_invocations(self):
        report = _audit(
            files={
                "scripts/run.sh": "echo ok\n",
                "scripts/tool.js": "console.log(1);\n",
            },
            readme=_readme(
                "```bash\nbash scripts/run.sh\nnode scripts/tool.js\n```\n"
            ),
        )
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_illustrative_name_does_not_hide_a_missing_path(self):
        # The name is not evidence that the path exists: a documented command
        # that cannot run is a signal even when it reads like an example.
        report = _audit(readme=_readme("Rode `python scripts/foo.py` como exemplo."))
        found = support.findings(report, "SQA-017")
        self.assertEqual(len(found), 1, found)
        self.assertIn("scripts/foo.py", found[0]["evidence"])

    def test_nested_path_does_not_become_a_phantom_script(self):
        report = _audit(
            files={"skills/foo/scripts/run.py": "print('ok')\n"},
            readme=_readme("Rode `python skills/foo/scripts/run.py` a partir da raiz."),
        )
        self.assertEqual(support.findings(report, "SQA-017"), [])


class MarkerCitationTest(unittest.TestCase):
    def test_marker_in_code_span_is_documentation(self):
        report = _audit(
            readme=_readme("Marcadores como `TODO` e `FIXME` não são permitidos.")
        )
        self.assertEqual(support.findings(report, "SQA-024"), [])

    def test_marker_vocabulary_list_is_documentation(self):
        report = _audit(
            readme=_readme("A skill reprova marcadores de trabalho inacabado (TODO, TBD, FIXME).")
        )
        self.assertEqual(support.findings(report, "SQA-024"), [])

    def test_real_pendency_is_reported(self):
        report = _audit(readme=_readme("TODO: revisar a seção de limites."))
        found = support.findings(report, "SQA-024")
        self.assertEqual(len(found), 1, found)

    def test_real_pendency_in_quotes_is_still_a_pendency(self):
        # Quoting a marker that opens a task is still an open task; only a
        # span that is just the marker word reads as vocabulary.
        report = _audit(readme=_readme('- "TODO: revisar a seção de limites"'))
        found = support.findings(report, "SQA-024")
        self.assertEqual(len(found), 1, found)


class ClaimScopeTest(unittest.TestCase):
    def test_tag_list_is_not_a_claim(self):
        report = _audit(skill_md=_skill_md("Corpo neutro.", tags="[read-only, audit]"))
        self.assertEqual(support.findings(report, "SQA-025"), [])

    def test_sentence_is_a_claim(self):
        report = _audit(skill_md=_skill_md("A auditoria é read-only sobre o alvo."))
        found = support.findings(report, "SQA-025")
        self.assertEqual(len(found), 1, found)

    def test_claim_that_mentions_tests_still_signals(self):
        # Naming a test is not producing it: the claim stays a signal until the
        # agent confirms the evidence.
        report = _audit(
            skill_md=_skill_md("O isolamento é garantido por testes automatizados.")
        )
        found = support.findings(report, "SQA-025")
        self.assertEqual(len(found), 1, found)

    def test_descriptive_test_sentence_is_not_a_claim(self):
        report = _audit(skill_md=_skill_md("Os testes cobrem os caminhos principais."))
        self.assertEqual(support.findings(report, "SQA-025"), [])

    def test_generated_header_does_not_exempt_the_file(self):
        report = _audit(
            files={
                "references/checks.md": (
                    "<!-- Gerado por scripts/render.py; não editar à mão. -->\n"
                    "\n"
                    "A auditoria é somente leitura sobre o alvo.\n"
                )
            }
        )
        found = support.findings(report, "SQA-025")
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["location"], "references/checks.md:3")

    def test_heading_is_not_a_claim(self):
        report = _audit(skill_md=_skill_md("## Somente leitura\n\nCorpo neutro."))
        self.assertEqual(support.findings(report, "SQA-025"), [])


class HardeningTest(unittest.TestCase):
    """Regressions for the hardening pass: dedup, links, failure handling."""

    def test_illustrative_placeholder_is_not_a_broken_reference(self):
        report = _audit(readme=_readme("Por exemplo, `python scripts/<nome>.py`."))
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_example_wording_does_not_exempt_a_real_path(self):
        # The skip is syntactic (placeholder wrapper), never the word "exemplo".
        report = _audit(readme=_readme("Como exemplo, `python scripts/exemplo.py`."))
        self.assertEqual(len(support.findings(report, "SQA-017")), 1)

    def test_repeated_invocation_on_one_line_is_reported_once(self):
        report = _audit(
            readme=_readme(
                "Rode `python scripts/missing.py` ou `python scripts/missing.py`."
            )
        )
        self.assertEqual(len(support.findings(report, "SQA-017")), 1)

    def test_runtime_version_is_not_a_documented_version(self):
        readme = "# sample-skill\n\nRequer Python 3.11.0 e Node 20.1.0.\n"
        report = _audit(readme=readme)
        self.assertEqual(support.findings(report, "SQA-023"), [])
        self.assertEqual(support.status(report, "SQA-023"), "not_run")

    def test_link_outside_the_target_does_not_satisfy_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp, readme=_readme("Veja `references/fora.md`."))
            outside = pathlib.Path(tmp) / "fora"
            outside.mkdir()
            (outside / "fora.md").write_text("conteúdo externo\n", encoding="utf-8")
            link = root / "references"
            link.mkdir(exist_ok=True)
            try:
                (link / "fora.md").symlink_to(outside / "fora.md")
            except (OSError, NotImplementedError):
                self.skipTest("sem permissão para criar link simbólico")
            report = support.audit(root)
            self.assertEqual(len(support.findings(report, "SQA-013")), 1)

    def test_unexpected_failure_is_not_reported_as_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            with mock.patch.object(
                support.audit_skill, "audit_target", side_effect=RuntimeError("boom")
            ), contextlib.redirect_stderr(io.StringIO()) as err:
                code = support.audit_skill.main(["audit", "--target", str(root)])
            self.assertEqual(code, support.common.EXIT_INTERNAL)
            self.assertIn("interno", err.getvalue())

    def test_a_malformed_clock_override_is_refused(self):
        # Falling back to the wall clock on a typo would make a "frozen" report
        # carry a real timestamp without saying so.
        with mock.patch.dict("os.environ", {"SQA_NOW": "ontem"}):
            with self.assertRaises(support.common.UsageError) as caught:
                support.common.now_ts()
        self.assertIn("SQA_NOW", str(caught.exception))

    def test_a_valid_clock_override_is_used_as_is(self):
        with mock.patch.dict("os.environ", {"SQA_NOW": "1700000000"}):
            self.assertEqual(support.common.now_ts(), 1700000000.0)
            self.assertEqual(support.common.clock_source(), "SQA_NOW")


if __name__ == "__main__":
    unittest.main()
