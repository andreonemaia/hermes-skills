"""Controlled A×B comparison of ``--include-children`` (investigação A×B).

The main scope must be classified from main-scope events only, so flipping
``include_children`` must not move a single main-scope finding: not its state,
not its evidence UIDs, not its classifications. Every test below runs one
sandbox twice, in the same process, with that flag and nothing else varying, and
compares the two payloads field by field.

Two pairs are covered, git available and git unavailable, and the git condition
is held identical inside a pair, so each pair isolates the flag alone. Fixtures
are deterministic (``SER_NOW`` pinned, synthetic ``state.db``), so a failure here
is a real regression rather than clock or environment noise.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

from support import (
    SCRIPTS_DIR,
    Sandbox,
    assistant,
    find_one,
    finding_for,
    findings_of,
    session,
    tool,
)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import classify  # noqa: E402  (needs the sys.path bootstrap above)

FIXED_NOW = "2026-10-13T12:00:00Z"
MAIN = "s1"
CHILD = "s2"
ABSENT_SHA = "0" * 40

# Top-level paths that must never move when only the flag varies.
INVARIANT = (
    "schema",
    "schema_version",
    "note",
    "generated_at",
    "clock_source",
    "available_skills",
    "sources",
    "options",
    "divergences",
    "analysis.scope",
    "analysis.session_id",
    "analysis.title",
    "analysis.session_source",
    "analysis.period",
    "analysis.counters_are_global",
    "analysis.window.since",
    "analysis.window.until",
    "analysis.window.filtered",
)

# Paths that may legitimately move, and only because the child scope was added.
ALLOWED_PREFIXES = (
    "analysis.scope",
    "analysis.include_children",
    "analysis.children_analysed",
    "analysis.window.scope",
    "counts",
    "evidence_events",
    "sessions_analysed",
    "unknowns",
    "limitations",
    "findings",
)


def msg(row, sid):
    """Attach a session id to a message row built by the support helpers."""
    return dict(row, session_id=sid)


def view_call(name, call_id):
    return ("skill_view", {"name": name}, call_id)


def script_call(command, call_id):
    return ("terminal", {"command": command}, call_id)


def script_argv(name):
    return "python skills/%s/scripts/run.py" % name


def load_rows(name, uid, call_id, ts, sid):
    """An assistant ``skill_view`` call and its successful result, in ``sid``."""
    return [
        msg(assistant(uid, ts, [view_call(name, call_id)]), sid),
        msg(tool(uid + "-r", ts + 1, call_id,
                 json.dumps({"success": True, "description": "carregada"}),
                 name="skill_view"), sid),
    ]


def run_rows(name, uid, call_id, ts, sid, output):
    """An assistant terminal call running ``name``'s script, plus its result."""
    return [
        msg(assistant(uid, ts, [script_call(script_argv(name), call_id)]), sid),
        msg(tool(uid + "-r", ts + 1, call_id,
                 json.dumps({"exit_code": 0, "output": output})), sid),
    ]


def diff_paths(left, right, path=""):
    """Dotted paths whose value differs. Dicts are walked, everything else is a leaf."""
    if isinstance(left, dict) and isinstance(right, dict):
        paths = set()
        for key in set(left) | set(right):
            child = "%s.%s" % (path, key) if path else str(key)
            paths |= diff_paths(left.get(key), right.get(key), child)
        return paths
    return set() if left == right else {path or "<root>"}


def event(scope, skill, kind, **overrides):
    """A minimal classification event, shaped like the ones evidence.py emits."""
    row = {
        "scope": scope,
        "skill": skill,
        "kind": kind,
        "ok": True,
        "ts": 1000.0,
        "at": "2026-10-13T12:00:00Z",
        "message_uid": "u-%s-%s" % (scope, skill),
        "tool": "terminal",
        "detail": "detalhe",
        "basis": "exit_code",
        "excerpt": "trecho",
        "error": None,
        "artifact_files": [],
        "artifact_commits": [],
        "source": "state_db",
    }
    row.update(overrides)
    return row


