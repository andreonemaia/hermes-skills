"""Hermetic test suite for the ``skill-execution-report`` CLI.

Every test builds a throw-away ``HERMES_HOME`` through ``support.Sandbox`` and
runs the CLI against it. Nothing here reads the real Hermes state, the user's
configuration, the network, or any external service.

Run with::

    python -m unittest discover -s tests -t tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

from support import (
    SCRIPTS_DIR,
    SKILL_DIR,
    Sandbox,
    assistant,
    finding_for,
    findings_of,
    session,
    tool,
    user,
)

FIXED_NOW = "2026-10-13T12:00:00Z"
SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
WINDOWS_HOME = r"C:\Users\someone\private"



def find_one(payload: dict, name: str, scope: str = "main") -> dict:
    """Like ``finding_for`` but fails loudly instead of returning ``None``."""
    found = finding_for(payload, name, scope)
    if found is None:
        raise AssertionError("finding ausente para %r (scope=%s)" % (name, scope))
    return found

def msg(row: dict, sid: str) -> dict:
    """Attach a session id to a message row built by the support helpers."""
    return dict(row, session_id=sid)


def view_call(name: str, call_id: str = "call-1"):
    return ("skill_view", {"name": name}, call_id)


def script_call(command: str, call_id: str = "call-1"):
    return ("terminal", {"command": command}, call_id)


def script_argv(name: str) -> str:
    return "python skills/%s/scripts/run.py" % name


class SerCase(unittest.TestCase):
    """Base case: one sandbox per test, in-process CLI invocation."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def run_cli(self, argv=(), now: str = FIXED_NOW):
        return self.box.run(argv, now=now)

    def run_json(self, argv=("--json",), now: str = FIXED_NOW):
        code, out, err = self.run_cli(argv, now=now)
        self.assertNotIn("Traceback", err)
        self.assertTrue(out.strip(), "esperava payload em stdout; stderr=%r" % err)
        return code, json.loads(out), out, err

    def loaded_pair(self, name: str, uid: str, call_id: str = "call-1",
                    ts: float = 1000.0, **extra) -> list:
        """An assistant ``skill_view`` call plus its successful result."""
        return [
            msg(assistant(uid, ts, [view_call(name, call_id)]), "s1"),
            msg(tool(uid + "-r", ts + 1, call_id,
                     json.dumps({"success": True, "description": "carregada"}),
                     name="skill_view"), "s1"),
        ]


# --------------------------------------------------------------- conservatism
class TestConservativeClassification(SerCase):
    def test_available_skill_is_not_reported_as_used(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(user("u1", 1000.0, "bom dia"), "s1")],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 1)
        self.assertEqual(payload["counts"]["skills_available"], 1)
        self.assertEqual(payload["counts"]["findings"], 0)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["available_skills"], ["alpha"])

    def test_text_mention_is_discovered_not_loaded(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(user("u1", 1000.0, "podemos usar skills/alpha/ aqui"), "s1")],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertIsNotNone(finding)
        self.assertEqual(finding["state"], "discovered")
        self.assertEqual(finding["state_reason"], "mention_only")
        self.assertEqual(finding["counts"]["mentions"], 1)
        self.assertEqual(finding["counts"]["loads_ok"], 0)
        self.assertEqual(finding["counts"]["invocations"], 0)
        self.assertEqual(payload["counts"]["skills_loaded"], 0)
        self.assertEqual(payload["counts"]["skills_invoked"], 0)

    def test_context_rows_do_not_produce_mentions(self):
        self.box.skill("alpha")
        row = msg(user("u1", 1000.0, "índice: skills/alpha/"), "s1")
        row["role"] = "system"
        self.box.database(sessions=[session("s1")], messages=[row])
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 1)
        self.assertEqual(payload["counts"]["findings"], 0)

    def test_successful_skill_view_proves_loading_only(self):
        self.box.skill("alpha")
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "loaded")
        self.assertEqual(finding["state_reason"], "skill_view_ok")
        self.assertEqual(finding["counts"]["loads_ok"], 1)
        self.assertEqual(payload["counts"]["skills_loaded"], 1)
        self.assertEqual(payload["counts"]["skills_execution_proven"], 0)
        self.assertEqual(finding["result"]["state"], "unknown")

    def test_failed_skill_view_is_not_a_load(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [view_call("alpha")]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1",
                         json.dumps({"success": False, "error": "não encontrada"}),
                         name="skill_view"), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "discovered")
        self.assertEqual(finding["state_reason"], "skill_view_failed")
        self.assertEqual(finding["counts"]["loads_ok"], 0)
        self.assertEqual(finding["counts"]["loads_failed"], 1)
        self.assertEqual(payload["counts"]["skills_loaded"], 0)

    def test_duplicate_display_rows_are_counted_once(self):
        self.box.skill("alpha")
        pair = self.loaded_pair("alpha", "a1")
        self.box.database(sessions=[session("s1")], messages=pair + [dict(r) for r in pair])
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["counts"]["loads_ok"], 1)
        self.assertEqual(len(finding["evidence"]), 1)

    def test_unrelated_tool_call_is_not_attributed(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [("terminal", {"command": "ls -la"}, "call-9")]), "s1"),
                msg(tool("a1-r", 1001.0, "call-9",
                         json.dumps({"exit_code": 0, "output": "ok"})), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 1)
        self.assertEqual(payload["counts"]["findings"], 0)

    def test_orphan_skill_view_result_is_attributed_by_name(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(tool("t1", 1000.0, "call-x",
                               json.dumps({"name": "alpha", "success": True}),
                               name="skill_view"), "s1")],
        )
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "loaded")
        self.assertTrue(finding["evidence"][0]["ok"])


