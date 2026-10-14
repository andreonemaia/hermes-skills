#!/usr/bin/env python3
"""Regressions for the findings owned by the report/_common/classify agent.

Every test is hermetic: it builds its own ``HERMES_HOME`` under a temporary
directory, so nothing here reads the developer's real ``state.db``,
``.usage.json`` or skills tree. ``tests/support.py`` is deliberately not
imported (another agent owns it); the few fixture helpers this file needs are
local, and the state.db schema is taken from ``evidence.SESSION_COLUMNS`` /
``evidence.MESSAGE_COLUMNS`` so the fixtures follow the reader's contract
instead of duplicating it.

Findings covered here: 6, 7, 8, 19, 22, 23, 24, 25, 26, 27, 28, 29, 30, 42,
48, 50, 56, 58, 59, 60, 61, 62.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"

#: Import the modules with HERMES_HOME pointing at a throwaway directory: a
#: module that resolved a path at import time would then resolve it inside the
#: sandbox, never inside the developer's real Hermes home.
_IMPORT_HOME = tempfile.mkdtemp(prefix="ser-import-home-")
_previous_home = os.environ.get("HERMES_HOME")
os.environ["HERMES_HOME"] = _IMPORT_HOME
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _common  # noqa: E402
import evidence  # noqa: E402
import report  # noqa: E402
from classify import (  # noqa: E402
    FINDING_STATES,
    USAGE_EDIT_KEYS,
    USAGE_USE_KEYS,
    USAGE_VIEW_KEYS,
    _counter_value,
    classify_scope,
    divergences,
    summarise,
)
from _common import (  # noqa: E402
    EXIT_CORRUPT,
    EXIT_MISSING,
    EXIT_NO_EVIDENCE,
    EXIT_OK,
    EXIT_OUTPUT,
    EXIT_USAGE,
    ReportError,
    SourceCorrupt,
    SourceMissing,
    load_usage,
    now_ts,
    open_ro,
    parse_iso,
    redact,
    REDACTED,
)

if _previous_home is None:
    os.environ.pop("HERMES_HOME", None)
else:
    os.environ["HERMES_HOME"] = _previous_home


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def sandbox():
    """A throwaway HERMES_HOME with an empty skills directory."""
    home = Path(tempfile.mkdtemp(prefix="ser-home-"))
    (home / "skills").mkdir()
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


#: Physical columns of the ``messages`` table, in the shape ``evidence`` reads
#: them: ``MESSAGE_COLUMNS`` is the SELECT list (it wraps ``content`` and
#: ``tool_calls`` in ``substr``/``length``), so the fixture table has to declare
#: the underlying names instead of reusing that string.
MESSAGE_TABLE_COLUMNS = (
    "id", "session_id", "message_uid", "role", "tool_name", "tool_call_id",
    "timestamp", "active", "compacted", "display_kind", "content", "tool_calls",
)


def _insert(conn, table, row):
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)" % (table, columns, marks),
        tuple(row.values()),
    )


def create_db(home, sessions=(), messages=()):
    """Write a minimal state.db with the columns evidence.StateDb reads."""
    path = Path(home) / "state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sessions (%s)" % ", ".join(evidence.SESSION_COLUMNS))
        conn.execute("CREATE TABLE messages (%s)" % ", ".join(MESSAGE_TABLE_COLUMNS))
        for row in sessions:
            _insert(conn, "sessions", row)
        for row in messages:
            _insert(conn, "messages", row)
        conn.commit()
    finally:
        conn.close()
    return path


def session_row(session_id="20261014_120000_aaaaaa", **overrides):
    row = {
        "id": session_id,
        "source": "cli",
        "title": "sessão de teste",
        "started_at": 1000.0,
        "ended_at": 1200.0,
        "message_count": 1,
        "tool_call_count": 0,
        "parent_session_id": None,
        "cwd": None,
        "git_repo_root": None,
        "git_branch": None,
        "hidden": 0,
        "archived": 0,
    }
    row.update(overrides)
    return row


def _message(idx, session_id, role, content, **overrides):
    row = {
        "id": idx,
        "session_id": session_id,
        "role": role,
        "content": content,
        "tool_call_id": None,
        "tool_calls": None,
        "tool_name": None,
        "timestamp": 1000.0 + idx,
        "active": 1,
        "compacted": 0,
        "display_kind": None,
        "message_uid": "uid-%d" % idx,
    }
    row.update(overrides)
    return row


def plain_messages(session_id="20261014_120000_aaaaaa"):
    """A session that mentions no skill at all: the report must find nothing."""
    return [_message(1, session_id, "user", "bom dia, tudo certo por aqui")]


def skill_view_messages(session_id="20261014_120000_aaaaaa", skill="alpha", ok=True):
    """A ``skill_view`` call paired with its observable result."""
    call_id = "call-1"
    tool_calls = json.dumps({
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": "skill_view", "arguments": json.dumps({"name": skill})},
        }],
    })
    body = json.dumps({"success": ok, "name": skill} if ok
                      else {"success": False, "error": "não encontrada"})
    return [
        _message(1, session_id, "user", "abre a skill %s" % skill),
        _message(2, session_id, "assistant", "", tool_calls=tool_calls),
        _message(3, session_id, "tool", body, tool_call_id=call_id, tool_name="skill_view"),
    ]


def add_skill(home, name="alpha", body=None):
    """Register ``name`` as a skill the runtime can see."""
    directory = Path(home) / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = "# %s\n" % name
    (directory / "SKILL.md").write_text(body, encoding="utf-8")


def run_cli(argv=(), home=None, now="2026-10-14T12:00:00Z", stdout=None):
    """Run ``report.main`` in-process against ``home`` with a frozen clock."""
    old_home, old_now = os.environ.get("HERMES_HOME"), os.environ.get("SER_NOW")
    if home is not None:
        os.environ["HERMES_HOME"] = str(home)
    if now is None:
        os.environ.pop("SER_NOW", None)
    else:
        os.environ["SER_NOW"] = now
    out = stdout if stdout is not None else io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = report.main(list(argv))
            except SystemExit as exc:  # argparse usage errors exit() directly
                code = exc.code if isinstance(exc.code, int) else 0
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        if old_now is None:
            os.environ.pop("SER_NOW", None)
        else:
            os.environ["SER_NOW"] = old_now
    text = out.getvalue() if hasattr(out, "getvalue") else ""
    return code, text, err.getvalue()


def payload_of(code, text):
    return json.loads(text)


# --------------------------------------------------------------------------- #
# exit codes: 1 must mean "no findings" and nothing else (#7, #48, #50)
# --------------------------------------------------------------------------- #

class TestExitCodes(unittest.TestCase):
    def test_no_findings_exits_one(self):
        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            code, text, err = run_cli(("--json",), home)
            self.assertEqual(code, EXIT_NO_EVIDENCE)
            self.assertEqual(payload_of(code, text)["counts"]["findings"], 0)
            self.assertNotIn("Traceback", err)

    def test_observed_skill_use_exits_zero(self):
        with sandbox() as home:
            add_skill(home)
            create_db(home, sessions=[session_row()], messages=skill_view_messages())
            code, text, err = run_cli(("--json",), home)
            self.assertEqual(code, EXIT_OK)
            self.assertIn("alpha", [f["skill"] for f in payload_of(code, text)["findings"]])
            self.assertNotIn("Traceback", err)

    def test_corrupt_source_exits_two(self):
        with sandbox() as home:
            (Path(home) / "state.db").write_bytes(b"isto nao e um banco sqlite")
            code, _text, err = run_cli(("--json",), home)
            self.assertEqual(code, EXIT_CORRUPT)
            self.assertNotEqual(code, EXIT_NO_EVIDENCE)
            self.assertNotIn("Traceback", err)

    def test_missing_source_exits_three(self):
        with sandbox() as home:
            code, _text, err = run_cli(("--json",), home)
            self.assertEqual(code, EXIT_MISSING)
            self.assertNotIn("Traceback", err)

    def test_usage_error_exits_four(self):
        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            code, _text, err = run_cli(("--since", "não é uma data"), home)
            self.assertEqual(code, EXIT_USAGE)
            self.assertNotIn("Traceback", err)

    def test_out_write_failure_exits_five(self):
        # EXIT_OUTPUT exists so that "the analysis worked but the file could not
        # be written" is not reported as a corrupt source (2) or a crash.
        self.assertEqual(EXIT_OUTPUT, 5)
        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            target = Path(home) / "diretorio-inexistente" / "relatorio.txt"
            code, _text, err = run_cli(("--out", str(target)), home)
            self.assertEqual(code, EXIT_OUTPUT)
            self.assertIn("não foi possível gravar", err)
            self.assertNotIn("Traceback", err)

    def test_internal_fault_never_exits_one(self):
        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            with mock.patch.object(report, "collect", side_effect=RuntimeError("boom")):
                code, _text, err = run_cli(("--json",), home)
            self.assertEqual(code, EXIT_CORRUPT)
            self.assertNotEqual(code, EXIT_NO_EVIDENCE)
            self.assertIn("erro interno", err)
            self.assertNotIn("Traceback", err)


class TestStreamHandling(unittest.TestCase):
    def test_non_ascii_output_survives_a_narrow_stream(self):
        raw = io.BytesIO()
        narrow = io.TextIOWrapper(raw, encoding="ascii")
        report.make_stream_tolerant(narrow)
        narrow.write("relatório: execução verificada ✓")
        narrow.flush()
        self.assertIn(b"relat", raw.getvalue())

    def test_stream_without_reconfigure_is_left_alone(self):
        plain = io.StringIO()
        report.make_stream_tolerant(plain)  # must not raise
        plain.write("ok")
        self.assertEqual(plain.getvalue(), "ok")

    def test_broken_pipe_returns_the_analysis_code_silently(self):
        class BrokenPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError(32, "Broken pipe")

        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            code, text, err = run_cli(("--json",), home, stdout=BrokenPipe())
        self.assertEqual(code, EXIT_NO_EVIDENCE)
        self.assertEqual(text, "")
        self.assertNotIn("Traceback", err)


# --------------------------------------------------------------------------- #
# path resolution: HERMES_HOME is read per run, never at import (#42)
# --------------------------------------------------------------------------- #

class TestHomeResolution(unittest.TestCase):
    def test_hermes_home_is_read_per_run_not_at_import(self):
        with sandbox() as with_data, sandbox() as empty:
            add_skill(with_data)
            create_db(with_data, sessions=[session_row()], messages=skill_view_messages())
            create_db(empty, sessions=[])

            first, _text, _err = run_cli(("--json",), with_data)
            second, _text, _err = run_cli(("--json",), empty)
            self.assertEqual(first, EXIT_OK, "a primeira execução deve ler o home indicado")
            self.assertEqual(second, EXIT_MISSING, "a segunda execução deve ler o novo home")


# --------------------------------------------------------------------------- #
# classify: reasons, limitations, scope, counters (#6, #19, #26, #29, #50, #59, #60)
# --------------------------------------------------------------------------- #

def script_event(scope="main", skill="alpha", ok=None, basis=None):
    event = {
        "at": "2026-10-14T12:00:01Z",
        "scope": scope,
        "session_id": "20261014_120000_aaaaaa",
        "kind": "skill_script",
        "skill": skill,
        "tool": "skill_script",
    }
    if ok is not None:
        event["ok"] = ok
    if basis is not None:
        event["basis"] = basis
    return event


class TestClassifyReasons(unittest.TestCase):
    def _classify(self, events, **kwargs):
        options = {
            "index_names": {"alpha"},
            "roots": [],
            "verify_artifacts": True,
            "window_filtered": False,
            "compacted_present": False,
        }
        options.update(kwargs)
        return classify_scope("main", events, {"id": "20261014_120000_aaaaaa"}, **options)

    def test_script_without_observed_result_is_unknown(self):
        findings = self._classify([script_event(ok=None)])
        self.assertEqual([f["state_reason"] for f in findings], ["skill_script_result_unknown"])
        self.assertIn("result_not_observable", findings[0]["limitations"])

    def test_script_with_observed_ok_keeps_the_ok_reason(self):
        findings = self._classify([script_event(ok=True)])
        self.assertEqual([f["state_reason"] for f in findings], ["skill_script_ok"])

    def test_script_with_observed_failure_is_failed(self):
        findings = self._classify([script_event(ok=False)])
        self.assertEqual([f["state_reason"] for f in findings], ["skill_script_failed"])

    def test_artifact_check_disabled_is_listed_once(self):
        for ok in (None, True):
            with self.subTest(ok=ok):
                findings = self._classify([script_event(ok=ok)], verify_artifacts=False)
                self.assertEqual(findings[0]["limitations"].count("artifact_check_disabled"), 1)

    def test_other_scopes_are_not_attributed_to_this_one(self):
        # A child session's evidence must never show up in the parent's findings.
        findings = self._classify([script_event(scope="child:20261014_120000_bbbbbb", ok=True)])
        self.assertEqual(findings, [])

    def test_orphan_view_limitation_reaches_the_finding(self):
        # #3: a skill_view result whose call was never observed keeps the load,
        # but the finding must repeat the event's limitation and flag the orphan
        # evidence, otherwise an incomplete load reads as a clean confirmation.
        event = {
            "at": "2026-10-14T12:00:01Z",
            "scope": "main",
            "session_id": "20261014_120000_aaaaaa",
            "kind": "skill_view",
            "skill": "alpha",
            "tool": "skill_view",
            "ok": True,
            "basis": "success_flag",
            "orphan_result": True,
            "limitations": ["out_of_transcript_load"],
        }
        finding = self._classify([event])[0]
        self.assertEqual(finding["state"], "loaded")
        self.assertEqual(finding["state_reason"], "skill_view_ok")
        self.assertIn("out_of_transcript_load", finding["limitations"])
        self.assertIs(finding["evidence"][0]["orphan_result"], True)

    def test_matched_view_keeps_the_finding_clean(self):
        # The counterpart of the test above: when the call is observed, neither
        # the limitation nor the orphan flag may appear.
        event = {
            "at": "2026-10-14T12:00:01Z",
            "scope": "main",
            "session_id": "20261014_120000_aaaaaa",
            "kind": "skill_view",
            "skill": "alpha",
            "tool": "skill_view",
            "ok": True,
            "basis": "success_flag",
            "call_id": "call-1",
        }
        finding = self._classify([event])[0]
        self.assertEqual(finding["limitations"], [])
        self.assertIsNone(finding["evidence"][0]["orphan_result"])


class TestCountersAndDivergences(unittest.TestCase):
    def test_first_whole_number_alias_wins(self):
        entry = {"use_count": 2, "uses": 9, "count": 4}
        self.assertEqual(_counter_value(entry, USAGE_USE_KEYS), 2)

    def test_bool_is_not_a_counter(self):
        # ``true`` is an ``int`` subclass; reading it as 1 would invent a use.
        self.assertIsNone(_counter_value({"use_count": True}, USAGE_USE_KEYS))
        self.assertEqual(_counter_value({"use_count": True, "uses": 3}, USAGE_USE_KEYS), 3)

    def test_integral_float_counts_and_strings_do_not(self):
        self.assertEqual(_counter_value({"load_count": 4.0}, USAGE_VIEW_KEYS), 4)
        self.assertIsNone(_counter_value({"load_count": "4"}, USAGE_VIEW_KEYS))
        self.assertIsNone(_counter_value({"load_count": 4.5}, USAGE_VIEW_KEYS))

    def test_divergence_skips_a_counter_the_file_does_not_carry(self):
        finding = {
            "skill": "alpha",
            "counts": {"loads_ok": 3, "invocations": 2, "edits": 0},
        }
        # ``uses`` and ``edits`` are absent from the file: a missing number is
        # not a divergence from zero (the transcript observed 2 uses), and only
        # ``loads`` genuinely disagrees.
        rows = divergences([finding], {"skills": {"alpha": {"load_count": 1}}})
        self.assertEqual([(row["field"], row["usage_json"], row["state_db"]) for row in rows],
                         [("loads", 1, 3)])

    def test_divergence_ignores_a_skill_the_file_does_not_know(self):
        finding = {"skill": "beta", "counts": {"loads_ok": 3, "invocations": 0, "edits": 0}}
        self.assertEqual(divergences([finding], {"skills": {"alpha": {"load_count": 1}}}), [])

    def test_edit_counter_aliases_are_read(self):
        self.assertEqual(_counter_value({"patch_count": 7}, USAGE_EDIT_KEYS), 7)

    def test_summarise_rejects_an_unknown_state(self):
        with self.assertRaises(ValueError):
            summarise([{"skill": "alpha", "state": "inventado", "scope": "main"}],
                      set(), set())

    def test_summarise_accepts_every_ladder_state(self):
        # ``discovered`` has no bucket of its own but is a legitimate state, so
        # the strict check must not reject it: rejecting it turned a valid
        # report into "erro interno".
        findings = [{"skill": "s-%d" % rank, "state": state, "scope": "main"}
                    for rank, state in enumerate(sorted(FINDING_STATES))]
        counts = summarise(findings, set(), set())
        self.assertEqual(counts["findings"], len(FINDING_STATES))
        self.assertEqual(counts["skills_loaded"], 1)
        self.assertEqual(counts["skills_invoked"], 1)
        self.assertEqual(counts["skills_execution_proven"], 1)
        self.assertEqual(counts["skills_unverifiable"], 1)
        self.assertEqual(counts["skills_aggregate_only"], 0)


# --------------------------------------------------------------------------- #
# _common: timestamps, sources, redaction (#22, #23, #24, #25, #27, #58)
# --------------------------------------------------------------------------- #

class TestTimestamps(unittest.TestCase):
    def test_parse_iso_rejects_non_finite_and_out_of_range(self):
        for value in ("nan", "inf", "-inf", "1e999", "", "   ", "ontem"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_iso(value)

    def test_parse_iso_iso_branch_honours_the_same_range_as_the_numeric_one(self):
        # Regression (revalidation p2b/N1): the ISO branch used to return an
        # instant the numeric branch rejects, so a timestamp could resolve
        # outside the range ``iso()`` can render.
        for value in ("9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_iso(value)
        # A boundary that stays inside the range is still accepted.
        self.assertGreater(parse_iso("9999-12-31T23:59:59Z"), 0)

    def test_parse_iso_accepts_the_documented_forms(self):
        self.assertEqual(parse_iso("0"), 0.0)
        self.assertEqual(parse_iso("2026-10-14T12:00:00Z"),
                         parse_iso("2026-10-14T12:00:00+00:00"))
        self.assertGreater(parse_iso("2026-10-14T12:00:00"), 0)

    def test_invalid_ser_now_is_a_usage_error_not_a_traceback(self):
        old = os.environ.get("SER_NOW")
        os.environ["SER_NOW"] = "ontem"
        try:
            with self.assertRaises(ReportError):
                now_ts()
        finally:
            if old is None:
                os.environ.pop("SER_NOW", None)
            else:
                os.environ["SER_NOW"] = old

    def test_invalid_ser_now_reaches_the_cli_as_exit_four(self):
        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            code, _text, err = run_cli(("--json",), home, now="ontem")
            self.assertEqual(code, EXIT_USAGE)
            self.assertIn("SER_NOW", err)
            self.assertNotIn("Traceback", err)


class TestSources(unittest.TestCase):
    def test_open_ro_maps_a_vanished_or_unopenable_file_to_missing(self):
        with sandbox() as home:
            with self.assertRaises(SourceMissing):
                open_ro(str(Path(home) / "state.db"))
            # A directory is not a database: the open fails the same way a file
            # that disappeared between the check and the open does.
            with self.assertRaises(SourceMissing):
                open_ro(str(home))

    def test_usage_with_only_malformed_entries_is_corrupt(self):
        with sandbox() as home:
            (Path(home) / "skills" / ".usage.json").write_text(
                json.dumps({"skills": {"notas": "texto"}}), encoding="utf-8")
            with self.assertRaises(SourceCorrupt):
                load_usage(str(Path(home) / "skills" / ".usage.json"))

    def test_usage_root_of_the_wrong_type_is_corrupt(self):
        with sandbox() as home:
            path = Path(home) / "skills" / ".usage.json"
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            with self.assertRaises(SourceCorrupt):
                load_usage(str(path))

    def test_usage_malformed_entries_are_reported_not_dropped(self):
        with sandbox() as home:
            path = Path(home) / "skills" / ".usage.json"
            path.write_text(json.dumps({"skills": {"alpha": {"use_count": 2}, "notas": "texto"}}),
                            encoding="utf-8")
            usage = load_usage(str(path))
            self.assertEqual(sorted(usage["skills"]), ["alpha"])
            self.assertEqual(usage["malformed_entries"], ["notas"])

    def test_usage_corruption_reaches_the_cli_as_exit_two(self):
        with sandbox() as home:
            create_db(home, sessions=[session_row()], messages=plain_messages())
            (Path(home) / "skills" / ".usage.json").write_text(
                json.dumps({"skills": {"notas": "texto"}}), encoding="utf-8")
            code, _text, err = run_cli(("--json",), home)
            self.assertEqual(code, EXIT_CORRUPT)
            self.assertIn("usage", err.lower())
            self.assertNotIn("Traceback", err)


class TestRedaction(unittest.TestCase):
    def test_session_ids_are_dropped_in_both_hex_cases(self):
        for session_id in ("20261014_070717_d6a10f", "20261014_070717_D6A10F"):
            with self.subTest(session_id=session_id):
                cleaned = redact("sessão %s analisada" % session_id)
                self.assertNotIn(session_id, cleaned)
                self.assertNotIn("20261014_070717", cleaned)

    def test_a_jwt_signature_is_consumed_with_its_token(self):
        # #56: header.payload.signature — the credential is the third segment,
        # and a pattern that stops after the payload leaves it in the report.
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        signature = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        cleaned = redact("colou o token %s.%s.%s no chat" % (header, payload, signature))
        self.assertNotIn(signature, cleaned)
        self.assertNotIn(header, cleaned)
        self.assertIn(REDACTED, cleaned)

    def test_the_unsigned_two_segment_form_is_still_consumed(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        cleaned = redact("colou o token %s no chat" % token)
        self.assertNotIn(token, cleaned)


class TestScopeOptions(unittest.TestCase):
    """#28, #30, #61, #62: what the run consulted, and with which repository."""

    def test_sources_enabled_names_the_skills_index(self):
        # #61: the index is scanned on every run, so a payload that omits it
        # understates what was looked at.
        with sandbox() as home:
            add_skill(home)
            create_db(home, sessions=[session_row()], messages=skill_view_messages())
            code, text, _err = run_cli(("--json",), home)
            enabled = payload_of(code, text)["options"]["sources_enabled"]
            self.assertIn("state_db", enabled)
            self.assertIn("skills_index", enabled)
            self.assertNotIn("usage_json", enabled)  # no .usage.json was written
            self.assertNotIn("git", enabled)         # no repository was known

    def test_git_is_listed_only_when_a_repository_is_used(self):
        with sandbox() as home:
            add_skill(home)
            create_db(home, sessions=[session_row()], messages=skill_view_messages())
            repo = Path(home) / "checkout"
            repo.mkdir()
            code, text, _err = run_cli(("--json", "--repo", str(repo)), home)
            self.assertIn("git", payload_of(code, text)["options"]["sources_enabled"])

    def test_max_skills_caps_the_index_and_zero_means_no_cap(self):
        # #28: --max-skills is documented as "0 = sem limite"; a negative value
        # is a usage error, not a silent full list.
        with sandbox() as home:
            add_skill(home, "alpha")
            add_skill(home, "beta")
            add_skill(home, "gama")
            create_db(home, sessions=[session_row()], messages=skill_view_messages())
            code, text, _err = run_cli(("--json", "--max-skills", "2"), home)
            self.assertEqual(len(payload_of(code, text)["available_skills"]), 2)
            code, text, _err = run_cli(("--json", "--max-skills", "0"), home)
            self.assertEqual(len(payload_of(code, text)["available_skills"]), 3)
            code, _text, err = run_cli(("--json", "--max-skills", "-1"), home)
            self.assertEqual(code, EXIT_USAGE)
            self.assertIn("max-skills", err)
            self.assertNotIn("Traceback", err)

    def test_each_scope_is_verified_against_its_own_repository(self):
        # #30: a child session may have run in another checkout, so it must not
        # inherit the parent's repository.
        with sandbox() as home:
            parent = session_row("20261014_120000_aaaaaa", git_repo_root="C:/repos/pai")
            child = session_row("20261014_120000_bbbbbb", git_repo_root="C:/repos/filho",
                                parent_session_id=parent["id"])
            create_db(home, sessions=[parent, child],
                      messages=skill_view_messages(parent["id"]))
            seen: dict[str, str | None] = {}
            real = report.collect_session_events

            def spy(db, session, index, since, until, scope, repo, verify):
                seen[scope] = repo
                return real(db, session, index, since, until, scope, repo, verify)

            with mock.patch.object(report, "collect_session_events", spy):
                run_cli(("--json", "--session", parent["id"], "--include-children"), home)
            self.assertEqual(seen.get("main"), "C:/repos/pai")
            self.assertEqual(seen.get("child:%s" % child["id"]), "C:/repos/filho")

    def test_schema_compatibility_is_reported_from_the_real_check(self):
        # #62: the field is the schema check's own verdict, not a constant.
        with sandbox() as home:
            add_skill(home)
            create_db(home, sessions=[session_row()], messages=skill_view_messages())
            verdict = {"tables": {}, "compatible": False,
                       "problems": ["missing table: sessions"]}
            with mock.patch.object(evidence.StateDb, "_check_schema", return_value=verdict):
                code, text, _err = run_cli(("--json",), home)
            schema = payload_of(code, text)["sources"]["state_db"]["schema"]
            self.assertFalse(schema["compatible"])
            self.assertEqual(schema["problems"], ["missing table: sessions"])


