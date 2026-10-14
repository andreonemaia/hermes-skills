"""Read-only evidence collection for the skill-execution-report skill.

Everything in this module observes; nothing writes. The database is opened with
``mode=ro`` plus ``PRAGMA query_only``, transcript rows are read in bounded
batches and only the first few kilobytes of each message body are pulled from
SQLite, so no message body is ever held in memory in full. What does grow with
the session is one short uid string per message, kept to drop the duplicate
display variants Hermes stores for the same logical message.
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
#: Error strings are quoted in the report, so they are clipped like excerpts.
MAX_ERROR = 120
MAX_ARTIFACT_CANDIDATES = 6
MAX_COMMIT_CANDIDATES = 3
#: Stand-in text for a successful ``skill_manage`` whose result carries no
#: prose of its own. It is not transcript content and is flagged as such.
SYNTHETIC_EXCERPT = "operações aplicadas"

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

#: Session columns the report reads. The SELECT and the row labels are both
#: built from this, so they cannot drift apart and mislabel a field.
SESSION_COLUMNS = tuple(sorted(REQUIRED_COLUMNS["sessions"]))

#: The unbounded lengths travel with the bounded heads so that a payload cut
#: by the read window can be reported instead of silently losing evidence.
MESSAGE_COLUMNS = (
    "id, message_uid, role, tool_name, tool_call_id, timestamp, active, "
    "compacted, display_kind, "
    "substr(coalesce(content, ''), 1, %d) AS content_head, "
    "substr(coalesce(tool_calls, ''), 1, %d) AS tool_calls_head, "
    "length(coalesce(content, '')) AS content_chars, "
    "length(coalesce(tool_calls, '')) AS tool_calls_chars"
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

    When two roots hold a skill of the same name the first root wins. The name
    is listed in ``conflicts`` so a caller can warn about the ambiguity instead
    of silently attributing an event to the wrong copy.
    """

    def __init__(self, names: dict[str, str], roots: list[str]):
        self.names = dict(names)
        self.roots = list(roots)
        #: name -> every root that carries it, for names carried by more than one.
        self.conflicts: dict[str, list[str]] = {}

    #: Category folders (``skills/creative/...``) nest skills one or two levels
    #: below the root, so the scan walks down a bounded number of levels.
    MAX_DEPTH = 3

    @classmethod
    def scan(cls, roots: list[str]) -> "SkillIndex":
        names: dict[str, str] = {}
        sources: dict[str, list[str]] = {}
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            cls._walk(root, root, names, sources, 0)
        index = cls(names, roots)
        index.conflicts = {name: roots_ for name, roots_ in sources.items() if len(roots_) > 1}
        return index

    @classmethod
    def _walk(cls, root: str, path: str, names: dict, sources: dict, depth: int) -> None:
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
                # First root wins, but every root is remembered so the
                # ambiguity is reportable rather than silent.
                sources.setdefault(entry, [])
                if root not in sources[entry]:
                    sources[entry].append(root)
                names.setdefault(entry, root)
                continue  # never look for skills inside a skill
            cls._walk(root, candidate, names, sources, depth + 1)

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


def _like_prefix(ref: str) -> str:
    """``ref`` as a LIKE prefix with ``%``, ``_`` and ``\\`` taken literally.

    Session ids are hex, but an operator can paste anything; unescaped, a ``_``
    matches any character and resolves a session that was never asked for.
    """
    escaped = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