# ------------------------------------------------------------------ execution
class TestExecutionEvidence(SerCase):
    def test_successful_script_without_artifact_is_invoked_not_proven(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1",
                         json.dumps({"exit_code": 0, "output": "pronto"})), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "invoked")
        self.assertEqual(finding["state_reason"], "skill_script_ok")
        self.assertEqual(finding["result"]["state"], "succeeded")
        self.assertEqual(finding["result"]["basis"], "exit_code")
        self.assertEqual(finding["verification"]["state"], "unverifiable")
        self.assertIn("no_correlated_artifact", finding["limitations"])
        self.assertEqual(payload["counts"]["skills_invoked"], 1)
        self.assertEqual(payload["counts"]["skills_execution_proven"], 0)

    def test_verified_artifact_promotes_to_execution_proven(self):
        self.box.skill("alpha")
        self.box.file("out/report.json")
        self.box.database(
            sessions=[session("s1", cwd=str(self.box.root))],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1", json.dumps(
                    {"exit_code": 0, "output": "relatório gravado em out/report.json"})), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "execution_proven")
        self.assertEqual(finding["state_reason"], "skill_script_ok_verified_artifact")
        self.assertEqual(finding["verification"]["state"], "verified")
        self.assertEqual(finding["verification"]["basis"], "artifact_file")
        self.assertEqual(finding["verification"]["detail"], "out/report.json")
        self.assertEqual(payload["counts"]["skills_execution_proven"], 1)

    def test_artifact_check_can_be_disabled(self):
        self.box.skill("alpha")
        self.box.file("out/report.json")
        self.box.database(
            sessions=[session("s1", cwd=str(self.box.root))],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1", json.dumps(
                    {"exit_code": 0, "output": "relatório gravado em out/report.json"})), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json(("--json", "--no-artifact-check"))
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "invoked")
        self.assertEqual(finding["verification"]["state"], "unverifiable")
        self.assertIn("artifact_check_disabled", finding["limitations"])
        self.assertIn("artifact_check_disabled", payload["limitations"])
        self.assertFalse(payload["options"]["artifact_check"])

    def test_failed_script_is_invoked_with_observable_failure(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1", json.dumps(
                    {"exit_code": 1, "output": "boom", "error": "falhou"})), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "invoked")
        self.assertEqual(finding["state_reason"], "skill_script_failed")
        self.assertEqual(finding["result"]["state"], "failed")
        self.assertEqual(finding["counts"]["invocations_failed"], 1)
        self.assertEqual(payload["counts"]["skills_invoked"], 1)
        self.assertNotEqual(finding["state"], "execution_proven")

    def test_skill_manage_without_load_is_edit_without_load(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(assistant("a1", 1000.0, [
                ("skill_manage", {"operations": [{"name": "alpha"}]}, "call-1")]), "s1")],
        )
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "discovered")
        self.assertEqual(finding["state_reason"], "skill_manage_only")
        self.assertIn("edit_without_load", finding["limitations"])
        self.assertEqual(finding["counts"]["edits"], 1)


