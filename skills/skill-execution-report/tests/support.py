"""Fixtures for the hermetic skill-execution-report test suite.

Everything here builds throw-away SQLite databases, skill directories and
usage files under a temporary directory. No test reads the real Hermes state,
touches the network, or depends on the developer's configuration.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

#: Column definitions mirroring the Hermes state.db schema the tool expects.
SESSION_COLUMNS = (
    "id TEXT PRIMARY KEY",
    "source TEXT",
    "title TEXT",
    "started_at REAL",
    "ended_at REAL",
    "message_count INTEGER",
    "tool_call_count INTEGER",
    "parent_session_id TEXT",
    "cwd TEXT",
    "git_repo_root TEXT",
    "git_branch TEXT",
    "hidden INTEGER DEFAULT 0",
    "archived INTEGER DEFAULT 0",
    "estimated_cost_usd REAL",
    "actual_cost_usd REAL",
    "cost_status TEXT",
    "input_tokens INTEGER",
    "output_tokens INTEGER",
)
MESSAGE_COLUMNS = (
    "id INTEGER PRIMARY KEY",
    "session_id TEXT",
    "role TEXT",
    "content TEXT",
    "tool_call_id TEXT",
    "tool_calls TEXT",
    "tool_name TEXT",
    "timestamp REAL",
    "active INTEGER DEFAULT 1",
    "compacted INTEGER DEFAULT 0",
    "message_uid TEXT",
    "display_kind TEXT",
)


def tool_calls(*calls) -> str:
    """Stored ``tool_calls`` JSON for ``(name, args, call_id)`` tuples."""
    payload = []
    for name, args, call_id in calls:
        payload.append({
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    return json.dumps(payload)


def tool_result(**fields) -> str:
    """A ``role=tool`` body as stored by Hermes."""
    return json.dumps(fields)


def assistant(uid: str, ts: float, calls=(), content: str = "", **extra) -> dict:
    row = {
        "role": "assistant",
        "message_uid": uid,
        "timestamp": ts,
        "content": content,
        "tool_calls": tool_calls(*calls),
    }
    row.update(extra)
    return row


def tool(uid: str, ts: float, call_id: str, body: str,
         name: str = "terminal", **extra) -> dict:
    row = {
        "role": "tool",
        "message_uid": uid,
        "timestamp": ts,
        "tool_call_id": call_id,
        "tool_name": name,
        "content": body,
    }
    row.update(extra)
    return row


def user(uid: str, ts: float, content: str, **extra) -> dict:
    row = {"role": "user", "message_uid": uid, "timestamp": ts, "content": content}
    row.update(extra)
    return row


def session(sid: str, **kw) -> dict:
    row = {
        "id": sid,
        "source": "cli",
        "title": "Sessão %s" % sid,
        "started_at": 1000.0,
        "ended_at": None,
        "message_count": 0,
        "tool_call_count": 0,
        "parent_session_id": None,
        "cwd": None,
        "git_repo_root": None,
        "git_branch": None,
        "hidden": 0,
        "archived": 0,
        "estimated_cost_usd": None,
        "actual_cost_usd": None,
        "cost_status": None,
        "input_tokens": None,
        "output_tokens": None,
    }
    row.update(kw)
    return row


class Sandbox:
    """A temporary ``HERMES_HOME`` populated with synthetic sources."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ser-test-", ignore_cleanup_errors=True)
        self.root = pathlib.Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / "skills").mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "state.db"
        self.usage_path = self.home / "skills" / ".usage.json"

    def cleanup(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------- sources
    def skill(self, name: str) -> pathlib.Path:
        target = self.home / "skills" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            "---\nname: %s\n---\n" % name, encoding="utf-8")
        return target

    def usage(self, data) -> pathlib.Path:
        self.usage_path.write_text(json.dumps(data), encoding="utf-8")
        return self.usage_path

    def database(self, sessions=(), messages=(), drop=(), raw=None, tables=None):
        """Create ``state.db``. ``drop`` removes columns; ``raw`` writes bytes."""
        if raw is not None:
            self.db_path.write_bytes(raw)
            return self.db_path
        conn = sqlite3.connect(str(self.db_path))
        try:
            definition = tables or {
                "sessions": SESSION_COLUMNS,
                "messages": MESSAGE_COLUMNS,
            }
            for table, columns in definition.items():
                kept = [c for c in columns if c.split()[0] not in drop]
                conn.execute("CREATE TABLE %s (%s)" % (table, ", ".join(kept)))
            self._insert(conn, "sessions", sessions)
            self._insert(conn, "messages", messages)
            conn.commit()
        finally:
            conn.close()
        return self.db_path

    @staticmethod
    def _insert(conn, table, rows) -> None:
        for row in rows:
            keys = sorted(row)
            conn.execute(
                "INSERT INTO %s (%s) VALUES (%s)" % (
                    table, ", ".join(keys), ", ".join("?" * len(keys))),
                [row[key] for key in keys],
            )

    def dir(self, *parts) -> pathlib.Path:
        target = self.root.joinpath(*parts)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def file(self, relative: str, text: str = "conteúdo") -> pathlib.Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    # ----------------------------------------------------------- execution
    def run(self, argv=(), now: str = "2026-10-13T12:00:00Z"):
        return run_cli(argv, self.home, now=now)


def run_cli(argv, home, now: str = "2026-10-13T12:00:00Z"):
    """Run the CLI in-process against ``home`` and capture its streams."""
    import report

    previous = {key: os.environ.get(key) for key in ("HERMES_HOME", "SER_NOW")}
    os.environ["HERMES_HOME"] = str(home)
    os.environ["SER_NOW"] = now
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main(list(argv))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return code, out.getvalue(), err.getvalue()


def findings_of(payload: dict, scope: str = "main") -> list:
    return [item for item in payload["findings"] if item["scope"] == scope]


def finding_for(payload: dict, name: str, scope: str = "main"):
    for item in payload["findings"]:
        if item["skill"] == name and item["scope"] == scope:
            return item
    return None