class TestLimitationsAndTies(unittest.TestCase):
    """#50 (code side) and #57: silences must be named, and rank-0 ties explicit."""

    def test_a_time_window_is_declared_as_a_limitation(self):
        # The contract says a filtered window becomes a limitation, never
        # silence: a report read without it looks like a complete transcript.
        with sandbox() as home:
            add_skill(home)
            create_db(home, sessions=[session_row()], messages=skill_view_messages())
            code, text, _err = run_cli(
                ("--json", "--until", "2030-01-01T00:00:00Z"), home)
            findings = payload_of(code, text)["findings"]
            self.assertEqual([f["skill"] for f in findings], ["alpha"])
            self.assertIn("window_filtered", findings[0]["limitations"])

    def test_rank_zero_never_presents_an_aggregate_counter_as_available(self):
        # #57: "available" and "unverifiable" share rank 0, so an aggregate
        # counter with no transcript link must still be reported as
        # unverifiable — the tie may not silently promote it.
        with sandbox() as home:
            add_skill(home, "alpha")
            (Path(home) / "skills" / ".usage.json").write_text(
                json.dumps({"skills": {"alpha": {"use_count": 4,
                                                 "last_used_at": "2026-10-14T10:00:00Z"}}}),
                encoding="utf-8")
            create_db(home, sessions=[session_row()], messages=plain_messages())
            code, text, _err = run_cli(("--json",), home)
            found = {f["skill"]: f for f in payload_of(code, text)["findings"]}
            self.assertEqual(found["alpha"]["state"], "unverifiable")
            self.assertEqual(found["alpha"]["state_rank"], 0)


if __name__ == "__main__":
    unittest.main()
