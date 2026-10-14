"""Read-only evidence collection for the skill-execution-report skill.

Everything in this module observes; nothing writes. The database is opened with
``mode=ro`` plus ``PRAGMA query_only``, transcript rows are read in bounded
batches and only the first few kilobytes of each message body are pulled from
SQLite so a long session never lands in memory in full.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from _common import (
    MAX_EXCERPT,
    SourceCorrupt,
    SourceMissing,
    iso,
    open_ro,
    redact,
)

BATCH = 200
CONTENT_CHARS = 4000
MAX_ARTIFACT_CANDIDATES = 6
MAX_COMMIT_CANDIDATES = 3

#: Columns without which the transcript cannot be interpreted. A different
#: Hermes schema is reported as corrupt instead of guessed at.
REQUIRED_COLUMNS = {
    "sessions": {
        "id", "source", "title", "started_at", "ended_at", "message_count",
        "tool_call_count", "parent_session_id", "cwd", "git_repo_root",
        "git_branch", "hidden", "archived", "estimated_cost_usd",
        "actual_cost_usd", "cost_status", "input_tokens", "output_tokens",
    },
    "messages": {
        "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
        "tool_name", "timestamp", "active", "compacted", "message_uid",
        "display_kind",
    },
}

#: Tool-call payloads are small; a wider window keeps every call of a batched
#: assistant turn visible (a truncated list would silently drop calls).
TOOL_CALL_CHARS = 20000

MESSAGE_COLUMNS = (
    "id, message_uid, role, tool_name, tool_call_id, timestamp, active, "
    "compacted, display_kind, "
    "substr(coalesce(content, ''), 1, %d) AS content_head, "
    "substr(coalesce(tool_calls, ''), 1, %d) AS tool_calls_head"
    % (CONTENT_CHARS, TOOL_CALL_CHARS)
)

SKILL_PATH_RE = re.compile(r"skills[/\\]([A-Za-z0-9][A-Za-z0-9._-]{0,63})[/\\]")
#: A produced path inside tool output: at least one separator and an extension.
PATH_RE = re.compile(
    r"(?<![\w./\\-])((?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|/)?[\w.@()+-]+"
    r"(?:[\\/][\w.@()+-]+)+\.[A-Za-z0-9]{1,8})(?![\w])"
)
COMMIT_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
COMMIT_HINT_RE = re.compile(r"(?i)\b(commit|sha|merge|push|revision|revisão)\b")


class SkillIndex:
    """Names of the skills visible to the runtime, from a filesystem scan.

    A directory counts as a skill when it contains a ``SKILL.md``. Category
    folders (``skills/creative/...``) are therefore skipped naturally, and only
    known names are ever used for attribution, which keeps paths that merely
    look like skills (``skills/docs/``) out of the report.
    """

    def __init__(self, names: dict[str, str], roots: list[str]):
        self.names = dict(names)
        self.roots = list(roots)

    #: Category folders (``skills/creative/...``) nest skills one or two levels
    #: below the root, so the scan walks down a bounded number of levels.
    MAX_DEPTH = 3

    @classmethod
    def scan(cls, roots: list[str]) -> "SkillIndex":
        names: dict[str, str] = {}
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            cls._walk(root, root, names, 0)
        return cls(names, roots)

    @classmethod
    def _walk(cls, root: str, path: str, names: dict, depth: int) -> None:
        if depth > cls.MAX_DEPTH:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        for entry in entries:
            if entry.startswith("."):
                continue
            candidate = os.path.join(path, entry)
            if os.path.islink(candidate) or not os.path.isdir(candidate):
                continue
            if os.path.isfile(os.path.join(candidate, "SKILL.md")):
                names.setdefault(entry, root)
                continue  # never look for skills inside a skill
            cls._walk(root, candidate, names, depth + 1)

    def has(self, name: str) -> bool:
        return name in self.names

    def sorted_names(self) -> list[str]:
        return sorted(self.names)


def git_facts(repo: str | None) -> dict:
    """Branch/head of a local repository, without touching the network."""
    if not repo or not os.path.isdir(repo) or not shutil.which("git"):
        return {"available": False, "reason": "repo not provided or git not on PATH"}
    def run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", repo] + args,
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    head = run(["rev-parse", "HEAD"])
    if head is None:
        return {"available": False, "reason": "not a git working tree"}
    return {
        "available": True,
        "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "head": head,
        "dirty": bool(run(["status", "--porcelain"])),
    }


def commit_exists(repo: str, sha: str) -> bool:
    """True when ``sha`` resolves to a commit in ``repo`` (local objects only)."""
    if not repo or not shutil.which("git"):
        return False
    try:
        out = subprocess.run(
            ["git", "-C", repo, "cat-file", "-e", "%s^{commit}" % sha],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


class StateDb:
    """Read-only view over the Hermes ``state.db`` transcript store."""

    def __init__(self, path: str):
        self.path = path
        self.conn = open_ro(path)
        self.schema = self._check_schema()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # pragma: no cover - defensive
            pass

    def _columns(self, table: str) -> set[str]:
        try:
            rows = self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()
        except Exception as exc:
            raise SourceCorrupt("cannot inspect table %s: %s" % (table, exc)) from exc
        return {row[1] for row in rows}

    def _check_schema(self) -> dict:
        report = {"tables": {}, "compatible": True, "problems": []}
        for table, required in REQUIRED_COLUMNS.items():
            present = self._columns(table)
            if not present:
                report["compatible"] = False
                report["problems"].append("missing table: %s" % table)
                report["tables"][table] = {"present": False, "missing": sorted(required)}
                continue
            missing = sorted(required - present)
            report["tables"][table] = {"present": True, "missing": missing}
            if missing:
                report["compatible"] = False
                report["problems"].append(
                    "table %s missing columns: %s" % (table, ", ".join(missing))
                )
        if not report["compatible"]:
            raise SourceCorrupt("; ".join(report["problems"]))
        return report

    # ---------------------------------------------------------------- sessions
    def session(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, source, title, started_at, ended_at, message_count, "
            "tool_call_count, parent_session_id, cwd, git_repo_root, git_branch, "
            "hidden, archived, estimated_cost_usd, actual_cost_usd, cost_status, "
            "input_tokens, output_tokens "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(zip(
            ("id", "source", "title", "started_at", "ended_at", "message_count",
             "tool_call_count", "parent_session_id", "cwd", "git_repo_root",
             "git_branch", "hidden", "archived", "estimated_cost_usd",
             "actual_cost_usd", "cost_status", "input_tokens", "output_tokens"),
            row,
        )) if row else None

    def require_session(self, session_id: str) -> dict:
        found = self.session(session_id)
        if found is None:
            raise SourceMissing("session not found: %s" % session_id)
        return found

    def resolve_session(self, ref: str | None) -> dict:
        """Resolve ``latest``, an exact id or a unique id prefix."""
        if ref in (None, "", "latest"):
            row = self.conn.execute(
                "SELECT id FROM sessions WHERE coalesce(hidden, 0) = 0 "
                "ORDER BY coalesce(started_at, 0) DESC, id DESC LIMIT 1"
            ).fetchone()
            if not row:
                raise SourceMissing("no sessions recorded in state.db")
            return self.require_session(row[0])
        exact = self.session(ref)
        if exact is not None:
            return exact
        rows = self.conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ? ORDER BY id DESC LIMIT 5",
            (ref + "%",),
        ).fetchall()
        if len(rows) == 1:
            return self.require_session(rows[0][0])
        if not rows:
            raise SourceMissing("session not found: %s" % ref)
        raise SourceMissing("session id prefix is ambiguous: %s" % ref)

    def children(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id FROM sessions WHERE parent_session_id = ? "
            "ORDER BY coalesce(started_at, 0) ASC, id ASC",
            (session_id,),
        ).fetchall()
        return [found for found in (self.session(r[0]) for r in rows) if found]

    def session_count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM sessions").fetchone()[0]

    # ---------------------------------------------------------------- messages
    def iter_messages(self, session_id: str, since: float | None = None,
                      until: float | None = None):
        """Yield message rows in insertion order, streaming in bounded batches."""
        sql = "SELECT %s FROM messages WHERE session_id = ?" % MESSAGE_COLUMNS
        params: list = [session_id]
        if since is not None:
            sql += " AND coalesce(timestamp, 0) >= ?"
            params.append(since)
        if until is not None:
            sql += " AND coalesce(timestamp, 0) <= ?"
            params.append(until)
        sql += " ORDER BY id ASC"
        cursor = self.conn.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        seen: set[str] = set()
        while True:
            batch = cursor.fetchmany(BATCH)
            if not batch:
                return
            for row in batch:
                record = dict(zip(columns, row))
                uid = record.get("message_uid") or "row:%s" % record.get("id")
                # Hermes stores one logical message once per display variant
                # (original, rendered copy, rewind snapshot). Keeping the first
                # physical row per uid counts every call once and keeps each
                # result next to its call, so nothing is double counted.
                if uid in seen:
                    continue
                seen.add(uid)
                yield record

    def message_span(self, session_id: str) -> dict:
        row = self.conn.execute(
            "SELECT count(*), min(timestamp), max(timestamp) FROM messages "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return {"messages": row[0] or 0, "first_at": row[1], "last_at": row[2]}


def parse_tool_calls(raw: str) -> list[dict]:
    """Normalise the stored ``tool_calls`` JSON into ``{name, args, call_id}``."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if isinstance(payload, dict):
        payload = payload.get("tool_calls") or []
    if not isinstance(payload, list):
        return []
    calls = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"_raw": arguments}
        calls.append({
            "name": name,
            "args": arguments if isinstance(arguments, dict) else {"_raw": arguments},
            "call_id": item.get("call_id") or item.get("id") or function.get("call_id"),
        })
    return calls


