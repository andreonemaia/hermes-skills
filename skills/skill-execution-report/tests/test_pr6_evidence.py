"""Regression tests for the evidence layer (PR #6 findings).

Hermetic: every test builds its own throw-away ``state.db`` under a temporary
directory. Nothing here reads the real Hermes state, the user's configuration,
the network, or any external service. ``support.py`` is deliberately not used
so these tests keep running while the CLI-level suite is being reworked.

Each test names the finding it guards, so a later refactor can tell which
behaviour is load-bearing.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import evidence as E  # noqa: E402  (path is set above on purpose)
from evidence import SourceCorrupt  # noqa: E402


# --------------------------------------------------------------------- fixture

#: Columns the fixture always writes, so a test can drop a *different* one.
SESSION_FIXTURE = (
    "id, source, title, started_at, ended_at, message_count, tool_call_count, "
    "parent_session_id, cwd, git_repo_root, git_branch, hidden, archived, "
    "estimated_cost_usd, actual_cost_usd, cost_status, input_tokens, output_tokens"
)
MESSAGE_FIXTURE = (
    "id, session_id, role, content, tool_calls, tool_name, tool_call_id, "
    "timestamp, active, compacted, message_uid, display_kind"
)


def create_schema(conn: sqlite3.Connection, drop: tuple = (),
                  timestamp_type: str = "REAL") -> None:
    """Create the two required tables, minus any ``drop``ped column."""
    # Real SQLite affinity matters: a TEXT ``hidden`` would never satisfy the
    # ``hidden = 0`` filter the reader uses.
    numeric = {
        ("sessions", "started_at"), ("sessions", "ended_at"),
        ("sessions", "message_count"), ("sessions", "tool_call_count"),
        ("sessions", "hidden"), ("sessions", "archived"),
        ("sessions", "estimated_cost_usd"), ("sessions", "actual_cost_usd"),
        ("sessions", "input_tokens"), ("sessions", "output_tokens"),
        ("messages", "timestamp"), ("messages", "active"),
        ("messages", "compacted"),
    }
    for table, required in E.REQUIRED_COLUMNS.items():
        definitions = []
        for column in sorted(required - set(drop)):
            kind = timestamp_type if (table, column) in numeric else "TEXT"
            definitions.append("%s %s" % (column, kind))
        conn.execute("CREATE TABLE %s (%s)" % (table, ", ".join(definitions)))


def add_session(conn: sqlite3.Connection, session_id: str, started_at: float = 1.0,
                hidden: int = 0, cwd: str | None = None,
                git_repo_root: str | None = None,
                parent_session_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO sessions (%s) VALUES (%s)" % (SESSION_FIXTURE,
                                                   ", ".join("?" * 18)),
        (session_id, "cli", session_id, started_at, None, 0, 0,
         parent_session_id, cwd, git_repo_root, None, hidden, 0,
         0.0, 0.0, "none", 0, 0),
    )


def add_message(conn: sqlite3.Connection, row_id: str, session_id: str,
                role: str, content: str | None = None,
                tool_calls: str | None = None, tool_call_id: str | None = None,
                timestamp: float = 1.0, message_uid: str | None = None,
                display_kind: str | None = None,
                tool_name: str | None = None) -> None:
    conn.execute(
        "INSERT INTO messages (%s) VALUES (%s)" % (MESSAGE_FIXTURE,
                                                   ", ".join("?" * 12)),
        (row_id, session_id, role, content, tool_calls, tool_name, tool_call_id,
         timestamp, 1, 0, message_uid or row_id, display_kind),
    )


def calls_payload(*calls: dict) -> str:
    """Render a stored ``tool_calls`` blob in the shape the reader parses."""
    return json.dumps(list(calls))


class FixtureCase(unittest.TestCase):
    """A sandbox holding one ``state.db`` and one skill root."""

    def setUp(self) -> None:
        # realpath keeps the MSYS-path assertions stable: normpath on a
        # symlinked temp dir would not match the resolved root.
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="ser-pr6-"))
        self.db_path = os.path.join(self.dir, "state.db")
        self.skills = os.path.join(self.dir, "skills")
        os.makedirs(self.skills)
        self.addCleanup(shutil.rmtree, self.dir, True)

    # -- builders ---------------------------------------------------------

    def build(self, *, drop: tuple = (), timestamp_type: str = "REAL") -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        create_schema(conn, drop=drop, timestamp_type=timestamp_type)
        return conn

    def open_db(self) -> E.StateDb:
        return E.StateDb(self.db_path)

    def make_skill(self, name: str, root: str | None = None) -> str:
        path = os.path.join(root or self.skills, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write("---\nname: %s\n---\n" % name)
        return path

    def index(self, *names: str) -> E.SkillIndex:
        for name in names:
            self.make_skill(name)
        return E.SkillIndex.scan([self.skills])

    def collect(self, index: E.SkillIndex, session_id: str = "s1"):
        db = self.open_db()
        try:
            session = db.require_session(session_id)
            return E.collect_session_events(db, session, index, None, None,
                                            "session", None, False)
        finally:
            db.close()


# ---------------------------------------------------------------- state.db I/O

class TestSchema(FixtureCase):
    """The read layer refuses what it cannot interpret, loudly."""

    def test_like_prefix_escapes_wildcards(self) -> None:
        """Finding #32: a session id with ``_``/``%`` must be matched literally."""
        self.assertEqual(E._like_prefix("a_b%c\\d"), "a\\_b\\%c\\\\d%")

    def test_prefix_resolution_is_literal(self) -> None:
        """Finding #32: ``a_b`` must not resolve ``aXbYc``."""
        conn = self.build()
        add_session(conn, "a_b_c", started_at=1.0)
        add_session(conn, "aXbYc", started_at=2.0)
        conn.commit()
        conn.close()
        db = self.open_db()
        try:
            self.assertEqual(db.resolve_session("a_b")["id"], "a_b_c")
            # The unescaped form would match both and be reported ambiguous.
            self.assertEqual(
                db.conn.execute("SELECT count(*) FROM sessions WHERE id LIKE 'a_b%'")
                .fetchone()[0], 2)
        finally:
            db.close()

    def test_text_timestamps_are_reported_as_corrupt(self) -> None:
        """Finding #64: TEXT timestamps would silently distort the window."""
        conn = self.build(timestamp_type="TEXT")
        add_session(conn, "s1")
        add_message(conn, "m1", "s1", "user", content="oi")
        conn.commit()
        conn.close()
        with self.assertRaises(SourceCorrupt) as caught:
            self.open_db()
        self.assertIn("non-numeric", str(caught.exception))

    def test_missing_column_is_reported_not_raised(self) -> None:
        """Finding #64: a missing column is a named problem, never a crash."""
        conn = self.build(drop=("started_at",))
        conn.execute("INSERT INTO sessions (id) VALUES ('s1')")
        conn.commit()
        conn.close()
        with self.assertRaises(SourceCorrupt) as caught:
            self.open_db()
        self.assertIn("missing columns", str(caught.exception))
        self.assertIn("started_at", str(caught.exception))

    def test_corrupt_schema_closes_the_connection(self) -> None:
        """Finding #31: a rejected schema must not leak the read-only handle."""

        class Probe(E.StateDb):
            instance = None

            def __init__(self, path: str) -> None:
                Probe.instance = self
                super().__init__(path)

        conn = self.build(timestamp_type="TEXT")
        add_session(conn, "s1")
        conn.commit()
        conn.close()
        with self.assertRaises(SourceCorrupt):
            Probe(self.db_path)
        self.assertIsNotNone(Probe.instance)
        with self.assertRaises(sqlite3.ProgrammingError):
            Probe.instance.conn.execute("SELECT 1")

    def test_session_columns_are_labelled_from_the_cursor(self) -> None:
        """Finding #63: the SELECT and the row labels cannot drift apart."""
        conn = self.build()
        add_session(conn, "s1", started_at=7.0, hidden=1, cwd=self.dir)
        conn.commit()
        conn.close()
        db = self.open_db()
        try:
            session = db.require_session("s1")
            self.assertEqual(sorted(session), list(E.SESSION_COLUMNS))
            self.assertEqual(session["hidden"], 1)
            self.assertEqual(session["cwd"], self.dir)
            self.assertEqual(session["started_at"], 7.0)
        finally:
            db.close()

    def test_latest_skips_hidden_but_explicit_id_resolves_it(self) -> None:
        """Finding #34: hidden sessions are only reached by an explicit name."""
        conn = self.build()
        add_session(conn, "visible", started_at=1.0)
        add_session(conn, "hidden-one", started_at=99.0, hidden=1)
        conn.commit()
        conn.close()
        db = self.open_db()
        try:
            self.assertEqual(db.resolve_session("latest")["id"], "visible")
            self.assertEqual(db.resolve_session("hidden-one")["id"], "hidden-one")
        finally:
            db.close()