class ScopeIsolationAB(unittest.TestCase):
    """One sandbox per comparison; both arms always run in the same process."""

    def setUp(self):
        self.repo = None
        self.sha = None
        self._fresh()

    def _fresh(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.repo = None
        self.sha = None

    # ----------------------------------------------------------- fixtures
    def _make_repo(self):
        """A real repository with one commit, or a skip when git is missing."""
        if shutil.which("git") is None:
            self.skipTest("git indisponível neste ambiente")
        repo = self.box.dir("repo")
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True, env=env,
                                  check=True).stdout

        git("init", "-q")
        (repo / "f.txt").write_text("conteudo\n", encoding="utf-8")
        git("add", "f.txt")
        git("commit", "-q", "-m", "inicial")
        self.repo = repo
        self.sha = git("rev-parse", "HEAD").strip()
        return repo

    def _fixture(self, *, git):
        """The same inputs for both arms: three main scripts and one child load."""
        repo = self._make_repo() if git else None
        for name in ("alpha", "delta", "epsilon", "beta", "gamma"):
            self.box.skill(name)
        self.box.file("out/alpha.json", "{}")
        cwd = str(self.box.dir())
        common = {"cwd": cwd, "git_repo_root": str(repo) if repo else None}

        rows = []
        # main: alpha's artifact exists, delta has none, epsilon names a commit
        # that only a repository can confirm.
        rows += run_rows("alpha", "a1", "call-a1", 1000.0, MAIN,
                         "gravado em out/alpha.json")
        rows += run_rows("delta", "d1", "call-d1", 1100.0, MAIN,
                         "concluido, nada gravado")
        rows += run_rows("epsilon", "e1", "call-e1", 1200.0, MAIN,
                         "commit %s criado" % (self.sha or ABSENT_SHA))
        # child: loads beta, which must never surface in the main scope.
        rows += load_rows("beta", "k1", "call-k1", 2000.0, CHILD)

        self.box.database(
            sessions=[session(MAIN, started_at=900.0, ended_at=1500.0, **common),
                      session(CHILD, started_at=1900.0,
                              parent_session_id=MAIN, **common)],
            messages=rows,
        )
        return {"main_uids": {"a1", "a1-r", "d1", "d1-r", "e1", "e1-r"},
                "child_uids": {"k1", "k1-r"}}

    # ------------------------------------------------------------- runner
    def _fingerprint(self):
        """sha256 of every input the run may read, so 'same inputs' is checkable."""
        root = self.box.dir()
        files = [p for p in root.rglob("*") if p.is_file()]
        if self.repo is not None:
            files += [p for p in self.repo.rglob("*")
                      if p.is_file() and ".git" not in p.parts]
        return {str(p.relative_to(root)).replace("\\", "/"):
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(files)}

    def _payload(self, argv):
        code, out, err = self.box.run(argv, now=FIXED_NOW)
        self.assertNotIn("Traceback", err)
        self.assertTrue(out.strip(), "sem payload em stdout; stderr=%r" % err)
        return code, json.loads(out)

    def _pair(self, *, git, extra=()):
        """Arm A (children excluded) then arm B (children included), one sandbox."""
        facts = self._fixture(git=git)
        before = self._fingerprint()
        code_a, arm_a = self._payload(("--json", "--session", MAIN, *extra))
        middle = self._fingerprint()
        code_b, arm_b = self._payload(
            ("--json", "--session", MAIN, "--include-children", *extra))
        after = self._fingerprint()
        self.assertEqual(before, middle, "a corrida A alterou as entradas")
        self.assertEqual(middle, after, "a corrida B alterou as entradas")
        self.assertEqual(code_a, code_b)
        return arm_a, arm_b, facts

    # --------------------------------------------------------- assertions
    def _assert_isolated(self, arm_a, arm_b, facts, *, git):
        main_a = findings_of(arm_a, "main")
        main_b = findings_of(arm_b, "main")

        # Eventos, UIDs, evidências e classificações do escopo main: idênticos.
        self.assertEqual(json.dumps(main_a, sort_keys=True),
                         json.dumps(main_b, sort_keys=True),
                         "o escopo main mudou ao ligar --include-children")
        self.assertEqual({f["skill"] for f in main_a},
                         {"alpha", "delta", "epsilon"})
        states = {f["skill"]: f["state"] for f in main_a}
        self.assertEqual(states["alpha"], "execution_proven")
        self.assertEqual(states["delta"], "invoked")
        self.assertEqual(states["epsilon"],
                         "execution_proven" if git else "invoked")
        if not git:
            self.assertEqual(
                find_one(arm_a, "epsilon")["verification"]["state"],
                "unverifiable")

        uids_a = {ev.get("message_uid") for f in main_a for ev in f["evidence"]}
        uids_b = {ev.get("message_uid") for f in main_b for ev in f["evidence"]}
        self.assertEqual(uids_a, uids_b)
        self.assertTrue(uids_a & facts["main_uids"], "main sem UIDs do main")
        self.assertEqual(uids_a & facts["child_uids"], set(),
                         "UID de sessão filha no escopo main")
        self.assertEqual(uids_b & facts["child_uids"], set(),
                         "UID de sessão filha no escopo main")

        # Nenhum evento, skill ou evidência da sessão filha no escopo main.
        for arm in (arm_a, arm_b):
            self.assertIsNone(finding_for(arm, "beta"),
                              "beta (só na filha) vazou para o escopo main")
            self.assertIsNone(finding_for(arm, "gamma"))
        self.assertTrue(all(f["scope"] == "main" for f in arm_a["findings"]),
                        "a corrida A não pode conter escopo além de main")
        self.assertIsNone(finding_for(arm_a, "beta", "child:" + CHILD),
                          "escopo filho apareceu sem --include-children")
        child = find_one(arm_b, "beta", "child:" + CHILD)
        self.assertEqual(child["session_id"], CHILD)
        self.assertEqual(child["state"], "loaded")

    def _assert_only_the_flag_moved(self, arm_a, arm_b, *, git):
        moved = diff_paths(arm_a, arm_b)

        offenders = {p for p in moved
                     if any(p == path or p.startswith(path + ".") for path in INVARIANT)}
        self.assertEqual(offenders, set(),
                         "campo que não deveria variar: %s" % sorted(offenders))

        outside = {p for p in moved
                   if not any(p == pre or p.startswith(pre + ".")
                              for pre in ALLOWED_PREFIXES)}
        self.assertEqual(outside, set(),
                         "diferença não explicada por --include-children: %s"
                         % sorted(outside))

        self.assertFalse(arm_a["analysis"]["include_children"])
        self.assertTrue(arm_b["analysis"]["include_children"])
        self.assertEqual(arm_a["analysis"]["scope"],
                         arm_b["analysis"]["scope"])
        self.assertEqual(arm_a["analysis"]["children_analysed"], [])
        self.assertEqual(arm_b["analysis"]["children_analysed"], [CHILD])
        self.assertEqual([s["scope"] for s in arm_a["sessions_analysed"]],
                         ["main"])
        self.assertEqual([s["scope"] for s in arm_b["sessions_analysed"]],
                         ["main", "child:" + CHILD])
        # Contadores de uso não podem se mover: incluir a filha não eleva o main.
        for key in ("skills_available", "skills_aggregate_only",
                    "skills_execution_proven", "skills_invoked",
                    "skills_unverifiable"):
            self.assertEqual(arm_a["counts"][key], arm_b["counts"][key],
                             "counts.%s variou ao incluir a filha" % key)
        self.assertGreater(arm_b["evidence_events"], arm_a["evidence_events"])
        self.assertIn("children_excluded", arm_a["unknowns"])
        self.assertNotIn("children_excluded", arm_b["unknowns"])

        # A condição de git é idêntica dentro do par A×B.
        self.assertEqual(arm_a["sources"]["git"], arm_b["sources"]["git"])
        self.assertEqual(arm_a["options"]["sources_enabled"],
                         arm_b["options"]["sources_enabled"])
        self.assertEqual(arm_a["sources"]["git"]["available"], git)

    # ------------------------------------------------------------- tests
    def test_main_scope_is_unchanged_by_the_flag(self):
        """A×B com Git disponível e indisponível: só `include_children` varia."""
        for git in (False, True):
            with self.subTest(git=git):
                self._fresh()
                arm_a, arm_b, facts = self._pair(git=git)
                self._assert_isolated(arm_a, arm_b, facts, git=git)
                self._assert_only_the_flag_moved(arm_a, arm_b, git=git)

    def test_child_execution_does_not_upgrade_a_main_finding(self):
        """O sintoma histórico: execução da filha elevando o estado do main."""
        self.box.skill("alpha")
        self.box.file("out/child.json", "{}")   # só a filha tem artefato real
        cwd = str(self.box.dir())
        rows = run_rows("alpha", "a1", "call-a1", 1000.0, MAIN,
                        "gravado em out/alpha.json")          # não existe
        rows += run_rows("alpha", "k1", "call-k1", 2000.0, CHILD,
                         "gravado em out/child.json")          # existe
        self.box.database(
            sessions=[session(MAIN, started_at=900.0, cwd=cwd),
                      session(CHILD, started_at=1900.0,
                              parent_session_id=MAIN, cwd=cwd)],
            messages=rows,
        )

        _, arm_a = self._payload(("--json", "--session", MAIN))
        _, arm_b = self._payload(
            ("--json", "--session", MAIN, "--include-children"))

        self.assertEqual(find_one(arm_a, "alpha")["state"], "invoked")
        self.assertEqual(find_one(arm_b, "alpha")["state"], "invoked",
                         "execução da sessão filha elevou o escopo main")
        self.assertEqual(
            json.dumps(findings_of(arm_a, "main"), sort_keys=True),
            json.dumps(findings_of(arm_b, "main"), sort_keys=True))

        child_alpha = find_one(arm_b, "alpha", "child:" + CHILD)
        self.assertEqual(child_alpha["verification"]["state"], "verified")
        self.assertEqual(child_alpha["state"], "execution_proven")

    def test_input_drift_explains_invoked_to_execution_proven(self):
        """`invoked` -> `execution_proven` vem da entrada, não da flag."""
        self.box.skill("alpha")
        cwd = str(self.box.dir())
        self.box.database(
            sessions=[session(MAIN, started_at=900.0, cwd=cwd)],
            messages=run_rows("alpha", "a1", "call-a1", 1000.0, MAIN,
                              "gravado em out/alpha.json"),
        )

        before = self._fingerprint()
        _, round_a1 = self._payload(("--json", "--session", MAIN))
        _, round_b1 = self._payload(
            ("--json", "--session", MAIN, "--include-children"))
        self.assertEqual(find_one(round_a1, "alpha")["state"], "invoked")
        self.assertEqual(find_one(round_b1, "alpha")["state"], "invoked")

        # A única mudança entre as rodadas: o artefato nomeado passa a existir.
        self.box.file("out/alpha.json", "{}")
        self.assertNotEqual(before, self._fingerprint())

        _, round_a2 = self._payload(("--json", "--session", MAIN))
        _, round_b2 = self._payload(
            ("--json", "--session", MAIN, "--include-children"))
        for payload in (round_a2, round_b2):
            finding = find_one(payload, "alpha")
            self.assertEqual(finding["state"], "execution_proven")
            self.assertEqual(finding["verification"]["basis"], "artifact_file")
        self.assertEqual(
            json.dumps(findings_of(round_a2, "main"), sort_keys=True),
            json.dumps(findings_of(round_b2, "main"), sort_keys=True))

    def test_classify_scope_ignores_events_from_other_scopes(self):
        """Prova de unidade: o filtro de escopo é a fronteira de isolamento."""
        roots = [str(self.box.dir())]
        names = {"alpha", "beta"}
        main_event = event("main", "alpha", "skill_script", message_uid="a1")
        child_event = event("child:" + CHILD, "beta", "skill_view",
                            message_uid="k1")

        main_only = classify.classify_scope(
            "main", [main_event], {"id": MAIN}, names, roots, True, False, False)
        main_mixed = classify.classify_scope(
            "main", [main_event, child_event], {"id": MAIN}, names, roots,
            True, False, False)
        self.assertEqual(len(main_only), 1)
        self.assertEqual(main_mixed, main_only,
                         "evento de outro escopo influenciou a classificação")

        child_only = classify.classify_scope(
            "child:" + CHILD, [child_event], {"id": CHILD}, names, roots,
            True, False, False)
        child_mixed = classify.classify_scope(
            "child:" + CHILD, [main_event, child_event], {"id": CHILD}, names,
            roots, True, False, False)
        self.assertEqual(len(child_only), 1)
        self.assertEqual(child_only[0]["skill"], "beta")
        self.assertEqual(child_only[0]["scope"], "child:" + CHILD)
        self.assertEqual(child_mixed, child_only)


if __name__ == "__main__":
    unittest.main()
