"""Regressions for the four findings reproduced on a496ad8.

Each class is one finding from the review:

* :class:`ExportSanitisationTest` - finding 1, raw values in the exported report;
* :class:`LiteralVersusExpressionTest` - finding 2, a literal with brackets
  mistaken for source code;
* :class:`TraversalFailureTest` - finding 3, an unreadable directory missing from
  the manifest;
* :class:`InvocationDedupTest` - finding 4, two different commands on one line
  collapsed into one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import support

common = support.common

#: Synthetic values only: shaped like the real formats so the masking applies,
#: and invalid on purpose so nothing here is a usable credential.
GITHUB_TOKEN = "ghp_" + "S" * 36
EMAIL = "synth.person@example.invalid"
HOME_PATH = "/home/synthuser/private/notes.txt"


def _skill_md(*, description: str, version: str) -> str:
    return f"""---
name: sample-skill
description: {description}
version: {version}
platforms: [linux]
---

# sample-skill

Skill de exemplo usada pelos testes automatizados deste repositório.

## Uso

Executar o script de exemplo e conferir a saída no terminal.

## Exemplo

```console
$ python scripts/run.py
0.1.0
```

O comportamento foi validado no Linux com Python 3.11.
"""


class ExportSanitisationTest(unittest.TestCase):
    """Finding 1: values read from the target reach the export unmasked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = support.build_skill(
            self._tmp.name,
            skill_md=_skill_md(
                description=f"Use when syncing with {GITHUB_TOKEN}.",
                version=EMAIL,
            ),
        )
        self.report = support.audit(self.root)

    def test_the_report_itself_keeps_the_raw_values(self):
        # Masking happens at export, after every digest and comparison: the
        # in-memory report is the technical record and stays exact.
        self.assertIn(GITHUB_TOKEN, self.report["skill"]["description"])
        self.assertEqual(self.report["skill"]["version"], EMAIL)

    def test_json_export_masks_frontmatter_values(self):
        text = common.render_json(self.report)
        self.assertNotIn(GITHUB_TOKEN, text)
        self.assertNotIn(EMAIL, text)
        self.assertIn(common.SECRET_MASK, text)
        self.assertIsInstance(json.loads(text), dict)

    def test_markdown_export_masks_frontmatter_values(self):
        text = common.render_markdown(self.report)
        self.assertNotIn(GITHUB_TOKEN, text)
        self.assertNotIn(EMAIL, text)
        self.assertIn(common.SECRET_MASK, text)

    def test_both_exports_mask_the_same_way(self):
        # One primitive for both outputs: no format may mask a value that the
        # other reproduces.
        masked = common.redact(EMAIL)
        self.assertEqual(masked, common.SECRET_MASK)
        self.assertIn(f"Versão declarada: {masked}", common.render_markdown(self.report))
        self.assertIn(f'"version": "{masked}"', common.render_json(self.report))

    def test_symlink_destination_is_masked_in_the_export(self):
        report = {
            "skill": {"name": "sample", "path": ".", "version": "0.1.0"},
            "findings": [],
            "limits": [],
            "checks_run": [],
            "manifest": {
                "digest": "0" * 64,
                "root": HOME_PATH,
                "entries": {
                    "references/link.md": {"kind": "symlink", "target": HOME_PATH},
                },
            },
        }
        text = common.render_json(report)
        self.assertNotIn("synthuser", text)
        self.assertNotIn(HOME_PATH, text)

    def test_every_windows_spelling_of_a_profile_path_is_masked(self):
        # The same profile reaches a report as C:\Users\<name>, C:/Users/<name>
        # or /c/Users/<name>; all three identify the user, so none may survive
        # the export.
        spellings = (
            "C:\\Users\\synthuser\\private\\notes.md",
            "C:/Users/synthuser/private/notes.md",
            "/c/Users/synthuser/private/notes.md",
        )
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                report = json.loads(json.dumps(self.report))
                report["manifest"]["entries"]["references/link.md"] = {
                    "kind": "symlink",
                    "target": spelling,
                }
                text = common.render_json(report)
                self.assertNotIn("synthuser", text)
                self.assertNotIn(spelling, text)

    def test_the_scanner_reports_every_windows_spelling(self):
        spellings = (
            "C:\\Users\\synthuser\\private\\notes.md",
            "C:/Users/synthuser/private/notes.md",
            "/c/Users/synthuser/private/notes.md",
        )
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                with tempfile.TemporaryDirectory() as tmp:
                    root = support.build_skill(
                        tmp, files={"docs/setup.md": f"copie para {spelling}\n"}
                    )
                    found = support.findings(support.audit(root), "SQA-020")
                    self.assertEqual(len(found), 1)
                    self.assertNotIn("synthuser", found[0]["evidence"])

    def test_checks_run_reason_is_masked(self):
        # A real report, with a raw value planted in the field that reproduces
        # free text: the reason of a check must be masked like any other.
        report = json.loads(json.dumps(self.report))
        report["checks_run"].append(
            {"code": "SQA-001", "status": "executed", "reason": f"leu {GITHUB_TOKEN}"}
        )
        self.assertNotIn(GITHUB_TOKEN, common.render_json(report))
        self.assertNotIn(GITHUB_TOKEN, common.render_markdown(report))

    def test_the_export_states_that_it_is_not_safe_to_attach(self):
        # The report must not promise more than format-based masking delivers.
        publication = common.publication_policy()
        self.assertTrue(publication["report_masked"])
        self.assertFalse(publication["report_safe_to_attach"])
        self.assertIn("publication", self.report)
        text = common.render_markdown(self.report)
        self.assertIn("Pronto para anexar sem revisão: **não**", text)

    def test_the_technical_manifest_stays_raw(self):
        # The comparison of integrity is the reason the manifest exists; masking
        # it would make two different trees look identical. It declares itself
        # private instead of pretending to be publishable.
        manifest = common.build_manifest(str(self.root))
        raw = common.render_json(manifest, mask=False)
        self.assertEqual(manifest["root"], self.root.name)
        self.assertIs(manifest["private"], True)
        self.assertIn('"private": true', raw)
        self.assertIn("SKILL.md", raw)
        self.assertIn("sample-skill", raw)

    def test_the_manifest_command_prints_the_raw_artifact(self):
        # The artifact the CLI writes is the raw one: the publishable report is
        # a different command, so this output must not be masked and must say
        # that it is private.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = support.audit_skill.main(["manifest", "--target", str(self.root)])
        self.assertEqual(code, common.EXIT_OK)
        out = buffer.getvalue()
        self.assertIn('"private": true', out)
        self.assertIn("sample-skill", out)
        self.assertNotIn(common.SECRET_MASK, out)
        self.assertIn("entries", json.loads(out))

    def test_a_url_that_merely_contains_a_profile_path_is_not_masked(self):
        # ``https://c/Users/...`` is a URL whose path segment happens to look
        # like an MSYS profile path; masking it would rewrite a published link.
        url = "https://c/Users/synthuser/private"
        self.assertEqual(common.redact(url), url)

    def test_a_url_that_contains_a_windows_profile_path_is_not_masked(self):
        # The Windows spelling has the same problem with a longer prefix:
        # ``https://example.com/c:/users/...`` is a link, not a profile path.
        url = "https://example.com/c:/users/synthuser/private"
        self.assertEqual(common.redact(url), url)
        self.assertEqual(common.redact("file:///C:/Users/synthuser/x"), "file:///C:/Users/synthuser/x")

    def test_the_report_manifest_declares_itself_publishable(self):
        # Two artifacts carry a manifest: the raw one says ``private``, the copy
        # inside the masked report must not, or a reader could not tell which
        # one may be attached.
        self.assertIs(self.report["manifest"]["private"], False)
        self.assertIs(common.build_manifest(str(self.root))["private"], True)
        self.assertIn('"private": false', common.render_json(self.report))

    def test_two_paths_masked_to_the_same_key_keep_both_entries(self):
        # Different home directories collapse to the same masked key. The
        # published manifest must not silently drop the second entry.
        report = {
            "manifest": {
                "entries": {
                    "/home/synthuser/a.md": "aa",
                    "/home/otheruser/a.md": "bb",
                }
            }
        }
        masked = common.publishable(report)["manifest"]["entries"]
        self.assertEqual(len(masked), 2)
        self.assertEqual(sorted(masked.values()), ["aa", "bb"])
        self.assertIn("/home/<redacted>/a.md", masked)
        self.assertIn("/home/<redacted>/a.md#2", masked)

    def test_the_publishable_copy_masks_the_manifest(self):
        # Same manifest, two artifacts: raw where it is compared, masked where
        # it is exported inside the report.
        manifest = common.build_manifest(str(self.root))
        manifest["entries"]["references/link.md"] = {"kind": "symlink", "target": HOME_PATH}
        exported = common.render_json({"manifest": manifest})
        self.assertNotIn(HOME_PATH, exported)
        self.assertNotIn("synthuser", exported)
        self.assertEqual(json.loads(exported)["manifest"]["root"], self.root.name)


