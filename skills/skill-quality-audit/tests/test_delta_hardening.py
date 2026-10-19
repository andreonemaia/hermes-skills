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
import subprocess
import sys
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

    def test_the_reference_comparison_is_masked_like_the_run_comparison(self):
        # The reference diff carries the same path lists as the run diff; a
        # masking rule applied to one and not the other leaks the target.
        report = {
            "reference_diff": {
                "holes_before": [HOME_PATH],
                "holes_after": ["."],
                "unconfirmed_added": [HOME_PATH],
                "unconfirmed_removed": [],
                "unverifiable": [HOME_PATH],
                "walk_errors": [{"rel": HOME_PATH, "reason": "permission"}],
            }
        }
        masked = common.publishable(report)
        self.assertNotIn("synthuser", str(masked))
        self.assertEqual(masked["reference_diff"]["holes_after"], ["."])

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

    def _controlled_builds(self, second_pass):
        """Patch ``build_manifest`` so only the second construction is altered.

        Counting ``os.scandir`` calls does not identify a pass: the Context
        reads the tree too, so the counter can trip before the first manifest
        is finished and leave both passes incomplete. Wrapping the manifest
        builder states which construction fails, which is the fact under test.
        """
        real_build = common.build_manifest
        calls = {"count": 0}

        def controlled(root):
            calls["count"] += 1
            if calls["count"] == 2:
                return second_pass(real_build, root)
            return real_build(root)

        return controlled, calls

    def test_a_hole_only_in_the_second_pass_is_inconclusive(self):
        # The first manifest completes over the whole tree; only the second
        # loses access. The entry that disappears is then explained by the
        # hole, so it is not a proven removal.
        def second_pass(real_build, root):
            with mock.patch("os.scandir", self._failing_scandir("privado")):
                return real_build(root)

        controlled, calls = self._controlled_builds(second_pass)
        with mock.patch.object(common, "build_manifest", controlled):
            report = support.audit(self.root)
        self.assertEqual(calls["count"], 2)
        diff = report["manifest_diff"]
        self.assertEqual(diff["removed"], [])
        self.assertEqual(diff["unconfirmed_removed"], ["references/privado/segredo.md"])
        self.assertIsNone(report["target_unchanged"])
        self.assertNotIn("SQA-026", support.codes(report))
        # Not merely "different from 5": the fixture has no finding, so an
        # inconclusive run must stay a clean exit rather than borrow a code.
        self.assertEqual(report["exit_code"], support.common.EXIT_OK)
        self.assertFalse(report["manifest"]["complete"])
        reasons = [item["reason"] for item in report["limits"]]
        self.assertTrue(
            any("não pôde ser comprovada" in reason for reason in reasons), reasons
        )

    def test_a_real_addition_beside_the_second_pass_hole_still_exits_five(self):
        # Control for the previous test: the hole hides one entry, but a file
        # created outside it between the two passes is still observed as a new
        # entry and remains a proven change.
        def second_pass(real_build, root):
            (pathlib.Path(root) / "extra.md").write_text("# novo\n", encoding="utf-8")
            with mock.patch("os.scandir", self._failing_scandir("privado")):
                return real_build(root)

        controlled, _ = self._controlled_builds(second_pass)
        with mock.patch.object(common, "build_manifest", controlled):
            report = support.audit(self.root)
        self.assertEqual(report["manifest_diff"]["added"], ["extra.md"])
        self.assertIs(report["target_unchanged"], False)
        self.assertIn("SQA-026", support.codes(report))
        self.assertEqual(report["exit_code"], support.common.EXIT_TARGET_CHANGED)

    def test_a_root_failure_in_the_second_pass_is_inconclusive(self):
        # A failure on ``.`` leaves every entry unobserved on that side; the
        # entries missing from the second manifest prove nothing.
        root_abs = pathlib.Path(self.root).resolve()
        real = os.scandir

        def fake(path="."):
            if pathlib.Path(str(path)).resolve() == root_abs:
                raise PermissionError(13, "simulado", str(path))
            return real(path)

        def second_pass(real_build, root):
            with mock.patch("os.scandir", fake):
                return real_build(root)

        controlled, _ = self._controlled_builds(second_pass)
        with mock.patch.object(common, "build_manifest", controlled):
            report = support.audit(self.root)
        self.assertEqual(report["manifest"]["walk_errors"][0]["rel"], ".")
        self.assertEqual(report["manifest_diff"]["removed"], [])
        self.assertTrue(report["manifest_diff"]["unconfirmed_removed"])
        self.assertIsNone(report["target_unchanged"])
        self.assertNotIn("SQA-026", support.codes(report))

    def test_a_reference_built_over_a_hole_cannot_prove_an_addition(self):
        # The reference never listed ``privado``; the target does. The file is
        # not an addition, it was simply absent from the incomplete reference.
        reference = self._tmp.name + "-hole.json"
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            manifest = common.build_manifest(str(self.root))
        pathlib.Path(reference).write_text(
            common.render_json(manifest, mask=False), encoding="utf-8"
        )
        self.addCleanup(pathlib.Path(reference).unlink)
        report = support.audit(self.root, reference_manifest=reference)
        diff = report["reference_diff"]
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["unconfirmed_added"], ["references/privado/segredo.md"])
        self.assertIsNone(report["matches_reference"])
        reasons = [item["reason"] for item in report["limits"]]
        self.assertTrue(
            any("referência não observou" in reason for reason in reasons), reasons
        )
        self.assertFalse(
            any("adicionado(s)" in reason for reason in reasons), reasons
        )

    def test_bare_string_walk_errors_reach_the_diff_as_records(self):
        # A hand-written or older reference may name a hole as a bare string.
        # The diff reads it at the same boundary as the walker's dicts, so the
        # hole is still a hole and the missing reason is stated, not invented.
        diff = common.diff_manifests(
            {"entries": {}, "walk_errors": ["references/privado/"]},
            {"entries": {}},
        )
        self.assertEqual(diff["holes_before"], ["references/privado"])
        self.assertEqual(
            diff["walk_errors"],
            [{"rel": "references/privado", "reason": common.UNKNOWN_REASON}],
        )
        self.assertEqual(diff["holes_after"], [])

    def test_the_cli_accepts_a_hand_written_reference_with_bare_errors(self):
        # End to end through ``audit --check-unchanged``: the reference names
        # its hole as a string, and both exports must still be written.
        reference = pathlib.Path(self._tmp.name) / "hand-written.json"
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            manifest = common.build_manifest(str(self.root))
        manifest["walk_errors"] = ["references/privado"]
        reference.write_text(
            common.render_json(manifest, mask=False), encoding="utf-8"
        )
        outputs = {
            "json": pathlib.Path(self._tmp.name) / "report.json",
            "md": pathlib.Path(self._tmp.name) / "report.md",
        }
        for fmt, out in outputs.items():
            proc = subprocess.run(
                [
                    sys.executable,
                    str(support.SCRIPTS / "audit_skill.py"),
                    "audit",
                    "--target",
                    str(self.root),
                    "--check-unchanged",
                    str(reference),
                    "--format",
                    fmt,
                    "--out",
                    str(out),
                ],
                cwd=str(support.SCRIPTS.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(proc.returncode, common.EXIT_OK, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
        report = json.loads(outputs["json"].read_text(encoding="utf-8"))
        self.assertEqual(
            report["reference_diff"]["walk_errors"],
            [{"rel": "references/privado", "reason": common.UNKNOWN_REASON}],
        )
        self.assertIsNone(report["matches_reference"])
        markdown = outputs["md"].read_text(encoding="utf-8")
        # The Markdown must state the same hole the JSON carries, otherwise a
        # reader of the report cannot tell why the comparison is inconclusive.
        self.assertIn("Referência com diretórios não listados", markdown)
        self.assertIn(common.UNKNOWN_REASON, markdown)
        self.assertIn("references/privado", markdown)
        self.assertIn("inconclusivo", markdown)
        self.assertEqual(support.findings(report, "SQA-026"), [])
        reasons = [
            item["reason"] for item in report["limits"] if item["id"] == "SQA-026"
        ]
        self.assertTrue(reasons and "references/privado" in reasons[0], reasons)

    def test_a_real_addition_beside_the_reference_hole_is_still_a_divergence(self):
        # Control: the reference hole hides ``privado``, but a file created in
        # the target outside it was observed by the reference and is a proven
        # addition.
        reference = self._tmp.name + "-hole-control.json"
        with mock.patch("os.scandir", self._failing_scandir("privado")):
            manifest = common.build_manifest(str(self.root))
        pathlib.Path(reference).write_text(
            common.render_json(manifest, mask=False), encoding="utf-8"
        )
        self.addCleanup(pathlib.Path(reference).unlink)
        (pathlib.Path(self.root) / "extra.md").write_text("# novo\n", encoding="utf-8")
        report = support.audit(self.root, reference_manifest=reference)
        self.assertEqual(report["reference_diff"]["added"], ["extra.md"])
        self.assertIs(report["matches_reference"], False)
        self.assertIn("SQA-026", support.codes(report))

    def test_a_reference_root_failure_cannot_prove_an_addition(self):
        # Same rule with the root itself unobserved in the reference: no entry
        # of the readable target can be called an addition.
        reference = self._tmp.name + "-root-hole.json"
        root_abs = pathlib.Path(self.root).resolve()
        real = os.scandir

        def fake(path="."):
            if pathlib.Path(str(path)).resolve() == root_abs:
                raise PermissionError(13, "simulado", str(path))
            return real(path)

        with mock.patch("os.scandir", fake):
            manifest = common.build_manifest(str(self.root))
        self.assertEqual([item["rel"] for item in manifest["walk_errors"]], ["."])
        pathlib.Path(reference).write_text(
            common.render_json(manifest, mask=False), encoding="utf-8"
        )
        self.addCleanup(pathlib.Path(reference).unlink)
        report = support.audit(self.root, reference_manifest=reference)
        self.assertEqual(report["reference_diff"]["added"], [])
        self.assertTrue(report["reference_diff"]["unconfirmed_added"])
        self.assertIsNone(report["matches_reference"])

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


class HolePathFormTest(unittest.TestCase):
    """A hole path is compared by directory, whatever form it was written in."""

    def test_trailing_slash_and_dot_prefix_still_cover_their_entries(self):
        # A reference manifest can be written by hand or by an older version;
        # "privado/" must cover "privado/x.md" exactly like "privado" does,
        # otherwise an unobserved subtree is read as a proven removal.
        for written in ("references/privado", "references/privado/", "./references/privado"):
            with self.subTest(written=written):
                manifest = {"entries": {}, "walk_errors": [{"rel": written, "reason": "permission"}]}
                holes = common.hole_paths(manifest)
                self.assertTrue(common.hole_covers(holes, "references/privado/segredo.md"))
                self.assertFalse(common.hole_covers(holes, "references/publico.md"))

    def test_an_unnamed_failure_covers_the_whole_tree(self):
        for written in ("", "/", "."):
            with self.subTest(written=written):
                holes = common.hole_paths({"entries": {}, "walk_errors": [{"rel": written}]})
                self.assertTrue(common.hole_covers(holes, "README.md"))

    def test_a_failure_without_a_known_location_covers_the_whole_tree(self):
        # UNKNOWN_PATH means the walk failed before it could name a directory,
        # so no entry can be called observed.
        holes = common.hole_paths({"entries": {}, "walk_errors": [{"rel": common.UNKNOWN_PATH}]})
        self.assertTrue(common.hole_covers(holes, "README.md"))
        self.assertTrue(common.hole_covers(holes, "references/qualquer.md"))

    def test_a_hand_written_manifest_with_bare_string_errors_is_read(self):
        # A manifest can be written by hand or by an older version; a bare
        # string names the directory, and must not crash the comparison.
        manifest = {"entries": {}, "walk_errors": ["references/privado", ""]}
        holes = common.hole_paths(manifest)
        self.assertTrue(common.hole_covers(holes, "references/privado/x.md"))
        self.assertTrue(common.hole_covers(holes, "README.md"))


class ManifestBoundaryTest(unittest.TestCase):
    """Malformed or unwrapped manifest records are normalized, not fatal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = support.build_skill(
            self._tmp.name, files={"README.md": "# alvo\n", "references/ok.md": "# ok\n"}
        )
        self.work = pathlib.Path(self._tmp.name)

    def _run(self, reference, out=None, cwd=None, fmt="json"):
        argv = [
            sys.executable,
            str(support.SCRIPTS / "audit_skill.py"),
            "audit",
            "--target",
            str(self.root),
            "--check-unchanged",
            str(reference),
            "--format",
            fmt,
        ]
        if out is not None:
            argv += ["--out", str(out)]
        return subprocess.run(
            argv,
            cwd=str(cwd or support.SCRIPTS.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _reference(self, name, payload):
        path = self.work / name
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )
        return path

    def test_an_unwrapped_record_is_read_like_a_one_item_list(self):
        # The canonical form is a list, but the contract also accepts a single
        # bare record as a one-item list. Iterating the string as-is would walk
        # it by character.
        for raw in ("references/privado", {"rel": "references/privado", "reason": "permission"}):
            with self.subTest(raw=raw):
                diff = common.diff_manifests(
                    {"entries": {}, "walk_errors": raw}, {"entries": {}}
                )
                self.assertEqual(diff["holes_before"], ["references/privado"])
                self.assertEqual(diff["walk_errors"][0]["rel"], "references/privado")

    def test_a_record_without_a_reason_keeps_the_unknown_reason(self):
        record = {"rel": "references/privado"}
        self.assertEqual(
            common.hole_paths({"entries": {}, "walk_errors": [record]}),
            ["references/privado"],
        )
        diff = common.diff_manifests(
            {"entries": {}, "walk_errors": [record]}, {"entries": {}}
        )
        self.assertEqual(
            diff["walk_errors"],
            [{"rel": "references/privado", "reason": common.UNKNOWN_REASON}],
        )

    def test_a_malformed_entries_mapping_is_a_usage_error(self):
        # ``entries`` as a list used to reach set() and exit 6 with no report.
        reference = self._reference("entries-list.json", {"entries": [{"rel": "README.md"}]})
        proc = self._run(reference)
        self.assertEqual(proc.returncode, common.EXIT_USAGE, proc.stderr)
        self.assertIn("entries", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_corrupted_reference_is_a_usage_error(self):
        reference = self._reference("truncated.json", '{"entries": {"README.md": ')
        proc = self._run(reference)
        self.assertEqual(proc.returncode, common.EXIT_USAGE, proc.stderr)
        self.assertIn("truncated.json", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_bare_string_walk_error_in_a_reference_file_is_accepted(self):
        # The reference file is read through ``load_manifest``; a single
        # unwrapped record must become one hole, not crash the diff.
        manifest = common.build_manifest(self.root)
        manifest["walk_errors"] = "references/privado"
        reference = self._reference("bare-string.json", manifest)
        proc = self._run(reference)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn(proc.returncode, (common.EXIT_USAGE, common.EXIT_INTERNAL))
        report = json.loads(proc.stdout)
        self.assertTrue(report["reference_compared"])
        self.assertIsNone(report["matches_reference"])
        self.assertEqual(
            report["reference_diff"]["walk_errors"],
            [{"rel": "references/privado", "reason": common.UNKNOWN_REASON}],
        )
        self.assertIn(
            support.audit_skill.INTEGRITY_CODE,
            [item["id"] for item in report["limits"]],
        )

    def test_a_reference_without_entries_is_a_usage_error(self):
        # A well-formed JSON document that is not a manifest: the boundary
        # refuses it instead of comparing against nothing.
        reference = self._reference("empty.json", {})
        proc = self._run(reference)
        self.assertEqual(proc.returncode, common.EXIT_USAGE, proc.stderr)
        self.assertIn("entries", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_relative_output_inside_the_target_is_refused(self):
        # Resolved against the working directory, not against the literal
        # argument, so the guard cannot be walked around with a bare name.
        proc = self._run(
            self._reference("ok.json", {"entries": {}}), out="report.md", cwd=self.root
        )
        self.assertEqual(proc.returncode, common.EXIT_USAGE, proc.stderr)
        self.assertFalse((self.root / "report.md").exists())

    def test_the_output_cannot_overwrite_the_reference(self):
        reference = self._reference("reference.json", {"entries": {}})
        before = reference.read_bytes()
        proc = self._run(reference, out=reference)
        self.assertEqual(proc.returncode, common.EXIT_USAGE, proc.stderr)
        self.assertEqual(reference.read_bytes(), before)

    def test_dotdot_cannot_smuggle_the_output_back_into_the_target(self):
        # Resolves inside the target through the parent directory; the literal
        # path is outside it, so only the resolved comparison catches this.
        out = self.root / ".." / self.root.name / "report.md"
        proc = self._run(self._reference("ok.json", {"entries": {}}), out=out)
        self.assertEqual(proc.returncode, common.EXIT_USAGE, proc.stderr)
        self.assertFalse((self.root / "report.md").exists())


class RestrictedParserDuplicateTest(unittest.TestCase):
    """A child key repeating its parent's name is not a duplicate key."""

    def test_a_nested_key_named_like_its_parent_is_not_flagged(self):
        parsed = common.read_frontmatter(
            "---\nname:\n  name: exemplo\nversion: 1\n---\n", backend="restricted"
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertNotIn(
            "duplicate_key", [item["construct"] for item in parsed["unsupported"]]
        )

    def test_a_real_duplicate_is_still_flagged(self):
        parsed = common.read_frontmatter(
            "---\nname: a\nname: b\n---\n", backend="restricted"
        )
        self.assertIn(
            "duplicate_key", [item["construct"] for item in parsed["unsupported"]]
        )

    def test_a_rejected_line_does_not_shadow_a_later_valid_key(self):
        # The indented root line is dropped, so ``name`` never entered the tree.
        # Consuming its marker anyway would report the valid line below as a
        # duplicate and lose a key the document does declare.
        parsed = common.read_frontmatter(
            "---\n  name: rejeitado\nname: aceito\n---\n", backend="restricted"
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["name"], "aceito")
        self.assertNotIn(
            "duplicate_key", [item["construct"] for item in parsed["unsupported"]]
        )

    def test_a_rejected_line_does_not_hide_a_later_duplicate(self):
        # Rejected line, then two accepted occurrences: the duplicate is still
        # a duplicate, so the real detection survives the fix above.
        parsed = common.read_frontmatter(
            "---\n  version: rejeitado\nversion: 1\nversion: 2\n---\n",
            backend="restricted",
        )
        self.assertIn(
            "duplicate_key", [item["construct"] for item in parsed["unsupported"]]
        )
        self.assertEqual(parsed["data"]["version"], "2")


class RestrictedFlowTest(unittest.TestCase):
    """Inline lists split on commas outside quotes only."""

    def test_a_comma_inside_quotes_is_one_item(self):
        parsed = common.read_frontmatter(
            '---\nname: x\nplatforms: ["win, linux"]\n---\n', backend="restricted"
        )
        self.assertEqual(parsed["data"]["platforms"], ["win, linux"])
        self.assertEqual(parsed["unsupported"], [])

    def test_a_malformed_flow_value_is_recorded_not_guessed(self):
        # A naive split used to turn ``["win, linux"]`` into two broken items
        # and an unbalanced bracket into a plausible-looking scalar.
        for raw in (
            'platforms: ["a", "b"',
            'platforms: [["a"]]',
            "platforms: [a] x",
            # An interior empty item is not data either: dropping it silently
            # would shorten the list without recording why.
            "platforms: [a, , b]",
        ):
            with self.subTest(raw=raw):
                parsed = common.read_frontmatter(
                    f"---\nname: x\n{raw}\n---\n", backend="restricted"
                )
                self.assertIsNone(parsed["data"].get("platforms"))
                self.assertIn(
                    "malformed_flow",
                    [item["construct"] for item in parsed["unsupported"]],
                )


class MissingRunnerTest(unittest.TestCase):
    """A catalogue entry without a runner must name itself, not exit 6."""

    def test_a_catalogue_check_without_runner_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = support.build_skill(tmp)
            registry = dict(support.audit_skill.REGISTRY)
            registry.pop("SQA-013", None)
            with mock.patch.object(support.audit_skill, "REGISTRY", registry):
                with self.assertRaisesRegex(RuntimeError, "SQA-013 has no runner"):
                    support.audit(root)


class ScriptCitationFormsTest(unittest.TestCase):
    """SQA-017 must see the forms a reader can actually type."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _report(self, line: str, suffix: str) -> dict:
        root = support.build_skill(
            self._tmp.name,
            name=f"forms-{suffix}",
            files={"scripts/cli.py": CLI_PY},
            readme=f"# forms\n\n## Uso\n\n{line}\n",
        )
        return support.audit(root)

    def test_the_module_form_resolves_to_the_script(self):
        report = self._report("`python -m scripts.cli audit`", "module")
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_a_windows_separator_resolves_to_the_script(self):
        report = self._report("`python scripts\\cli.py audit`", "backslash")
        self.assertEqual(support.findings(report, "SQA-017"), [])

    def test_a_missing_module_is_still_a_finding(self):
        report = self._report("`python -m scripts.missing audit`", "missing")
        self.assertEqual(len(support.findings(report, "SQA-017")), 1)


class RestrictedIndentationTest(unittest.TestCase):
    """Indentation that fits no open level is recorded, not guessed."""

    def test_indented_root_key_is_not_attached_to_the_root(self):
        # ``stray`` has no open parent. Attaching it at the root would invent a
        # top-level key the document never declared.
        parsed = common.read_frontmatter(
            "---\nname: x\n  stray: 1\n---\n", backend="restricted"
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertNotIn("stray", parsed["data"])
        self.assertIn(
            "invalid_indentation",
            [item["construct"] for item in parsed["unsupported"]],
        )

    def test_a_deeper_line_under_a_scalar_sibling_is_recorded(self):
        # ``b`` sits one column deeper than its sibling ``a``: YAML rejects it.
        # The old parser attached it to ``metadata`` as if it were a sibling.
        parsed = common.read_frontmatter(
            "---\nname: x\nmetadata:\n  a: 1\n   b: 2\n---\n", backend="restricted"
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["metadata"], {"a": "1"})
        self.assertIn(
            "invalid_indentation",
            [item["construct"] for item in parsed["unsupported"]],
        )

    def test_consistent_nesting_is_still_read(self):
        parsed = common.read_frontmatter(
            "---\nname: x\nmetadata:\n  a:\n    b: 1\n  c: 2\n---\n",
            backend="restricted",
        )
        self.assertEqual(parsed["unsupported"], [])
        self.assertEqual(parsed["data"]["metadata"], {"a": {"b": "1"}, "c": "2"})


if __name__ == "__main__":
    unittest.main()