class StateDb:
    """Read-only view over the Hermes ``state.db`` transcript store."""

    def __init__(self, path: str):
        self.path = path
        self.conn = open_ro(path)
        try:
            self.schema = self._check_schema()
        except Exception:
            # A rejected schema must not leave the read-only handle open.
            self.close()
            raise

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
        for table, column in (("sessions", "started_at"),
                              ("messages", "timestamp")):
            info = report["tables"].get(table, {})
            # A missing table or column is already reported above; probing it
            # here would raise instead of reporting.
            if not info.get("present") or column in info.get("missing", ()):
                continue
            # SQLite sorts TEXT after every number, so a text timestamp would
            # silently distort the since/until window instead of failing.
            bad = self.conn.execute(
                "SELECT 1 FROM %s WHERE %s IS NOT NULL AND "
                "typeof(%s) NOT IN ('integer', 'real') LIMIT 1"
                % (table, column, column)
            ).fetchone()
            if bad:
                report["compatible"] = False
                report["problems"].append(
                    "table %s has non-numeric %s values" % (table, column))
        if not report["compatible"]:
            raise SourceCorrupt("; ".join(report["problems"]))
        return report

    # ---------------------------------------------------------------- sessions
    def session(self, session_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT %s FROM sessions WHERE id = ?" % ", ".join(SESSION_COLUMNS),
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip((column[0] for column in cursor.description), row))

    def require_session(self, session_id: str) -> dict:
        found = self.session(session_id)
        if found is None:
            raise SourceMissing("session not found: %s" % session_id)
        return found

    def resolve_session(self, ref: str | None) -> dict:
        """Resolve ``latest``, an exact id or a unique id prefix.

        ``latest`` deliberately skips hidden sessions; an explicit reference
        (exact id or prefix) resolves whatever matches, hidden ones included,
        because the caller named that session on purpose.
        """
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
            "SELECT id FROM sessions WHERE id LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT 5",
            (_like_prefix(ref),),
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
        #: One short uid per message already yielded. This is the only
        #: structure here that grows with the session: dropping a uid far from
        #: its twin would re-count a duplicate row, so the trade-off is kept
        #: and declared in the module docstring instead of hidden.
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
#: The value group keeps ``"error": null`` from being read as an error; over a
#: cut-off body no heuristic can do better than this.
ERROR_FIELD_RE = re.compile(r'"(error|exception|traceback)"\s*:\s*(?!null\b)(\S)')
#: ``skill_view`` echoes the skill name at the top of its payload, which is what
#: makes an orphaned result attributable at all.
NAME_FIELD_RE = re.compile(r'"name"\s*:\s*"([^"\\]{1,64})"')


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
    report needs; anything further is deliberately not guessed at. The window
    is searched, not parsed, so a field quoted inside a document body that the
    result happens to contain can be mistaken for the result's own outcome.
    """
    out: dict = {"_raw": raw, "_truncated": True}
    match = SUCCESS_RE.search(raw[:512])
    if match:
        out["success"] = match.group(1) == "true"
    if ERROR_FIELD_RE.search(raw[:2048]):
        out["error"] = "resultado truncado; campo de erro presente"
    name = NAME_FIELD_RE.search(raw[:1024])
    if name:
        out["name"] = name.group(1)
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


def native_path(raw: str) -> str:
    """Translate an MSYS ``/c/...`` drive path into the ``C:\\...`` form.

    Output captured from the git-bash shell Hermes runs in uses POSIX drive
    paths, which ``ntpath.isabs`` rejects; unhandled they would be read as
    relative and joined onto a root that cannot contain them.
    """
    match = re.match(r"^/([A-Za-z])/(.*)$", raw or "")
    if not match:
        return raw
    return "%s:\\%s" % (match.group(1).upper(), match.group(2).replace("/", "\\"))


def inside_root(path: str, root: str) -> bool:
    """True when ``path`` sits inside ``root``; both are already normalised.

    ``os.path.commonpath`` raises ``ValueError`` for paths on different drives,
    or when one is drive-relative and the other is not. A candidate the
    comparison cannot place is outside every root, which is the safe answer.
    """
    try:
        return os.path.commonpath([os.path.normpath(root), path]) == os.path.normpath(root)
    except ValueError:
        return False


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
        raw = native_path(match.group(1))
        if raw.startswith("<") or "..." in raw:
            continue
        candidates = [raw] if os.path.isabs(raw) else [os.path.join(b, raw) for b in bases]
        for candidate in candidates:
            normalised = os.path.normpath(candidate)
            if not any(inside_root(normalised, b) for b in bases):
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
        "truncated_messages": 0,
        "truncated_tool_call_messages": 0,
        "truncated_events": 0,
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
        content_cut = (row.get("content_chars") or 0) > CONTENT_CHARS
        calls_cut = (row.get("tool_calls_chars") or 0) > TOOL_CALL_CHARS
        if content_cut:
            facts["truncated_messages"] += 1
        if calls_cut:
            facts["truncated_tool_call_messages"] += 1
        if role == "assistant":
            for call in parse_tool_calls(row.get("tool_calls_head") or ""):
                # A call with no id cannot be matched to a result later. Keying
                # it as the literal "None" would make two such calls collide
                # and swap their results, so it is left unattributable instead.
                call_id = call.get("call_id") or None
                if call_id:
                    known_calls.add(call_id)
                evidence = attribute_call(call, row, index, scope, ts)
                if evidence is None:
                    continue
                if calls_cut:
                    evidence["truncated"] = True
                if call_id:
                    pending[call_id] = evidence
                events.append(evidence)
        elif role == "tool":
            result = parse_tool_result(row.get("content_head") or "")
            call_id = row.get("tool_call_id") or None
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
                if not call_id or call_id not in known_calls:
                    facts["skill_events_without_call"] += 1
                events.append(evidence)
            apply_result(evidence, result, row, roots, repo, verify_artifacts)
        if role in ("user", "assistant"):
            # Both conversational roles can "mention" a skill in prose, so this
            # is not an ``elif``: an assistant turn is consumed above by its
            # tool calls and would otherwise never be read for mentions.
            # System/context rows carry the whole skill index and would mark
            # every skill, so they stay out.
            text = row.get("content_head") or ""
            if role == "assistant":
                # The assistant body carries its tool-call payload; its
                # arguments must not be read as prose.
                text = strip_tool_call_json(text)
            for name in skills_in_text(text, index):
                mention = {
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
                }
                if content_cut:
                    mention["truncated"] = True
                events.append(mention)

    facts["truncated_events"] = sum(1 for event in events if event.get("truncated"))
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
    raw_args = json.dumps(args, ensure_ascii=False)
    matched = skills_in_text(raw_args, index)
    if not matched:
        return None
    event: dict[str, object] = dict(
        base,
        kind="skill_script",
        skill=matched[0],
        detail=matched_path(raw_args, matched[0]),
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
        # The result is real, but no call for it was observed in the window, so
        # the load cannot be confirmed from this transcript alone.
        "limitations": ["out_of_transcript_load"],
    }


def _apply_error(evidence: dict[str, object], error: object) -> None:
    """Record a failure field; an explicit error outranks every other signal.

    ``ok`` and ``basis`` are always written together, so a success flag can
    never survive next to a contradictory ``basis``.
    """
    if not error:
        return
    evidence["ok"] = False
    evidence["basis"] = "error_field"
    evidence["error"] = redact(str(error), MAX_ERROR)


def apply_result(evidence: dict[str, object], result: dict, row: dict, roots: list[str],
                 repo: str | None, verify_artifacts: bool) -> None:
    """Attach the observable outcome of a tool result to its observation.

    The same precedence holds for every ``kind``: an ``error`` field means the
    call failed, so it outranks ``success`` and ``exit_code`` alike.
    """
    kind = evidence["kind"]
    if result.get("_truncated"):
        evidence["result_truncated"] = True
    if (row.get("content_chars") or 0) > CONTENT_CHARS:
        evidence["truncated"] = True
    if kind in ("skill_view", "skill_manage"):
        success = result.get("success")
        if isinstance(success, bool):
            evidence["ok"] = success
            evidence["basis"] = "success_flag"
        elif row.get("display_kind") == "failed_turn":
            evidence["ok"] = False
            evidence["basis"] = "failed_turn"
        _apply_error(evidence, result.get("error"))
        excerpt = result.get("description") or result.get("message")
        if not excerpt and kind == "skill_manage" and evidence.get("ok"):
            # Not transcript content: flagged so it is never read as a quote.
            evidence["excerpt"] = SYNTHETIC_EXCERPT
            evidence["excerpt_synthetic"] = True
        elif excerpt:
            evidence["excerpt"] = redact(str(excerpt))
        return
    if kind != "skill_script":
        return
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        evidence["ok"] = exit_code == 0
        evidence["basis"] = "exit_code"
        evidence["exit_code"] = exit_code
    elif row.get("display_kind") == "failed_turn":
        evidence["ok"] = False
        evidence["basis"] = "failed_turn"
    _apply_error(evidence, result.get("error"))
    output = result.get("output")
    if not output and result.get("_raw"):
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