class LiteralVersusExpressionTest(unittest.TestCase):
    """Finding 2: a quoted literal with brackets was read as source code."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _codes(self, assignment: str) -> list[str]:
        root = support.build_skill(
            self._tmp.name,
            files={"scripts/parse.py": f"# exemplo\n{assignment}\n"},
        )
        report = support.audit(root)
        return support.codes(report)

    def test_a_literal_without_brackets_is_reported(self):
        self.assertIn("SQA-019", self._codes("password = 'synthAbc9_xyz!'"))

    def test_a_literal_with_brackets_is_reported(self):
        self.assertIn("SQA-019", self._codes("password = 'synthAbc9[xyz]!'"))

    def test_a_literal_with_parentheses_is_reported(self):
        self.assertIn("SQA-019", self._codes("password = 'synthAbc9(xyz)!'"))

    def test_a_quoted_literal_that_looks_like_code_is_reported(self):
        # The pattern's optional quote is consumed outside the capture, so the
        # quotes must be read from the line: a value inside them is data even
        # when it has the shape of a call.
        self.assertIn("SQA-019", self._codes("password = 'marker.group(1)'"))

    def test_a_quoted_literal_with_a_subscript_is_reported(self):
        # Same shape as the expression case below, now inside quotes: the open
        # bracket must not win over the quote that makes it a literal.
        self.assertIn("SQA-019", self._codes("password = \"os.environ['SYNTH']\""))

    def test_a_literal_that_starts_with_a_quote_is_masked(self):
        # The quote that opens the literal and the quote that is the first
        # character of the value are different characters: a value starting
        # with ``'`` must still be masked.
        text = common.redact("password = \"'synthAbc9!\"")
        self.assertNotIn("synthAbc9", text)
        self.assertIn(common.SECRET_MASK, text)

    def test_a_call_is_still_treated_as_code(self):
        self.assertNotIn("SQA-019", self._codes("token = marker.group(1)"))

    def test_a_subscript_is_treated_as_code(self):
        self.assertNotIn("SQA-019", self._codes('secret = os.environ["SYNTH"]'))

    def test_the_classifier_reads_characters_only(self):
        self.assertTrue(common.is_code_expression("marker.group(1)"))
        self.assertTrue(common.is_code_expression("os.environ["))
        self.assertFalse(common.is_code_expression("synthAbc9[xyz]!"))
        self.assertFalse(common.is_code_expression("synthAbc9(xyz)!"))

    def test_a_deeply_nested_call_is_still_code(self):
        # The classifier scans brackets instead of matching them with a nested
        # regular expression, so depth is not a limit.
        self.assertTrue(common.is_code_expression("fmt(a(b(c(d))))"))
        self.assertNotIn("SQA-019", self._codes("token = fmt(a(b(c(d))))"))


class TraversalFailureTest(unittest.TestCase):
    """Finding 3: a directory that cannot be listed vanishes from the manifest."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = support.build_skill(
            self._tmp.name,
            files={
                "references/privado/segredo.md": "# dentro do diretório inacessível\n",
                "references/publico.md": "# público\n",
            },
        )

    @staticmethod
    def _failing_scandir(blocked: str):
        """``os.scandir`` that fails on one directory name, without touching chmod."""
        real = os.scandir

        def fake(path="."):
            if pathlib.Path(str(path)).name == blocked:
                raise PermissionError(13, "simulado", str(path))
            return real(path)

        return fake

    def test_the_hole_is_recorded_in_the_manifest(self):
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root)
        manifest = report["manifest"]
        self.assertFalse(manifest["complete"])
        self.assertEqual([item["rel"] for item in manifest["walk_errors"]], ["references/privado"])
        self.assertEqual(manifest["walk_errors"][0]["reason"], "PermissionError")
        self.assertNotIn("references/privado/segredo.md", manifest["entries"])
        self.assertIn("references/publico.md", manifest["entries"])

    def test_the_hole_reaches_coverage_and_the_limits(self):
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root)
        self.assertEqual(
            [item["rel"] for item in report["coverage"]["traversal_errors"]],
            ["references/privado"],
        )
        reasons = [item["reason"] for item in report["limits"]]
        self.assertTrue(any("travessia incompleta" in reason for reason in reasons), reasons)

    def test_stability_is_not_claimed_over_content_never_read(self):
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root)
        self.assertIsNone(report["target_unchanged"])

    def test_the_markdown_says_the_manifest_is_incomplete(self):
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root)
        text = common.render_markdown(report)
        self.assertIn("(manifesto incompleto)", text)
        self.assertIn("travessia falhou", text)
        self.assertIn("inconclusivo", text)

    def test_a_readable_target_still_reports_stability(self):
        report = support.audit(self.root)
        self.assertTrue(report["manifest"]["complete"])
        self.assertTrue(report["target_unchanged"])
        self.assertEqual(report["manifest"]["walk_errors"], [])

    def test_an_unreadable_file_makes_the_manifest_incomplete(self):
        # A hole in the tree is not the only way to lose content: a file that
        # exists but cannot be hashed leaves the digest just as partial.
        real = common.sha256_file

        def fake(path):
            return None if pathlib.Path(str(path)).name == "SKILL.md" else real(path)

        with mock.patch.object(common, "sha256_file", fake):
            report = support.audit(self.root)
        manifest = report["manifest"]
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["unreadable"], ["SKILL.md"])
        self.assertIsNone(report["target_unchanged"])
        self.assertIn("(manifesto incompleto)", common.render_markdown(report))

    def test_a_failure_without_a_path_is_not_reported_as_the_root(self):
        # ``.`` would claim the failure was the target itself, which the report
        # never verified; an unknown location has to say so.
        def failing(path="."):
            raise PermissionError(13, "simulado")

        with mock.patch("os.scandir", failing):
            report = support.audit(self.root)
        self.assertEqual(report["manifest"]["walk_errors"][0]["rel"], common.UNKNOWN_PATH)
        self.assertNotEqual(report["manifest"]["walk_errors"][0]["rel"], ".")

    def test_a_divergence_wins_over_a_hole_in_the_comparison(self):
        # A locked subtree makes the comparison incomplete, but an entry that
        # was read and differs is a proven divergence: the hole must not turn
        # a known change into "could not confirm".
        reference = self._tmp.name + "-divergent.json"
        manifest = common.build_manifest(str(self.root))
        manifest["entries"]["references/publico.md"]["sha256"] = "0" * 64
        pathlib.Path(reference).write_text(
            common.render_json(manifest, mask=False), encoding="utf-8"
        )
        self.addCleanup(pathlib.Path(reference).unlink)
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root, reference_manifest=reference)
        self.assertIs(report["matches_reference"], False)
        self.assertEqual(
            [item["rel"] for item in report["manifest"]["walk_errors"]],
            ["references/privado"],
        )
        reasons = [item["reason"] for item in report["limits"]]
        self.assertFalse(
            any("não pôde ser confirmado" in reason for reason in reasons), reasons
        )

    def test_a_removal_outside_the_hole_is_still_a_divergence(self):
        # Only a removal that a hole could explain is inconclusive; an entry
        # missing from a subtree that was read is a proven change.
        reference = self._tmp.name + "-removed.json"
        manifest = common.build_manifest(str(self.root))
        manifest["entries"]["references/ausente.md"] = {"kind": "file", "size": 1, "sha256": "0" * 64}
        pathlib.Path(reference).write_text(
            common.render_json(manifest, mask=False), encoding="utf-8"
        )
        self.addCleanup(pathlib.Path(reference).unlink)
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root, reference_manifest=reference)
        self.assertIs(report["matches_reference"], False)

    def test_a_hole_only_in_the_second_pass_still_makes_the_copy_incomplete(self):
        # The published manifest copy lists the union of both passes, so its
        # ``complete`` flag has to be the conjunction of both as well.
        real = os.scandir
        seen = {"privado": 0}

        def fake(path="."):
            if pathlib.Path(str(path)).name == "privado":
                seen["privado"] += 1
                if seen["privado"] > 1:
                    raise PermissionError(13, "simulado", str(path))
            return real(path)

        with mock.patch("os.scandir", fake):
            report = support.audit(self.root)
        self.assertGreaterEqual(seen["privado"], 2)
        self.assertEqual(
            [item["rel"] for item in report["manifest"]["walk_errors"]],
            ["references/privado"],
        )
        self.assertFalse(report["manifest"]["complete"])
        self.assertIsNone(report["target_unchanged"])

    def test_the_same_hole_in_both_passes_is_listed_once(self):
        hole = {"rel": "references/privado", "reason": "PermissionError"}
        before = {"entries": {"a.md": "aa"}, "walk_errors": [hole]}
        after = {"entries": {"a.md": "aa"}, "walk_errors": [dict(hole)]}
        diff = common.diff_manifests(before, after)
        self.assertEqual(diff["walk_errors"], [hole])
        self.assertFalse(diff["complete"])

    def test_a_reference_manifest_cannot_be_confirmed_over_a_hole(self):
        # Two identical holes would make both digests agree; agreement is not
        # evidence that the tree was read.
        reference = self._tmp.name + "-reference.json"
        pathlib.Path(reference).write_text(
            common.render_json(common.build_manifest(str(self.root)), mask=False),
            encoding="utf-8",
        )
        self.addCleanup(pathlib.Path(reference).unlink)
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            report = support.audit(self.root, reference_manifest=reference)
        self.assertIsNone(report["matches_reference"])
        reasons = [item["reason"] for item in report["limits"]]
        self.assertTrue(
            any("não pôde ser confirmado" in reason for reason in reasons), reasons
        )