SUCCESS_RE = re.compile(r'"success"\s*:\s*(true|false)')
ERROR_FIELD_RE = re.compile(r'"(error|exception|traceback)"\s*:')


def parse_tool_result(raw: str) -> dict:
    """Normalise a ``role=tool`` body into a small dict of observable signals."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return _salvage_truncated(raw)
    return payload if isinstance(payload, dict) else {"_raw": payload}


def _salvage_truncated(raw: str) -> dict:
    """Recover the outcome flags of a JSON body cut off by the read window.

    A large tool result exceeds ``CONTENT_CHARS``, so the body is not valid
    JSON any more. The outcome lives in the first bytes, which is all the
    report needs; anything further is deliberately not guessed at.
    """
    out: dict = {"_raw": raw, "_truncated": True}
    match = SUCCESS_RE.search(raw[:512])
    if match:
        out["success"] = match.group(1) == "true"
    if ERROR_FIELD_RE.search(raw[:2048]):
        out["error"] = "resultado truncado; campo de erro presente"
    return out


def strip_tool_call_json(text: str) -> str:
    """Drop tool-call payloads so their ``name`` arguments are not read as prose."""
    start = text.find("<<<TOOL_CALLS>>>")
    if start == -1:
        return text
    return text[:start]


def skills_in_text(text: str, index: SkillIndex) -> list[str]:
    """Known skill names referenced through a ``skills/<name>/`` path."""
    if not text:
        return []
    found = []
    for match in SKILL_PATH_RE.finditer(text):
        name = match.group(1)
        if index.has(name) and name not in found:
            found.append(name)
    return found


def artifact_paths(text: str, roots: list[str]) -> list[str]:
    """Existing files referenced in tool output, resolved under ``roots``.

    Only paths that exist right now and sit inside an allowed root are
    returned, so the report never claims an artifact it cannot point at.
    """
    if not text:
        return []
    bases = [r for r in roots if r and os.path.isdir(r)]
    seen: list[str] = []
    for match in PATH_RE.finditer(text):
        raw = match.group(1)
        if raw.startswith("<") or "..." in raw:
            continue
        candidates = [raw] if os.path.isabs(raw) else [os.path.join(b, raw) for b in bases]
        for candidate in candidates:
            normalised = os.path.normpath(candidate)
            if not any(
                os.path.commonpath([os.path.normpath(b), normalised]) == os.path.normpath(b)
                for b in bases
            ):
                continue
            if os.path.isfile(normalised) and normalised not in seen:
                seen.append(normalised)
        if len(seen) >= MAX_ARTIFACT_CANDIDATES:
            break
    return seen[:MAX_ARTIFACT_CANDIDATES]


def commit_candidates(text: str) -> list[str]:
    """Short/ long hex tokens in output that sit near a commit-ish word."""
    if not text or not COMMIT_HINT_RE.search(text):
        return []
    out: list[str] = []
    for match in COMMIT_RE.finditer(text):
        sha = match.group(1)
        if sha not in out:
            out.append(sha)
        if len(out) >= MAX_COMMIT_CANDIDATES:
            break
    return out


def collect_session_events(db: StateDb, session: dict, index: SkillIndex,
                           since: float | None, until: float | None,
                           scope: str, repo: str | None,
                           verify_artifacts: bool) -> tuple[list[dict[str, object]], dict]:
    """Walk one session transcript and return (events, facts).

    An event is a single, sanitised observation. Attribution rules:

    * ``skill_view`` / ``skill_manage`` are attributed by their ``name`` argument.
    * other tools are attributed only when their arguments reference
      ``skills/<known-name>/``, which is how a skill's own script gets executed.
    * results are matched to calls by ``tool_call_id``; a result without its call
      is recorded as unattributed rather than guessed at.
    """
    session_id = session["id"]
    events: list[dict[str, object]] = []
    facts = {
        "session_id": session_id,
        "scope": scope,
        "messages": 0,
        "active_messages": 0,
        "compacted_messages": 0,
        "unattributed_tool_results": 0,
        "skill_events_without_call": 0,
        "span": None,
    }
    roots = [session.get("cwd"), session.get("git_repo_root"), repo]
    roots = [r for r in roots if r]
    pending: dict[str, dict] = {}
    #: Every call id seen in the window, whether or not it is skill related.
    #: Used to tell "result whose call is missing" from "result of an
    #: unrelated tool", which must not inflate the unknown-evidence count.
    known_calls: set[str] = set()

    for row in db.iter_messages(session_id, since, until):
        facts["messages"] += 1
        if row.get("compacted"):
            facts["compacted_messages"] += 1
        if row.get("active"):
            facts["active_messages"] += 1
        role = row.get("role")
        ts = row.get("timestamp")
        if role == "assistant":
            for call in parse_tool_calls(row.get("tool_calls_head") or ""):
                call_id = str(call.get("call_id"))
                known_calls.add(call_id)
                evidence = attribute_call(call, row, index, scope, ts)
                if evidence is None:
                    continue
                pending[call_id] = evidence
                events.append(evidence)
        elif role == "tool":
            result = parse_tool_result(row.get("content_head") or "")
            call_id = str(row.get("tool_call_id"))
            evidence = pending.pop(call_id, None)
            if evidence is None:
                evidence = attribute_result(row, result, index, scope, ts)
                if evidence is None:
                    if call_id and call_id not in known_calls:
                        facts["unattributed_tool_results"] += 1
                    continue
                # A skill-related result whose call is not in the window: the
                # load happened outside the transcript, so the call itself is
                # reported as unverifiable instead of being invented.
                if call_id not in known_calls:
                    facts["skill_events_without_call"] += 1
                events.append(evidence)
            apply_result(evidence, result, row, roots, repo, verify_artifacts)
        elif role in ("user", "assistant"):
            # Only conversational turns can "mention" a skill. System/context
            # rows carry the whole skill index and would mark every skill.
            text = row.get("content_head") or ""
            if role == "assistant":
                text = strip_tool_call_json(text)
            for name in skills_in_text(text, index):
                events.append({
                    "kind": "mention",
                    "skill": name,
                    "at": iso(ts),
                    "ts": ts,
                    "scope": scope,
                    "session_id": session_id,
                    "message_uid": row.get("message_uid"),
                    "source": "state_db",
                    "role": role,
                    "detail": "skills/%s/" % name,
                })

    facts["span"] = db.message_span(session_id)
    return events, facts


def attribute_call(call: dict, row: dict, index: SkillIndex, scope: str,
                   ts: float | None) -> dict[str, object] | None:
    """Turn one tool call into an observation, or ``None`` when unrelated."""
    name = call["name"]
    args = call["args"]
    base: dict[str, object] = {
        "at": iso(ts),
        "ts": ts,
        "scope": scope,
        "session_id": row.get("session_id"),
        "message_uid": row.get("message_uid"),
        "source": "state_db",
        "tool": name,
        "call_id": call.get("call_id"),
    }
    if name == "skill_view":
        target = args.get("name")
        if not isinstance(target, str) or not target:
            return None
        return dict(base, kind="skill_view", skill=target, detail="skill_view(%s)" % target)
    if name == "skill_manage":
        operations = args.get("operations")
        targets: list[str] = []
        if isinstance(operations, list):
            for op in operations:
                if isinstance(op, dict) and isinstance(op.get("name"), str):
                    targets.append(op["name"])
        if isinstance(args.get("name"), str):
            targets.append(args["name"])
        targets = [t for t in dict.fromkeys(targets) if t]
        if not targets:
            return None
        event: dict[str, object] = dict(
            base,
            kind="skill_manage",
            skill=targets[0],
            detail="skill_manage(%s)" % ", ".join(targets),
        )
        if len(targets) > 1:
            event["related_skills"] = targets[1:]
        return event
    matched = skills_in_text(json.dumps(args, ensure_ascii=False), index)
    if not matched:
        return None
    event: dict[str, object] = dict(
        base,
        kind="skill_script",
        skill=matched[0],
        detail=matched_path(json.dumps(args, ensure_ascii=False), matched[0]),
    )
    if len(matched) > 1:
        event["related_skills"] = matched[1:]
    return event


def matched_path(raw: str, name: str) -> str:
    """The ``skills/<name>/...`` fragment that justified attribution."""
    match = SKILL_PATH_RE.search(raw or "")
    while match:
        if match.group(1) == name:
            tail = raw[match.start(): match.start() + 80]
            return redact(tail, 80)
        match = SKILL_PATH_RE.search(raw, match.end())
    return "skills/%s/" % name


def attribute_result(row: dict, result: dict, index: SkillIndex, scope: str,
                     ts: float | None) -> dict[str, object] | None:
    """Attribution for a tool result whose originating call is out of window."""
    if row.get("tool_name") != "skill_view":
        return None
    target = result.get("name")
    if not isinstance(target, str) or not target:
        return None
    return {
        "kind": "skill_view",
        "skill": target,
        "at": iso(ts),
        "ts": ts,
        "scope": scope,
        "session_id": row.get("session_id"),
        "message_uid": row.get("message_uid"),
        "source": "state_db",
        "tool": "skill_view",
        "call_id": row.get("tool_call_id"),
        "detail": "skill_view(%s)" % target,
        "orphan_result": True,
    }


def apply_result(evidence: dict[str, object], result: dict, row: dict, roots: list[str],
                 repo: str | None, verify_artifacts: bool) -> None:
    """Attach the observable outcome of a tool result to its observation."""
    kind = evidence["kind"]
    if result.get("_truncated"):
        evidence["result_truncated"] = True
    if kind in ("skill_view", "skill_manage"):
        success = result.get("success")
        if isinstance(success, bool):
            evidence["ok"] = success
            evidence["basis"] = "success_flag"
        elif row.get("display_kind") == "failed_turn":
            evidence["ok"] = False
            evidence["basis"] = "failed_turn"
        if result.get("error"):
            evidence["ok"] = False
            evidence["basis"] = evidence.get("basis") or "error_field"
            evidence["error"] = redact(str(result.get("error")), 120)
        excerpt = result.get("description") or result.get("message")
        if not excerpt and kind == "skill_manage":
            excerpt = "operações aplicadas" if evidence.get("ok") else None
        if excerpt:
            evidence["excerpt"] = redact(str(excerpt))
        return
    if kind != "skill_script":
        return
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int):
        evidence["ok"] = exit_code == 0
        evidence["basis"] = "exit_code"
        evidence["exit_code"] = exit_code
    elif row.get("display_kind") == "failed_turn":
        evidence["ok"] = False
        evidence["basis"] = "failed_turn"
    if result.get("error"):
        evidence["error"] = redact(str(result.get("error")), 120)
    output = result.get("output")
    if output is None and result.get("_raw"):
        output = result.get("_raw")
    if isinstance(output, str):
        evidence["excerpt"] = redact(output, MAX_EXCERPT)
        if verify_artifacts:
            files = artifact_paths(output, roots)
            if files:
                evidence["artifact_files"] = files
            commits = []
            if repo:
                for sha in commit_candidates(output):
                    if commit_exists(repo, sha):
                        commits.append(sha)
            if commits:
                evidence["artifact_commits"] = commits