# ---------------------------------------------------------------- child scope
class TestChildScopes(SerCase):
    def _sources(self):
        self.box.skill("alpha")
        self.box.skill("beta")
        messages = self.loaded_pair("alpha", "a1")
        messages += [
            msg(assistant("a2", 2000.0, [view_call("beta", "call-2")]), "s2"),
            msg(tool("a2-r", 2001.0, "call-2",
                     json.dumps({"success": True}), name="skill_view"), "s2"),
        ]
        self.box.database(
            sessions=[session("s1", started_at=1000.0),
                      session("s2", started_at=2000.0, parent_session_id="s1")],
            messages=messages,
        )

    def test_children_are_excluded_by_default(self):
        self._sources()
        code, payload, _out, _err = self.run_json(("--json", "--session", "s1"))
        self.assertIsNotNone(finding_for(payload, "alpha"))
        self.assertIsNone(finding_for(payload, "beta"))
        self.assertEqual(payload["analysis"]["children_analysed"], [])
        self.assertIn("children_excluded", payload["unknowns"])
        self.assertEqual(payload["counts"]["skills_loaded"], 1)

    def test_children_are_included_only_on_request(self):
        self._sources()
        code, payload, _out, _err = self.run_json(
            ("--json", "--session", "s1", "--include-children"))
        child = find_one(payload, "beta", scope="child:s2")
        self.assertIsNotNone(child)
        self.assertIn("child_scope", child["limitations"])
        self.assertEqual(payload["analysis"]["children_analysed"], ["s2"])
        self.assertNotIn("children_excluded", payload["unknowns"])
        self.assertEqual(payload["counts"]["skills_loaded"], 2)
        self.assertEqual(len(findings_of(payload, "main")), 1)

    def test_compacted_messages_stay_analysable(self):
        self.box.skill("alpha")
        pair = self.loaded_pair("alpha", "a1")
        pair[0]["compacted"] = 1
        self.box.database(sessions=[session("s1")], messages=pair)
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "loaded")
        self.assertIn("compacted_messages_present", payload["limitations"])
        self.assertIn("compacted_messages_present", finding["limitations"])
        self.assertEqual(payload["sessions_analysed"][0]["compacted_messages"], 1)


# ------------------------------------------------------------ aggregate usage
class TestAggregateSources(SerCase):
    def test_usage_counters_are_never_summed_with_transcript(self):
        self.box.skill("alpha")
        self.box.usage({"skills": {"alpha": {"view_count": 5, "use_count": 1}}})
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))
        code, payload, _out, _err = self.run_json()
        self.assertEqual(payload["counts"]["skills_loaded"], 1)
        rows = {row["field"]: (row["usage_json"], row["state_db"])
                for row in payload["divergences"]}
        self.assertEqual(rows["loads"], (5, 1))
        self.assertEqual(rows["uses"], (1, 0))
        self.assertEqual([f for f in payload["findings"] if f["scope"] == "aggregate"], [])
        self.assertIn("usage_counters_are_global", payload["unknowns"])
        self.assertTrue(payload["sources"]["usage_json"]["present"])

    def test_usage_only_skill_is_unverifiable_aggregate(self):
        self.box.usage({"skills": {"ghost": {"use_count": 2}}})
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(user("u1", 1000.0, "oi"), "s1")],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "ghost", scope="aggregate")
        self.assertIsNotNone(finding)
        self.assertEqual(finding["state"], "unverifiable")
        self.assertEqual(finding["state_reason"], "usage_counter_only")
        self.assertEqual(payload["counts"]["skills_aggregate_only"], 1)
        self.assertEqual(payload["counts"]["skills_execution_proven"], 0)
        self.assertIn("out_of_transcript_load", finding["limitations"])

    def test_no_usage_flag_ignores_the_aggregate_source(self):
        self.box.usage({"skills": {"ghost": {"use_count": 2}}})
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(user("u1", 1000.0, "oi"), "s1")],
        )
        code, payload, _out, _err = self.run_json(("--json", "--no-usage"))
        self.assertEqual(code, 1)
        self.assertFalse(payload["sources"]["usage_json"]["present"])
        self.assertEqual(payload["options"]["sources_enabled"], ["state_db"])
        self.assertEqual(payload["counts"]["findings"], 0)
        self.assertEqual(payload["divergences"], [])