# ------------------------------------------------------------------ skill index

class TestSkillIndex(FixtureCase):
    """Ambiguous skill names are reportable instead of silently wrong."""

    def test_conflicting_skill_names_are_recorded(self) -> None:
        """Finding #65: two roots holding one name must be visible."""
        other = os.path.join(self.dir, "extra-skills")
        os.makedirs(other)
        self.make_skill("dup")
        self.make_skill("uniq", other)
        self.make_skill("dup", other)
        index = E.SkillIndex.scan([self.skills, other])
        self.assertEqual(index.conflicts, {"dup": [self.skills, other]})
        self.assertNotIn("uniq", index.conflicts)

    def test_first_root_wins_on_conflict(self) -> None:
        """Finding #65: the documented tie-break is the first root."""
        other = os.path.join(self.dir, "extra-skills")
        os.makedirs(other)
        self.make_skill("dup")
        self.make_skill("dup", other)
        index = E.SkillIndex.scan([self.skills, other])
        self.assertEqual(index.names["dup"], self.skills)


# -------------------------------------------------------------- result parsing

def make_event(kind: str = "skill_view", **extra) -> dict:
    event = {
        "kind": kind, "skill": "alpha", "at": "1970-01-01T00:00:01Z", "ts": 1.0,
        "scope": "session", "session_id": "s1", "message_uid": "m1",
        "source": "state_db", "tool": kind, "call_id": "c1", "detail": kind,
    }
    event.update(extra)
    return event


