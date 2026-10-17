"""Paired regressions: every widening of a detector has a near-miss that must not fire.

Each pair pins both directions, so a heuristic that starts matching prose (or
stops matching a real claim) fails here instead of silently changing the report.
"""

from __future__ import annotations

import tempfile
import unittest

import support


class PairsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._built = 0

    def build(self, body: str, files: dict[str, str] | None = None) -> dict:
        self._built += 1
        name = f"sample-skill-{self._built}"
        root = support.build_skill(
            self._tmp.name,
            name=name,
            readme=f"# {name}\n\n{body}\n",
            files=files,
        )
        return support.audit(root)

    def line(self, text: str) -> dict:
        return self.build(text)


class ClaimPairsTest(PairsBase):
    """SQA-025: the six labels, each with a sentence that only looks similar."""

    def test_somente_leitura_fires_and_readonly_flag_does_not(self):
        self.assertEqual(len(support.findings(self.line("A auditoria é somente leitura."), "SQA-025")), 1)
        self.assertEqual(
            support.findings(self.line("O campo readonly do formulário é visual."), "SQA-025"),
            [],
        )

    def test_nao_grava_fires_and_a_different_verb_does_not(self):
        self.assertEqual(len(support.findings(self.line("O script não grava no alvo."), "SQA-025")), 1)
        self.assertEqual(
            support.findings(self.line("O script não sobrescreve arquivos existentes."), "SQA-025"),
            [],
        )

    def test_sem_rede_fires_and_social_networks_do_not(self):
        self.assertEqual(len(support.findings(self.line("O comando roda sem rede."), "SQA-025")), 1)
        self.assertEqual(
            support.findings(self.line("O material não fala de redes sociais."), "SQA-025"),
            [],
        )

    def test_isolamento_fires_and_isoladamente_does_not(self):
        self.assertEqual(len(support.findings(self.line("O isolamento é garantido."), "SQA-025")), 1)
        self.assertEqual(
            support.findings(self.line("Cada arquivo é lido isoladamente, em ordem."), "SQA-025"),
            [],
        )

    def test_garantido_fires_and_a_past_tense_mention_does_not(self):
        self.assertEqual(len(support.findings(self.line("O resultado é garantido pelo manifesto."), "SQA-025")), 1)
        self.assertEqual(
            support.findings(self.line("A execução foi registrada no relatório."), "SQA-025"),
            [],
        )

    def test_fenced_claim_is_example_text_and_inline_claim_is_a_promise(self):
        fenced = self.build("```text\nA skill é somente leitura sobre o alvo.\n```")
        self.assertEqual(support.findings(fenced, "SQA-025"), [])
        inline = self.build("A skill é somente leitura sobre o alvo.")
        self.assertEqual(len(support.findings(inline, "SQA-025")), 1)

    def test_every_label_has_a_pair(self):
        """The six labels are the vocabulary; none may be added without a pair."""
        labels = {label for label, _ in support.audit_skill.CLAIM_PATTERNS}
        self.assertEqual(
            labels,
            {
                "somente leitura",
                "read-only",
                "nao grava",
                "sem rede",
                "isolamento",
                "garantia",
            },
        )


class IllustrativePathPairsTest(PairsBase):
    """SQA-017: a placeholder is documentation; a concrete path is a command."""

    def test_angle_placeholder_is_not_a_broken_path(self):
        self.assertEqual(
            support.findings(self.line("Por exemplo, `python scripts/<nome>.py`."), "SQA-017"),
            [],
        )

    def test_braces_are_also_a_placeholder(self):
        found = support.findings(self.line("Rode `python scripts/{nome}.py`."), "SQA-017")
        self.assertEqual(found, [])

    def test_placeholder_like_word_without_wrapper_is_a_real_path(self):
        # "nome" reads like a placeholder to a human, but without < > or { }
        # the auditor cannot tell it apart from a file that should exist.
        found = support.findings(self.line("Rode `python scripts/nome.py`."), "SQA-017")
        self.assertEqual(len(found), 1, found)
        self.assertIn("scripts/nome.py", found[0]["evidence"])

    def test_fenced_placeholder_is_documentation(self):
        report = self.build("```bash\npython scripts/<nome>.py\n```")
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_fenced_concrete_path_is_checked(self):
        # The fence is the normal way to document a command, so the path inside
        # it is checked like an inline one: only placeholders are exempt.
        report = self.build("```bash\npython scripts/nao_existe.py\n```")
        found = support.findings(report, "SQA-017")
        self.assertEqual(len(found), 1, found)
        self.assertIn("scripts/nao_existe.py", found[0]["evidence"])

    def test_fenced_concrete_path_that_exists_is_accepted(self):
        report = self.build(
            "```bash\npython scripts/existe.py\n```",
            files={"scripts/existe.py": "print('ok')\n"},
        )
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_a_broken_link_inside_a_fence_is_not_reported(self):
        in_fence = self.build("```md\n[doc](references/nao_existe.md)\n```")
        self.assertEqual(support.findings(in_fence, "SQA-013"), [])
        in_prose = self.build("veja [doc](references/nao_existe.md)\n")
        self.assertEqual(len(support.findings(in_prose, "SQA-013")), 1)

    def test_declared_limit_matches_the_behaviour(self):
        limits = " ".join(support.audit_skill.REPORT_LIMITS)
        # Commands and concrete paths inside fences are judged; links inside them
        # are not. The declared limits must say both, or the report hides a limit
        # it has (see ``_documentation_lines``).
        self.assertIn("Comandos dentro de blocos cercados são conferidos", limits)
        self.assertIn("links em blocos cercados não são verificados", limits)
        self.assertIn("caminhos concretos também", limits)
        self.assertNotIn("caminhos dentro de", limits)


if __name__ == "__main__":
    unittest.main()