# ------------------------------------------------------- orphan skill events
class TestOrphanSkillEvents(SerCase):
    def test_skill_result_without_call_is_surfaced_as_unknown(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(tool("t9", 1002.0, "c9",
                               json.dumps({"success": True, "name": "alpha"}),
                               name="skill_view"), "s1")],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        self.assertIn("skill_events_without_call", payload["unknowns"])

    def test_attributed_skill_result_does_not_raise_the_unknown(self):
        self.box.skill("alpha")
        self.box.database(sessions=[session("s1")],
                          messages=self.loaded_pair("alpha", "a1"))
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        self.assertNotIn("skill_events_without_call", payload["unknowns"])


# ------------------------------------------------------------ source failures
class TestSourceFailures(SerCase):
    def test_missing_database_is_reported_and_not_created(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 3)
        self.assertIn("fonte ausente", err)
        self.assertEqual(out, "")
        self.assertFalse(self.box.db_path.exists())

    def test_corrupted_database_is_reported_as_unreadable(self):
        self.box.database(raw=b"isto nao e um banco sqlite")
        code, _out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("fonte ilegível", err)

    def test_incompatible_schema_is_reported_as_unreadable(self):
        self.box.database(sessions=[session("s1")], drop=("tool_calls",))
        code, _out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("fonte ilegível", err)

    def test_empty_session_table_is_a_missing_source(self):
        self.box.database(sessions=[])
        code, _out, err = self.run_cli()
        self.assertEqual(code, 3)
        self.assertIn("fonte ausente", err)

    def test_unknown_session_reference_is_a_missing_source(self):
        self.box.database(sessions=[session("20260101_000000_aaaaaa")])
        code, _out, err = self.run_cli(("--session", "nao-existe"))
        self.assertEqual(code, 3)
        self.assertIn("fonte ausente", err)

    def test_ambiguous_session_prefix_is_refused(self):
        self.box.database(sessions=[session("20260101_000000_aaaaaa"),
                                    session("20260101_000000_bbbbbb")])
        code, _out, err = self.run_cli(("--session", "20260101_000000_"))
        self.assertEqual(code, 3)
        self.assertIn("ambig", err.lower())

    def test_unique_session_prefix_resolves(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("20260101_000000_abcdef")],
            messages=[dict(r, session_id="20260101_000000_abcdef")
                      for r in self.loaded_pair("alpha", "a1")],
        )
        code, payload, _out, _err = self.run_json(("--json", "--session", "20260101_000000_abc"))
        self.assertEqual(payload["analysis"]["session_id"], "20260101_000000_abcdef")


# ------------------------------------------------------------- window/limits
class TestWindowAndLimits(SerCase):
    def test_window_excludes_events_outside_the_range(self):
        self.box.skill("alpha")
        self.box.skill("beta")
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(user("u1", 1000.0, "veja skills/alpha/"), "s1"),
                msg(user("u2", 2000.0, "veja skills/beta/"), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json(
            ("--json", "--since", "1970-01-01T00:20:00Z"))
        self.assertIsNone(finding_for(payload, "alpha"))
        beta = finding_for(payload, "beta")
        self.assertIsNotNone(beta)
        self.assertIn("window_filtered", beta["limitations"])
        self.assertIn("window_filtered", payload["limitations"])
        self.assertTrue(payload["analysis"]["window"]["filtered"])

    def test_max_skills_truncates_the_available_list(self):
        for name in ("a1", "a2", "a3"):
            self.box.skill(name)
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(user("u1", 1000.0, "oi"), "s1")],
        )
        code, payload, _out, _err = self.run_json(("--json", "--max-skills", "2"))
        self.assertEqual(len(payload["available_skills"]), 2)
        self.assertEqual(payload["counts"]["skills_available"], 3)