class TestResultOutcome(FixtureCase):
    """Failure precedence: an explicit error outranks every other signal."""

    def row(self, **extra) -> dict:
        base = {"content_chars": 10, "display_kind": None}
        base.update(extra)
        return base

    def apply(self, event: dict, result: dict, **row) -> dict:
        E.apply_result(event, result, self.row(**row), [self.dir], None, False)
        return event

    def test_error_null_is_not_an_error(self) -> None:
        """Finding #36: ``"error": null`` must not be read as a failure."""
        event = self.apply(make_event(), {"success": True, "error": None})
        self.assertNotIn("error", event)
        self.assertIs(event["ok"], True)
        self.assertEqual(event["basis"], "success_flag")

    def test_error_field_outranks_success_flag(self) -> None:
        """Findings #11/#37: success plus an error field is a failure."""
        event = self.apply(make_event(), {"success": True, "error": "boom"})
        self.assertIs(event["ok"], False)
        self.assertEqual(event["basis"], "error_field")
        self.assertEqual(event["error"], "boom")

    def test_error_field_outranks_exit_code_zero(self) -> None:
        """Finding #12: a zero exit code does not survive an error field."""
        event = self.apply(make_event("skill_script"), {"exit_code": 0, "error": "nope"})
        self.assertIs(event["ok"], False)
        self.assertEqual(event["basis"], "error_field")

    def test_exit_code_bool_is_not_an_exit_code(self) -> None:
        """A JSON ``true`` is not an exit status, so it sets no verdict."""
        event = self.apply(make_event("skill_script"), {"exit_code": True})
        self.assertNotIn("ok", event)
        self.assertNotIn("basis", event)

    def test_failed_turn_basis_without_success_flag(self) -> None:
        """Finding #37: the transcript's own failure mark is the fallback."""
        event = self.apply(make_event(), {"description": "x"}, display_kind="failed_turn")
        self.assertIs(event["ok"], False)
        self.assertEqual(event["basis"], "failed_turn")

    def test_error_is_clipped(self) -> None:
        """Finding #68: a quoted error is bounded like any other excerpt."""
        event = self.apply(make_event(), {"success": False, "error": "e" * 500})
        self.assertEqual(len(event["error"]), E.MAX_ERROR)

    def test_synthetic_excerpt_is_flagged(self) -> None:
        """Finding #38: stand-in prose is marked so it is never read as a quote."""
        event = self.apply(make_event("skill_manage"), {"success": True})
        self.assertEqual(event["excerpt"], E.SYNTHETIC_EXCERPT)
        self.assertIs(event["excerpt_synthetic"], True)

    def test_no_synthetic_excerpt_on_failure(self) -> None:
        """Finding #38: a failed manage call has no stand-in prose."""
        event = self.apply(make_event("skill_manage"), {"success": False})
        self.assertNotIn("excerpt", event)

    def test_truncated_result_keeps_name_and_success(self) -> None:
        """Findings #12/#67: the outcome survives a body cut by the read window.

        The reader only ever sees ``substr(content, 1, CONTENT_CHARS)``, so a
        large result reaches ``parse_tool_result`` already invalid as JSON.
        """
        body = json.dumps({"name": "alpha", "success": True,
                           "description": "x" * 6000})
        cut = body[: E.CONTENT_CHARS]
        self.assertRaises(ValueError, json.loads, cut)
        result = E.parse_tool_result(cut)
        self.assertIs(result["_truncated"], True)
        self.assertEqual(result["name"], "alpha")
        self.assertIs(result["success"], True)
        event = self.apply(make_event(), result)
        self.assertIs(event["result_truncated"], True)
        self.assertIs(event["ok"], True)

    def test_truncated_result_error_null_is_not_an_error(self) -> None:
        """Finding #36 on the salvage path: a null error field stays silent."""
        body = ('{"name": "alpha", "success": true, "error": null, "pad": "%s"}'
                % ("z" * 6000))
        result = E.parse_tool_result(body[: E.CONTENT_CHARS])
        self.assertIs(result["_truncated"], True)
        self.assertNotIn("error", result)
        event = self.apply(make_event(), result)
        self.assertIs(event["ok"], True)

    def test_truncated_result_with_error_field_fails(self) -> None:
        """Finding #67: a real error field in a cut-off body still fails."""
        body = ('{"name": "alpha", "success": false, "error": "boom", "pad": "%s"}'
                % ("z" * 6000))
        result = E.parse_tool_result(body[: E.CONTENT_CHARS])
        self.assertIs(result["_truncated"], True)
        self.assertIn("error", result)
        event = self.apply(make_event(), result)
        self.assertIs(event["ok"], False)
        self.assertEqual(event["basis"], "error_field")