CLI_PY = '''"""Command line used by the dedup regressions."""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cli.py")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("audit")
    sub.add_parser("clean")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
'''


class InvocationDedupTest(unittest.TestCase):
    """Finding 4: two invocations on one line collapsed into one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _report(self, line: str, suffix: str) -> dict:
        root = support.build_skill(
            self._tmp.name,
            name=f"dedup-{suffix}",
            files={"scripts/cli.py": CLI_PY},
            readme=f"# dedup\n\n## Uso\n\n{line}\n\n## Exemplo\n\n```console\n$ python scripts/cli.py audit\n```\n",
        )
        return support.audit(root)

    def test_two_different_invocations_on_one_line_are_both_checked(self):
        report = self._report(
            "`python scripts/cli.py audit` e `python scripts/cli.py nonexistent`", "ab"
        )
        evidence = " ".join(item["evidence"] for item in support.findings(report, "SQA-017"))
        self.assertIn("nonexistent", evidence)

    def test_the_order_does_not_change_the_result(self):
        report = self._report(
            "`python scripts/cli.py nonexistent` e `python scripts/cli.py audit`", "ba"
        )
        evidence = " ".join(item["evidence"] for item in support.findings(report, "SQA-017"))
        self.assertIn("nonexistent", evidence)

    def test_an_identical_repetition_is_still_one_command(self):
        line = "`python scripts/cli.py nonexistent` e `python scripts/cli.py nonexistent`"
        report = self._report(line, "aa")
        self.assertEqual(len(support.findings(report, "SQA-017")), 1)

    def test_the_same_bad_argument_with_different_prose_is_one_finding(self):
        # The finding is the bad argument, not the sentence around it: the same
        # command quoted twice is one documented claim, reported once.
        line = (
            "`python scripts/cli.py nonexistent` (acima) e "
            "`python scripts/cli.py nonexistent` (repetido)"
        )
        report = self._report(line, "ab2")
        self.assertEqual(len(support.findings(report, "SQA-017")), 1)

    def test_a_valid_invocation_does_not_hide_an_invalid_one(self):
        # The valid command must not short-circuit the line before the bad one
        # is examined, in either order.
        for suffix, line in (
            ("ok1", "`python scripts/cli.py audit` e `python scripts/cli.py nonexistent`"),
            ("ok2", "`python scripts/cli.py nonexistent` e `python scripts/cli.py audit`"),
        ):
            with self.subTest(suffix=suffix):
                found = support.findings(self._report(line, suffix), "SQA-017")
                self.assertEqual(len(found), 1)
                # The evidence quotes the argument that failed, not the valid
                # one that happened to share the line.
                self.assertIn("'nonexistent'", found[0]["evidence"])


if __name__ == "__main__":
    unittest.main()