# -------------------------------------------------------------------- privacy
class TestPrivacy(SerCase):
    def _run_with_secrets(self, argv=()):
        self.box.skill("alpha")
        output = "api_key: %s em %s" % (SECRET, WINDOWS_HOME)
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1",
                         json.dumps({"exit_code": 0, "output": output})), "s1"),
            ],
        )
        return self.run_cli(argv)

    def test_excerpts_never_leak_credentials_or_paths(self):
        code, out, err = self._run_with_secrets()
        self.assertNotIn("Traceback", err)
        self.assertNotIn(SECRET, out)
        self.assertNotIn("sk-ABC", out)
        self.assertNotIn(r"C:\Users\someone", out)
        self.assertIn("[redacted]", out)
        self.assertIn("<home>", out)

    def test_json_payload_is_also_sanitised(self):
        code, out, err = self._run_with_secrets(("--json",))
        self.assertNotIn(SECRET, out)
        self.assertNotIn(r"C:\Users\someone", out)
        self.assertIn("<home>", out)

    def test_escaped_paths_do_not_leak_the_account_name(self):
        """Transcripts hold JSON-escaped paths (C:\\\\Users\\\\name\\\\AppData).

        The account name must not survive just because every separator in the
        excerpt is doubled.
        """
        self.box.skill("alpha")
        escaped = WINDOWS_HOME.replace("\\", "\\\\")
        output = json.dumps({"config": escaped + "\\\\AppData\\\\Roaming", "cwd": escaped})
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1",
                         json.dumps({"exit_code": 0, "output": output})), "s1"),
            ],
        )
        for argv in ((), ("--json",)):
            code, out, err = self.run_cli(argv)
            self.assertNotIn("someone", out, "argv=%r" % (argv,))
            self.assertIn("<home>", out, "argv=%r" % (argv,))


# ---------------------------------------------------- read-only and determinism
class TestReadOnlyAndDeterminism(SerCase):
    def _fixture(self):
        self.box.skill("alpha")
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))

    def test_state_db_is_never_modified(self):
        self._fixture()
        before_bytes = self.box.db_path.read_bytes()
        before_files = sorted(p.name for p in self.box.home.iterdir())
        self.run_cli()
        self.assertEqual(self.box.db_path.read_bytes(), before_bytes)
        self.assertEqual(sorted(p.name for p in self.box.home.iterdir()), before_files)

    def test_json_is_deterministic_and_matches_the_human_report(self):
        self._fixture()
        code_json, first, _err1 = self.run_cli(("--json",))
        _code_json2, second, _err2 = self.run_cli(("--json",))
        self.assertEqual(first, second)
        payload = json.loads(first)
        self.assertEqual(
            first,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        self.assertEqual(payload["schema"], "skill-execution-report/v1")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["clock_source"], "SER_NOW")
        self.assertEqual(payload["generated_at"], "2026-10-13T12:00:00Z")

        code_human, human, _err3 = self.run_cli()
        self.assertEqual(code_human, code_json)
        self.assertIn("carregadas: 1", human)
        self.assertIn("invocadas: 0", human)
        self.assertIn("execução comprovada: 0", human)
        self.assertIn("alpha", human)
        self.assertIn("carregada", human)

    def test_unknown_cost_and_duration_are_not_invented(self):
        self._fixture()
        code, payload, _out, _err = self.run_json()
        self.assertIsNone(payload["analysis"]["period"]["duration_seconds"])
        self.assertIsNone(payload["analysis"]["period"]["ended_at"])
        for key in ("cost_unavailable", "duration_unavailable", "session_in_progress"):
            self.assertIn(key, payload["unknowns"])
        self.assertNotIn("estimated_cost_usd", json.dumps(payload))

    def test_extreme_timestamps_do_not_break_the_report(self):
        """Out-of-range or pre-epoch instants degrade instead of crashing."""
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1", started_at=-1.0)],
            messages=[msg(user("u1", 253402300799.0, "veja skills/alpha/"), "s1")],
        )
        code, out, err = self.run_cli()
        self.assertNotIn("Traceback", err)
        self.assertNotIn("Traceback", out)
        self.assertEqual(code, 0)
        self.assertIn("alpha", out)

    def test_out_writes_only_the_requested_file(self):
        self._fixture()
        target = self.box.root / "relatorio.json"
        code, out, err = self.run_cli(("--json", "--out", str(target)))
        self.assertEqual(target.read_text(encoding="utf-8"), out)
        self.assertEqual(sorted(p.name for p in self.box.home.iterdir()), ["skills", "state.db"])