# ----------------------------------------------------------------------- paths

class TestPaths(FixtureCase):
    """Artifacts are only claimed when they exist inside an allowed root."""

    def test_native_path_translates_msys_drive(self) -> None:
        """Finding #35: a git-bash ``/c/...`` path is not a relative path."""
        self.assertEqual(E.native_path("/c/Users/x/y.txt"), "C:\\Users\\x\\y.txt")
        self.assertEqual(E.native_path("C:/Users/x/y.txt"), "C:/Users/x/y.txt")

    def test_artifact_paths_finds_msys_path(self) -> None:
        """Finding #35: an artifact printed as ``/c/...`` is still found."""
        target = os.path.join(self.dir, "sub", "out.txt")
        os.makedirs(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        msys = "/c" + target[2:].replace("\\", "/")
        self.assertEqual(E.artifact_paths("wrote %s" % msys, [self.dir]),
                         [os.path.normpath(target)])

    def test_artifact_paths_on_another_drive_is_skipped(self) -> None:
        """Finding #2: a candidate on another drive is dropped, not fatal."""
        self.assertEqual(E.artifact_paths("see D:/nope/x.txt", [self.dir]), [])

    def test_artifact_paths_outside_the_root_is_skipped(self) -> None:
        """Finding #2: an existing file outside every root is not an artifact."""
        outside = os.path.join(self.dir, "outside")
        os.makedirs(outside)
        target = os.path.join(outside, "x.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        self.assertEqual(E.artifact_paths("wrote " + target, [self.skills]), [])


# ------------------------------------------------------------------ transcript

class TestTranscript(FixtureCase):
    """Attribution over a real (fixture) transcript window."""

    def test_assistant_prose_mention_is_recorded(self) -> None:
        """Finding #1: an assistant turn that names a skill in prose counts."""
        calls = calls_payload({"name": "skill_view", "call_id": "c1",
                               "arguments": {"name": "beta"}})
        conn = self.build()
        add_session(conn, "s1", cwd=self.dir)
        add_message(conn, "m1", "s1", "assistant",
                    content="Vou usar skills/alpha/ agora.<<<TOOL_CALLS>>>" + calls,
                    tool_calls=calls)
        add_message(conn, "m2", "s1", "tool",
                    content=json.dumps({"name": "beta", "success": True}),
                    tool_call_id="c1", tool_name="skill_view")
        conn.commit()
        conn.close()
        events, _facts = self.collect(self.index("alpha", "beta"))
        mentions = [e for e in events if e["kind"] == "mention"]
        self.assertEqual([m["skill"] for m in mentions], ["alpha"])
        self.assertEqual(mentions[0]["role"], "assistant")
        # The call payload is not prose: beta is a call, never a mention.
        self.assertEqual([e["skill"] for e in events if e["kind"] == "skill_view"],
                         ["beta"])

    def test_system_row_mention_is_not_recorded(self) -> None:
        """Finding #1: the skill index lives in context rows and must not count."""
        conn = self.build()
        add_session(conn, "s1")
        add_message(conn, "m1", "s1", "system", content="veja skills/alpha/")
        add_message(conn, "m2", "s1", "user", content="veja skills/alpha/")
        conn.commit()
        conn.close()
        events, _facts = self.collect(self.index("alpha"))
        self.assertEqual([e["role"] for e in events], ["user"])

    def test_duplicate_uid_is_counted_once(self) -> None:
        """Finding #10: one logical message stored twice is one observation."""
        conn = self.build()
        add_session(conn, "s1")
        add_message(conn, "m1", "s1", "user", content="veja skills/alpha/",
                    message_uid="u1")
        add_message(conn, "m2", "s1", "user", content="veja skills/alpha/",
                    message_uid="u1")
        conn.commit()
        conn.close()
        events, facts = self.collect(self.index("alpha"))
        self.assertEqual(len([e for e in events if e["kind"] == "mention"]), 1)
        self.assertEqual(facts["messages"], 1)

    def test_call_without_id_is_not_keyed_as_none(self) -> None:
        """Finding #10: two id-less calls must not swap their results."""
        calls = calls_payload({"name": "skill_view", "arguments": {"name": "alpha"}},
                              {"name": "skill_view", "arguments": {"name": "beta"}})
        conn = self.build()
        add_session(conn, "s1")
        add_message(conn, "m1", "s1", "assistant", content="", tool_calls=calls)
        add_message(conn, "m2", "s1", "tool",
                    content=json.dumps({"name": "alpha", "success": False,
                                        "error": "boom"}),
                    tool_name="skill_view")
        add_message(conn, "m3", "s1", "tool",
                    content=json.dumps({"name": "beta", "success": True}),
                    tool_call_id="ghost", tool_name="skill_view")
        conn.commit()
        conn.close()
        events, facts = self.collect(self.index("alpha", "beta"))
        calls = [e for e in events if e["kind"] == "skill_view"
                 and e["message_uid"] == "m1"]
        self.assertEqual(sorted(e["skill"] for e in calls), ["alpha", "beta"])
        # No result may attach to an id-less call, so those observations keep
        # no verdict at all -- the literal "None" key would have made the two
        # calls collide and take each other's result.
        for event in calls:
            self.assertNotIn("ok", event)
            self.assertNotIn("basis", event)
        for event in events:
            self.assertNotEqual(event.get("call_id"), "None")
        self.assertEqual(facts["skill_events_without_call"], 2)
        self.assertEqual(facts["unattributed_tool_results"], 0)

    def test_orphan_result_is_reported_as_limitation(self) -> None:
        """Finding #3: a result whose call is absent is marked unverifiable."""
        conn = self.build()
        add_session(conn, "s1")
        add_message(conn, "m1", "s1", "assistant", content="ok")
        add_message(conn, "m2", "s1", "tool",
                    content=json.dumps({"name": "alpha", "success": True}),
                    tool_call_id="ghost", tool_name="skill_view")
        conn.commit()
        conn.close()
        events, facts = self.collect(self.index("alpha"))
        loads = [e for e in events if e["kind"] == "skill_view"]
        self.assertEqual(len(loads), 1)
        self.assertIs(loads[0]["orphan_result"], True)
        self.assertIn("out_of_transcript_load", loads[0]["limitations"])
        self.assertEqual(facts["skill_events_without_call"], 1)

    def test_truncated_message_counters_and_flag(self) -> None:
        """Findings #9/#52: the unbounded lengths travel with the heads."""
        self.assertIn("content_chars", E.MESSAGE_COLUMNS)
        self.assertIn("tool_calls_chars", E.MESSAGE_COLUMNS)
        # Padded past the call window; the JSON stays valid, only the window cuts.
        padded = calls_payload({"name": "skill_view", "call_id": "c1",
                                "arguments": {"name": "alpha"}})
        padded += " " * (E.TOOL_CALL_CHARS + 100)
        body = json.dumps({"name": "alpha", "success": True,
                           "description": "y" * (E.CONTENT_CHARS + 100)})
        conn = self.build()
        add_session(conn, "s1")
        add_message(conn, "m1", "s1", "assistant", content="", tool_calls=padded)
        add_message(conn, "m2", "s1", "tool", content=body, tool_call_id="c1",
                    tool_name="skill_view")
        conn.commit()
        conn.close()
        events, facts = self.collect(self.index("alpha"))
        self.assertEqual(facts["truncated_tool_call_messages"], 1)
        self.assertEqual(facts["truncated_messages"], 1)
        self.assertEqual(facts["truncated_events"], 1)
        loads = [e for e in events if e["kind"] == "skill_view"]
        self.assertEqual(len(loads), 1)
        self.assertIs(loads[0]["truncated"], True)
        self.assertIs(loads[0]["ok"], True)
        # The body was cut by the read window, so its outcome was salvaged from
        # the surviving bytes rather than parsed.
        self.assertIs(loads[0]["result_truncated"], True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
