"""Hermetic test suite for the ``skill-execution-report`` CLI.

Every test builds a throw-away ``HERMES_HOME`` through ``support.Sandbox`` and
runs the CLI against it. Nothing here reads the real Hermes state, the user's
configuration, the network, or any external service.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import pathlib
import re
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
        # The load is real but no call in the transcript points at it, so the
        # finding has to carry that limitation and the orphan flag instead of
        # reading like a clean, confirmed load.
        self.assertIn("out_of_transcript_load", finding["limitations"])
        self.assertIs(finding["evidence"][0]["orphan_result"], True)


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

    def test_unobserved_script_result_renders_a_pt_br_reason(self):
        # Regressão (revalidação p3): `skill_script_result_unknown` não tinha
        # rótulo em PT-BR, então o relatório em texto exibia o código cru e
        # descrevia a invocação sem dizer que o resultado era desconhecido.
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1")],
        )
        code, out, _err = self.run_cli(())
        self.assertEqual(code, 0)
        self.assertNotIn("skill_script_result_unknown", out)
        self.assertIn("resultado não foi observado", out)

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

    # Achado 47: o caminho de escopo filho só era testado no caso feliz. Os dois
    # casos abaixo fixam que a ausência de filhos e um filho sem linhas legíveis
    # não viram erro nem contagem inventada.
    def test_include_children_without_children_is_not_an_error(self):
        self.box.skill("alpha")
        self.box.database(sessions=[session("s1")], messages=self.loaded_pair("alpha", "a1"))
        code, payload, _out, err = self.run_json(("--json", "--session", "s1", "--include-children"))
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err)
        self.assertEqual(payload["analysis"]["children_analysed"], [])
        # Nada foi excluído, então o sinal de exclusão não deve aparecer.
        self.assertNotIn("children_excluded", payload["unknowns"])
        self.assertEqual(payload["counts"]["skills_loaded"], 1)

    def test_child_without_readable_rows_is_still_scoped_not_dropped(self):
        self.box.skill("alpha")
        self.box.skill("beta")
        self.box.database(
            sessions=[session("s1", started_at=1000.0),
                      session("s2", started_at=2000.0, parent_session_id="s1")],
            messages=self.loaded_pair("alpha", "a1"),
        )
        code, payload, _out, err = self.run_json(
            ("--json", "--session", "s1", "--include-children"))
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err)
        # O filho existe e é analisado; o que ele não tem é evidência de uso.
        self.assertEqual(payload["analysis"]["children_analysed"], ["s2"])
        self.assertIsNone(finding_for(payload, "beta"))
        self.assertNotIn("children_excluded", payload["unknowns"])

    def test_hidden_child_is_analysed_when_the_parent_is_named(self):
        # `hidden` esconde a sessão das listagens, não a torna inexistente: o
        # vínculo por parent_session_id é a autoridade quando o pai foi nomeado.
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
                      session("s2", started_at=2000.0, parent_session_id="s1", hidden=1)],
            messages=messages,
        )
        code, payload, _out, err = self.run_json(
            ("--json", "--session", "s1", "--include-children"))
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err)
        self.assertEqual(payload["analysis"]["children_analysed"], ["s2"])
        self.assertEqual(find_one(payload, "beta", scope="child:s2")["state"], "loaded")


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
        # #61: every consulted source is named, so --no-usage drops usage_json
        # from the list instead of leaving the reader guessing what was read.
        self.assertEqual(payload["options"]["sources_enabled"],
                         ["state_db", "skills_index"])
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

    # Achado 15: as falhas de fonte só eram verificadas em modo texto. O contrato
    # documentado (README, tabela de códigos) diz que 2/3/4 saem com mensagem em
    # stderr e *sem* relatório, então stdout precisa ficar vazio também em --json.
    def test_missing_source_under_json_keeps_stdout_empty(self):
        code, out, err = self.run_cli(("--json",))
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("fonte ausente", err)
        self.assertNotIn("Traceback", err)

    def test_corrupted_source_under_json_keeps_stdout_empty(self):
        self.box.database(raw=b"isto nao e um banco sqlite")
        code, out, err = self.run_cli(("--json",))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("fonte ilegível", err)
        self.assertNotIn("Traceback", err)

    def test_ambiguous_prefix_under_json_keeps_stdout_empty(self):
        self.box.database(sessions=[session("20260101_000000_aaaaaa"),
                                    session("20260101_000000_bbbbbb")])
        code, out, err = self.run_cli(("--json", "--session", "20260101_000000_"))
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("ambig", err.lower())

    # Achado 46: a mensagem de fonte ausente carrega a referência do usuário. Ela
    # passa pelo mesmo `redact` do relatório, senão o stderr vira via de vazamento.
    def test_missing_source_message_redacts_the_session_reference(self):
        self.box.database(sessions=[session("20260101_000000_aaaaaa")])
        ref = "20261012_080613_51acc2"
        code, out, err = self.run_cli(("--json", "--session", ref))
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("fonte ausente", err)
        self.assertNotIn(ref, err)
        self.assertIn("<session>", err)

    def test_corrupted_source_message_is_redacted(self):
        # O caminho do banco vem do usuário (--db) e não pode reaparecer cru.
        target = self.box.root / "privado" / "state.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"isto nao e um banco sqlite")
        code, out, err = self.run_cli(("--json", "--db", str(target)))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn(str(target), err)
        self.assertNotIn("Traceback", err)


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

    # Achado 44: os testes só cobriam o artefato que existe. Os dois casos
    # negativos abaixo fixam que "caminho citado" nunca é "caminho comprovado".
    def _invoked_with_output(self, output: str, cwd):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1", cwd=str(cwd))],
            messages=[
                msg(assistant("a1", 1000.0, [script_call(script_argv("alpha"))]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1",
                         json.dumps({"exit_code": 0, "output": output})), "s1"),
            ],
        )

    def test_artifact_path_that_does_not_exist_does_not_prove_execution(self):
        self._invoked_with_output("gravado em out/ghost.json", self.box.root)
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["state"], "invoked")
        self.assertEqual(finding["verification"]["state"], "unverifiable")
        self.assertIsNone(finding["verification"]["basis"])
        self.assertIn("no_correlated_artifact", finding["limitations"])

    def test_artifact_outside_allowed_roots_is_ignored(self):
        outside = self.box.root.parent / ("fora-%s.txt" % self.box.root.name)
        outside.write_text("x", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        self._invoked_with_output("gravado em %s" % outside, self.box.root)
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        # O arquivo existe, mas fora das raízes permitidas (cwd/repo): não conta.
        self.assertEqual(finding["state"], "invoked")
        self.assertEqual(finding["verification"]["state"], "unverifiable")
        self.assertIn("no_correlated_artifact", finding["limitations"])


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

    # Achado 78: a deduplicação só era testada com linhas byte-idênticas. Os dois
    # casos abaixo fixam os extremos: a mesma linha relida com timestamp diferente
    # não pode contar duas vezes, e uma chamada repetida em outro turno deve contar.
    def test_duplicate_rows_with_distinct_timestamps_count_once(self):
        self.box.skill("alpha")
        pair = self.loaded_pair("alpha", "a1")
        replayed = [dict(row, timestamp=row["timestamp"] + 0.5) for row in pair]
        self.box.database(sessions=[session("s1")], messages=pair + replayed)
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["counts"]["loads_ok"], 1)
        self.assertEqual(len(finding["evidence"]), 1)

    def test_same_call_repeated_in_another_turn_counts_twice(self):
        self.box.skill("alpha")
        self.box.database(
            sessions=[session("s1")],
            messages=[
                msg(assistant("a1", 1000.0, [view_call("alpha", "call-1")]), "s1"),
                msg(tool("a1-r", 1001.0, "call-1",
                         json.dumps({"success": True}), name="skill_view"), "s1"),
                msg(assistant("a2", 2000.0, [view_call("alpha", "call-2")]), "s1"),
                msg(tool("a2-r", 2001.0, "call-2",
                         json.dumps({"success": True}), name="skill_view"), "s1"),
            ],
        )
        code, payload, _out, _err = self.run_json()
        self.assertEqual(code, 0)
        finding = find_one(payload, "alpha")
        self.assertEqual(finding["counts"]["loads_ok"], 2)
        self.assertEqual(len(finding["evidence"]), 2)

    # Achado 75: o sandbox e o leitor precisam concordar sobre o schema. A
    # checagem é comportamental: o leitor real roda sobre o banco que o sandbox
    # constrói e precisa declará-lo compatível. Se um lado ganhar uma coluna que
    # o outro exige, este teste falha, sem inspecionar a forma da implementação.
    # Lacuna declarada: colunas *extras* no sandbox não são detectadas, porque o
    # leitor ignora colunas desconhecidas por projeto.
    def test_the_reader_accepts_the_test_fixture_schema(self):
        self.box.skill("alpha")
        self.box.database(sessions=[session("s1")],
                          messages=self.loaded_pair("alpha", "a1"))
        _code, payload, _out, _err = self.run_json()
        schema = payload["sources"]["state_db"]["schema"]
        self.assertTrue(schema["compatible"], schema.get("problems"))
        self.assertEqual(schema["problems"], [])


class TestPtVocabulary(unittest.TestCase):
    """O vocabulário do relatório em PT-BR precisa cobrir o do classificador.

    Regressão (revalidação p3): um motivo novo (`skill_script_result_unknown`)
    ficou sem rótulo e o relatório em texto caiu no fallback, imprimindo o
    código cru; na direção oposta, `skill_dir_only` era um rótulo sem produtor.
    A checagem é tabela-contra-tabela: nenhuma suposição sobre a forma do
    código, apenas que os dois vocabulários são o mesmo conjunto.
    """

    def test_every_state_reason_has_a_label_and_no_orphan_label_exists(self):
        import classify
        import render

        self.assertEqual(
            sorted(set(classify.STATE_REASONS) - set(render.PT_REASONS)), [])
        self.assertEqual(
            sorted(set(render.PT_REASONS) - set(classify.STATE_REASONS)), [])

    def test_every_limitation_has_a_label_and_no_orphan_label_exists(self):
        import classify
        import render

        self.assertEqual(
            sorted(set(classify.LIMITATION_CODES) - set(render.PT_LIMITATIONS)), [])
        self.assertEqual(
            sorted(set(render.PT_LIMITATIONS) - set(classify.LIMITATION_CODES)), [])


class TestDocVocabulary(unittest.TestCase):
    """A documentação de motivos precisa espelhar o vocabulário do código.

    Regressão (tratamento da revisão do PR #6): a linha 7 da tabela de decisão
    prometia `edit_without_load` como `state_reason`, mas o classificador emite
    `skill_manage_only` e reserva `edit_without_load` para *limitação*. Nenhum
    teste acusava a divergência, porque a checagem existente só comparava código
    com os rótulos PT-BR. Aqui a checagem é documento-contra-tabela: lê a
    referência e exige que cada código citado exista de fato no emissor.
    """

    DOC = (pathlib.Path(__file__).resolve().parents[1]
           / "references" / "evidence-rules.md")

    @classmethod
    def _text(cls):
        return cls.DOC.read_text(encoding="utf-8")

    @classmethod
    def _codes_after(cls, label):
        """Códigos entre crases no parágrafo que segue ``label``."""
        tail = cls._text().split(label, 1)[1]
        return set(re.findall(r"`([a-z_]+)`", tail.split("\n\n", 1)[0]))

    @classmethod
    def _table_reasons(cls):
        reasons = set()
        for line in cls._text().splitlines():
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            if len(cells) == 4 and re.fullmatch(r"\d+", cells[0]):
                reasons.add(cells[3].strip("`"))
        return reasons

    def test_the_decision_table_only_names_real_state_reasons(self):
        import classify

        reasons = self._table_reasons()
        self.assertTrue(reasons, "a tabela de decisão não foi encontrada")
        self.assertEqual(sorted(reasons - set(classify.STATE_REASONS)), [])

    def test_the_documented_limitations_are_the_real_ones(self):
        import classify

        self.assertEqual(self._codes_after("`limitations` (codes):"),
                         set(classify.LIMITATION_CODES))

    def test_the_documented_unknowns_are_real_and_emitted(self):
        """Todo ``unknown`` documentado precisa ter rótulo e produtor.

        `PT_UNKNOWNS` não serve como vocabulário fechado: ele repete
        `artifact_check_disabled`, que também é limitação. Então a checagem é
        duas direções parciais: o código tem rótulo PT-BR e aparece como
        literal nos scripts (ou seja, algo realmente o emite). É a direção que
        pega o defeito observado: a doc prometia o que nenhum código produzia.
        """
        import render

        documented = self._codes_after("`unknowns` (codes):")
        self.assertTrue(documented, "a lista de unknowns não foi encontrada")
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SCRIPTS_DIR.glob("*.py"))
        )
        for code in sorted(documented):
            with self.subTest(code=code):
                self.assertIn(code, render.PT_UNKNOWNS)
                self.assertIn('"%s"' % code, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