# ------------------------------------------------------------ process contract
class TestProcessContract(SerCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["HERMES_HOME"] = str(self.box.home)
        env["SER_NOW"] = FIXED_NOW
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "report.py"), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(SKILL_DIR), env=env, check=False,
        )

    def test_help_exits_zero(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--session", result.stdout)
        self.assertIn("--include-children", result.stdout)

    def test_unknown_flag_exits_with_usage_code(self):
        result = self._run("--definitely-not-a-flag")
        self.assertEqual(result.returncode, 4)

    def test_inverted_window_is_a_usage_error(self):
        self.box.database(sessions=[session("s1")])
        result = self._run("--since", "2026-10-13T13:00:00Z",
                           "--until", "2026-10-13T12:00:00Z")
        self.assertEqual(result.returncode, 4)
        self.assertIn("erro:", result.stderr)

    def test_end_to_end_run_matches_in_process_output(self):
        self.box.skill("alpha")
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))
        result = self._run("--json")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["counts"]["skills_loaded"], 1)
        self.assertIn("Relatório somente leitura", payload["note"])


# --------------------------------------------------------------- git artifact
class TestCommitVerification(SerCase):
    def _repo_with_commit(self):
        if not shutil.which("git"):
            self.skipTest("git não está no PATH")
        repo = self.box.dir("repo")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  text=True, env=env, check=True).stdout.strip()

        try:
            git("init", "-q")
            (repo / "f.txt").write_text("x", encoding="utf-8")
            git("add", "f.txt")
            git("commit", "-q", "-m", "c")
            return repo, git("rev-parse", "HEAD")
        except (subprocess.CalledProcessError, OSError) as exc:
            self.skipTest("git indisponível neste ambiente: %s" % exc)

    def test_commit_in_output_verifies_execution(self):
        repo, sha = self._repo_with_commit()
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1", git_repo_root=str(repo))],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1", json.dumps(
                    {"exit_code": 0, "output": "commit %s criado" % sha})), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "execution_proven")
        self.assertEqual(finding["verification"]["state"], "verified")
        self.assertEqual(finding["verification"]["basis"], "artifact_commit")
        self.assertEqual(finding["verification"]["detail"], sha[:12])


# -------------------------------------------------------------- robust inputs
class TestRobustInputs(SerCase):
    """Input shapes that must not be silently mis-read (found by review)."""

    def test_usage_counter_written_as_float_is_not_zero(self):
        self.box.skill("alpha")
        self.box.usage({"skills": {"alpha": {"view_count": 5.0, "use_count": 2.0}}})
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))
        _code, payload, _out, _err = self.run_json()
        rows = {row["field"]: (row["usage_json"], row["state_db"])
                for row in payload["divergences"]}
        self.assertEqual(rows["loads"], (5, 1))
        self.assertEqual(rows["uses"], (2, 0))

    def test_boolean_usage_counter_is_not_read_as_one(self):
        self.box.skill("alpha")
        self.box.usage({"skills": {"ghost": {"use_count": True}}})
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        self.assertEqual([f["skill"] for f in payload["findings"]], ["alpha"])
        self.assertEqual(payload["counts"]["skills_aggregate_only"], 0)

    def test_unusable_usage_counters_are_skipped_not_invented(self):
        from classify import _counter

        self.assertEqual(_counter({"use_count": 4.0}, ("use_count",)), 4)
        self.assertEqual(_counter({"use_count": 2.5}, ("use_count",)), 0)
        self.assertEqual(_counter({"use_count": "3"}, ("use_count",)), 0)
        self.assertEqual(_counter({"use_count": True}, ("use_count",)), 0)
        self.assertEqual(_counter({}, ("use_count",)), 0)

    def test_database_path_with_percent_sign_is_read(self):
        self.box.skill("alpha")
        db = self.box.database(sessions=[session("s1")],
                               messages=self.loaded_pair("alpha", "a1"))
        # A literal "%20" in the path is decoded by SQLite's URI parser, so the
        # path must be percent-escaped before it is handed over.
        target = self.box.dir("pct%20dir") / "state.db"
        shutil.copyfile(db, target)
        code, payload, _out, _err = self.run_json(("--json", "--db", str(target)))
        self.assertEqual(code, 0)
        self.assertEqual(find_one(payload, "alpha")["state"], "loaded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
