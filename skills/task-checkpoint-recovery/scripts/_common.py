"""Shared helpers for the task-checkpoint-recovery skill.

Standard library only. No network access, no secrets, no absolute user paths
are ever persisted. Every function here is deterministic given the inputs and
the ``TCR_NOW`` clock override used by the test suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time

SCHEMA_ID = "task-checkpoint/v1"
SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_CORRUPT = 2
EXIT_MISSING = 3
EXIT_USAGE = 4

HISTORY_KEEP_DEFAULT = 20
STALE_AFTER_SECONDS_DEFAULT = 24 * 3600
MAX_TEXT = 2000
LOCK_STALE_SECONDS = 300.0

#: Fields the checkpoint schema permits. Anything else is rejected so that a
#: checkpoint can never silently accumulate prompts, raw tool output, env
#: dumps or credentials (adjustment 5 of the brief).
ALLOWED_KEYS = {
    "schema", "revision", "task", "repo", "steps", "decisions", "blockers",
    "next_actions", "files", "artifacts", "operations_summary", "checks",
    "notes", "limitations",
}
ALLOWED_TASK = {"id", "title", "objective", "status", "created_at", "updated_at"}
ALLOWED_REPO = {"available", "root", "branch", "head", "remote_head", "dirty",
                "ahead", "behind", "reason"}
ALLOWED_STEP = {"id", "title", "status", "evidence", "completed_at"}
ALLOWED_DECISION = {"id", "decision", "rationale", "at"}
ALLOWED_BLOCKER = {"id", "what", "needs", "at"}
ALLOWED_NEXT = {"id", "action", "requires_approval"}
ALLOWED_FILE = {"path", "sha256", "state"}
ALLOWED_ARTIFACT = {"kind", "id", "at", "state"}
ALLOWED_CHECK = {"command", "exit", "at"}

STEP_STATUS = {"done", "pending", "blocked"}
TASK_STATUS = {"in_progress", "blocked", "awaiting_approval", "done"}
FILE_STATE = {"created", "modified", "deleted"}
OP_KINDS = {"commit", "push", "pr", "merge", "download", "other"}
OP_STATES = {"planned", "started", "verified", "failed", "unknown"}
#: A ledger entry is only ever a claim. Only these states may be treated as a
#: reason to skip work, and only after revalidation against reality.
OP_TRUSTWORTHY = {"verified"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

DENY_KEY_RE = re.compile(
    r"(^|[_-])(token|tokens|secret|secrets|password|passwd|credential|credentials|"
    r"api[_-]?key|apikey|auth|authorization|bearer|cookie|cookies|"
    r"private[_-]?key|access[_-]?key|session[_-]?id)($|[_-])",
    re.IGNORECASE,
)
DENY_VALUE_RES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret|passwd)\s*[:=]\s*\S{6,}"),
)
ENV_DUMP_RE = re.compile(r"(?m)^\s*[A-Z][A-Z0-9_]{2,}\s*=\s*\S+")


class CheckpointError(Exception):
    """Base error for this skill."""


class SecurityError(CheckpointError):
    """Content or path rejected by the privacy rules."""


class CorruptCheckpoint(CheckpointError):
    """Checkpoint could not be parsed or did not satisfy the schema."""


class RevisionConflict(CheckpointError):
    """Compare-and-swap on ``revision`` detected a concurrent write."""


class LockTimeout(CheckpointError):
    """Another process held the store lock for too long."""


# --------------------------------------------------------------------------
# time / ids
# --------------------------------------------------------------------------

def now_iso(now: str | None = None) -> str:
    """ISO-8601 timestamp. ``TCR_NOW`` (or ``now``) keeps tests deterministic."""
    if now:
        return now
    override = os.environ.get("TCR_NOW")
    if override:
        return override
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def now_epoch() -> float:
    override = os.environ.get("TCR_EPOCH")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return time.time()


def validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ValueError(
            "invalid task id %r: use lowercase letters, digits, dot, dash or "
            "underscore (max 64 chars)" % (slug,)
        )
    return slug


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text: str, size: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def store_dir(root: str, slug: str) -> str:
    validate_slug(slug)
    return os.path.join(os.path.abspath(root), ".hermes", "checkpoints", slug)


def checkpoint_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "checkpoint.json")


def markdown_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "checkpoint.md")


def ops_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "operations.jsonl")


def history_dir(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "history")


def safe_relpath(root: str, rel: str, must_exist: bool = False) -> str:
    """Normalise ``rel`` and refuse anything escaping ``root``.

    Blocks absolute paths, Windows drive letters, ``..`` components, and
    symlinks that resolve outside ``root``.
    """
    if not rel or not isinstance(rel, str):
        raise SecurityError("empty path")
    if os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel) or rel.startswith(("\\\\", "//")):
        raise SecurityError("absolute paths are not allowed: %r" % (rel,))
    norm = os.path.normpath(rel).replace("\\", "/")
    if norm in (".", "") or norm.startswith("../") or norm == ".." or "/../" in norm:
        raise SecurityError("path escapes the project root: %r" % (rel,))

    abs_root = os.path.realpath(os.path.abspath(root))
    target = os.path.join(abs_root, norm)
    real = os.path.realpath(target)
    if real != abs_root and not real.startswith(abs_root + os.sep):
        raise SecurityError("path resolves outside the project root: %r" % (rel,))

    # Refuse symlinked paths outright. A link target can be repointed after the
    # checkpoint was taken, which would silently invalidate the recorded hash and
    # could make a recorded path escape the project later. A path that is a link
    # is therefore never recorded.
    probe = abs_root
    for part in norm.split("/"):
        probe = os.path.join(probe, part)
        if os.path.islink(probe):
            raise SecurityError("symlinked paths are not allowed: %r" % (rel,))
    if must_exist and not os.path.exists(real):
        raise SecurityError("file does not exist: %r" % (rel,))
    return norm


def relative_to_root(root: str, path: str) -> str:
    """Best-effort repo-relative path; never leaks an absolute user path."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:
        return os.path.basename(path)
    rel = rel.replace("\\", "/")
    if rel.startswith("../"):
        return os.path.basename(path)
    return rel


# --------------------------------------------------------------------------
# privacy
# --------------------------------------------------------------------------

def scan_secrets(value, path: str = "$") -> list[str]:
    """Return findings for anything that must never be persisted."""
    findings: list[str] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            for key, sub in node.items():
                if DENY_KEY_RE.search(str(key)):
                    findings.append("secret-looking field name %s.%s" % (where, key))
                walk(sub, "%s.%s" % (where, key))
        elif isinstance(node, (list, tuple)):
            for i, sub in enumerate(node):
                walk(sub, "%s[%d]" % (where, i))
        elif isinstance(node, str):
            for rx in DENY_VALUE_RES:
                if rx.search(node):
                    findings.append("secret-looking value matching %s at %s" % (rx.pattern[:28], where))
                    break
            if ENV_DUMP_RE.search(node):
                findings.append("environment-variable dump (potential secrets) at %s" % where)
            if len(node) > MAX_TEXT:
                findings.append("value too long (%d > %d) at %s" % (len(node), MAX_TEXT, where))

    walk(value, path)
    return findings


def assert_no_secrets(value, path: str = "$") -> None:
    findings = scan_secrets(value, path)
    if findings:
        raise SecurityError(
            "checkpoint refused: " + "; ".join(sorted(set(findings)))
        )


# --------------------------------------------------------------------------
# atomic writes and locking
# --------------------------------------------------------------------------

def atomic_write_text(path: str, text: str) -> None:
    """Write ``text`` atomically: same-dir temp file, fsync, os.replace."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(
        directory,
        ".%s.%d.%s.tmp" % (os.path.basename(path), os.getpid(), os.urandom(4).hex()),
    )
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


class FileLock:
    """Cross-process lock guarding a checkpoint store.

    Protects against concurrent writers, including subagents or separate
    processes (adjustment 3). A lock older than ``LOCK_STALE_SECONDS`` is
    considered abandoned and reclaimed.
    """

    def __init__(self, path: str, timeout: float = 10.0, poll: float = 0.05,
                 stale_after: float = LOCK_STALE_SECONDS):
        self.path = path
        self.timeout = timeout
        self.poll = poll
        self.stale_after = stale_after
        self._fd = None

    def acquire(self) -> "FileLock":
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if self._reclaim_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        "another process holds %s" % os.path.basename(self.path)
                    )
                time.sleep(self.poll)
                continue
            except OSError as exc:  # pragma: no cover - platform specific
                raise LockTimeout("cannot create lock %s: %s" % (self.path, exc))
            os.write(fd, ("pid=%d\nat=%s\n" % (os.getpid(), now_iso())).encode("utf-8"))
            os.fsync(fd)
            self._fd = fd
            return self

    def _reclaim_if_stale(self) -> bool:
        try:
            age = time.time() - os.path.getmtime(self.path)
        except OSError:
            return True
        if age <= self.stale_after:
            return False
        try:
            os.unlink(self.path)
        except OSError:
            return False
        return True

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


# --------------------------------------------------------------------------
# git (offline by default)
# --------------------------------------------------------------------------

def git_available() -> bool:
    return shutil.which("git") is not None


def git_run(root: str, *args: str, timeout: float = 20.0):
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 128, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


#: The skill's own store lives inside the project and is documented as
#: git-ignored. Its presence must never make the project look dirty, otherwise a
#: real ``dirty_tree`` divergence could never be told apart from our own files.
HERMES_DIR = ".hermes"


def _tree_is_dirty(porcelain: str) -> bool:
    """True when ``git status --porcelain`` shows anything outside ``.hermes/``."""
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:  # renames: "R  old -> new"
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"').replace("\\", "/")
        if path == HERMES_DIR or path.startswith(HERMES_DIR + "/"):
            continue
        return True
    return False


def git_state(root: str, stale_after: float = STALE_AFTER_SECONDS_DEFAULT) -> dict:
    """Snapshot of the working tree. Never fails hard: unreduced state is
    expressed as ``available: false`` with a ``reason`` (adjustment 4)."""
    state = {
        "available": False, "root": ".", "branch": None, "head": None,
        "remote_head": None, "dirty": None, "ahead": None, "behind": None,
        "reason": None,
    }
    if not git_available():
        state["reason"] = "git executable not found; reduced checks"
        return state
    rc, out, err = git_run(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out != "true":
        state["reason"] = "not a git repository; reduced checks"
        return state

    state["available"] = True
    rc, out, _ = git_run(root, "rev-parse", "--abbrev-ref", "HEAD")
    state["branch"] = out if rc == 0 else None
    rc, out, _ = git_run(root, "rev-parse", "HEAD")
    state["head"] = out if rc == 0 else None
    rc, out, _ = git_run(root, "status", "--porcelain")
    state["dirty"] = _tree_is_dirty(out) if rc == 0 else None
    rc, out, _ = git_run(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc == 0 and out:
        rc2, counts, _ = git_run(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if rc2 == 0 and counts:
            parts = counts.split()
            if len(parts) == 2:
                state["behind"], state["ahead"] = int(parts[0]), int(parts[1])
    state["stale_after"] = stale_after
    return state


def git_is_ancestor(root: str, older: str, newer: str) -> bool | None:
    rc, _, _ = git_run(root, "merge-base", "--is-ancestor", older, newer)
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def git_remote_ref(root: str, remote: str, ref: str):
    """Read a remote ref. Works offline against local/fake remotes; network
    remotes may fail, in which case ``None`` means 'could not verify'."""
    if not remote or not ref:
        return None
    rc, out, _ = git_run(root, "ls-remote", remote, ref, timeout=30.0)
    if rc != 0 or not out:
        return None
    return out.split()[0]


# --------------------------------------------------------------------------
# schema validation
# --------------------------------------------------------------------------

def _err(errors: list[str], msg: str) -> None:
    errors.append(msg)


def _check_keys(obj, allowed: set, where: str, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        _err(errors, "%s must be an object" % where)
        return
    for key in obj:
        if key not in allowed:
            _err(errors, "%s has unsupported field %r" % (where, key))


def validate_checkpoint(data) -> list[str]:
    """Full schema validation. Returns a list of human-readable errors."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["checkpoint must be a JSON object"]
    _check_keys(data, ALLOWED_KEYS, "$", errors)
    if data.get("schema") != SCHEMA_ID:
        _err(errors, "unsupported schema %r (expected %r)" % (data.get("schema"), SCHEMA_ID))
    revision = data.get("revision")
    if not isinstance(revision, int) or revision < 1:
        _err(errors, "revision must be a positive integer")

    task = data.get("task")
    if not isinstance(task, dict):
        _err(errors, "task must be an object")
    else:
        _check_keys(task, ALLOWED_TASK, "$.task", errors)
        for field in ("id", "title", "objective", "created_at", "updated_at"):
            if not isinstance(task.get(field), str) or not task.get(field):
                _err(errors, "task.%s must be a non-empty string" % field)
        if task.get("id") and not SLUG_RE.match(task["id"]):
            _err(errors, "task.id is not a valid task id")
        if task.get("status") not in TASK_STATUS:
            _err(errors, "task.status must be one of %s" % sorted(TASK_STATUS))

    repo = data.get("repo")
    if repo is not None:
        _check_keys(repo, ALLOWED_REPO, "$.repo", errors)
        if not isinstance(repo.get("available"), bool):
            _err(errors, "repo.available must be a boolean")

    for index, step in enumerate(data.get("steps") or []):
        where = "$.steps[%d]" % index
        _check_keys(step, ALLOWED_STEP, where, errors)
        for field in ("id", "title"):
            if not isinstance(step.get(field), str) or not step.get(field):
                _err(errors, "%s.%s must be a non-empty string" % (where, field))
        if step.get("status") not in STEP_STATUS:
            _err(errors, "%s.status must be one of %s" % (where, sorted(STEP_STATUS)))

    for index, dec in enumerate(data.get("decisions") or []):
        where = "$.decisions[%d]" % index
        _check_keys(dec, ALLOWED_DECISION, where, errors)
        if not isinstance(dec.get("decision"), str) or not dec.get("decision"):
            _err(errors, "%s.decision must be a non-empty string" % where)

    for index, blocker in enumerate(data.get("blockers") or []):
        where = "$.blockers[%d]" % index
        _check_keys(blocker, ALLOWED_BLOCKER, where, errors)
        if not isinstance(blocker.get("what"), str) or not blocker.get("what"):
            _err(errors, "%s.what must be a non-empty string" % where)
        if not isinstance(blocker.get("needs"), str) or not blocker.get("needs"):
            _err(errors, "%s.needs must be a non-empty string (what it waits on)" % where)

    for index, nxt in enumerate(data.get("next_actions") or []):
        where = "$.next_actions[%d]" % index
        _check_keys(nxt, ALLOWED_NEXT, where, errors)
        if not isinstance(nxt.get("action"), str) or not nxt.get("action"):
            _err(errors, "%s.action must be a non-empty string" % where)
        if not isinstance(nxt.get("requires_approval"), bool):
            _err(errors, "%s.requires_approval must be a boolean" % where)

    for index, item in enumerate(data.get("files") or []):
        where = "$.files[%d]" % index
        _check_keys(item, ALLOWED_FILE, where, errors)
        if not isinstance(item.get("path"), str) or not item.get("path"):
            _err(errors, "%s.path must be a non-empty string" % where)
        elif os.path.isabs(item["path"]) or re.match(r"^[A-Za-z]:", item["path"]):
            _err(errors, "%s.path must be project-relative" % where)
        if item.get("state") not in FILE_STATE:
            _err(errors, "%s.state must be one of %s" % (where, sorted(FILE_STATE)))

    for index, art in enumerate(data.get("artifacts") or []):
        where = "$.artifacts[%d]" % index
        _check_keys(art, ALLOWED_ARTIFACT, where, errors)
        if art.get("kind") not in OP_KINDS:
            _err(errors, "%s.kind must be one of %s" % (where, sorted(OP_KINDS)))

    for index, chk in enumerate(data.get("checks") or []):
        where = "$.checks[%d]" % index
        _check_keys(chk, ALLOWED_CHECK, where, errors)
        if not isinstance(chk.get("command"), str) or not chk.get("command"):
            _err(errors, "%s.command must be a non-empty string" % where)
        if not isinstance(chk.get("exit"), int):
            _err(errors, "%s.exit must be an integer" % where)

    summary = data.get("operations_summary")
    if summary is not None:
        if not isinstance(summary, dict):
            _err(errors, "operations_summary must be an object")
        else:
            for key in summary:
                if key not in OP_STATES:
                    _err(errors, "operations_summary has unknown state %r" % key)
    for field in ("notes", "limitations"):
        value = data.get(field)
        if value is not None and not isinstance(value, list):
            _err(errors, "%s must be a list" % field)
    return errors


def load_json_strict(path: str):
    """Parse JSON or raise :class:`CorruptCheckpoint` with a clear message."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorruptCheckpoint("invalid JSON in %s: %s" % (os.path.basename(path), exc))
    except OSError as exc:
        raise CorruptCheckpoint("cannot read %s: %s" % (os.path.basename(path), exc))


def quarantine(path: str, reason: str) -> str:
    """Move a corrupt file aside so the last valid state keeps working."""
    stamp = now_iso().replace(":", "").replace("-", "")
    target = "%s.corrupt-%s" % (path, stamp)
    try:
        os.replace(path, target)
    except OSError:
        pass
    return target


def history_snapshots(root: str, slug: str) -> list[str]:
    directory = history_dir(root, slug)
    if not os.path.isdir(directory):
        return []
    return sorted(
        (os.path.join(directory, name) for name in os.listdir(directory)
         if name.endswith(".json")),
        key=lambda p: (os.path.getmtime(p), p),
    )


def recover_from_history(root: str, slug: str):
    """Return the newest schema-valid snapshot, or ``None``."""
    for path in reversed(history_snapshots(root, slug)):
        try:
            data = load_json_strict(path)
        except CorruptCheckpoint:
            continue
        if not validate_checkpoint(data):
            return data, path
    return None


def load_checkpoint(root: str, slug: str, quarantine_corrupt: bool = True):
    """Load the current checkpoint, falling back to the newest valid snapshot.

    Raises :class:`FileNotFoundError` when nothing exists and
    :class:`CorruptCheckpoint` when nothing valid can be recovered.
    """
    path = checkpoint_path(root, slug)
    errors_seen: list[str] = []
    data = None
    try:
        data = load_json_strict(path)
    except FileNotFoundError:
        data = None
    except CorruptCheckpoint as exc:
        errors_seen.append(str(exc))
        data = None
    if data is not None:
        problems = validate_checkpoint(data)
        if not problems:
            return data, path, "current"
        errors_seen.append("; ".join(problems))

    # Fall back to the newest valid snapshot. This also covers a checkpoint.json
    # that is simply gone (quarantined or deleted): the work must not be lost.
    recovered = recover_from_history(root, slug)
    if recovered is not None:
        # The damaged file is moved aside only now, once a valid fallback exists,
        # so a corrupt file is never the only copy that gets lost.
        if quarantine_corrupt and os.path.exists(path):
            quarantine(path, "; ".join(errors_seen))
        return recovered[0], recovered[1], "history-fallback"
    if errors_seen:
        raise CorruptCheckpoint("corrupt checkpoint: " + "; ".join(errors_seen))
    raise FileNotFoundError(path)


# --------------------------------------------------------------------------
# operations ledger (append-only)
# --------------------------------------------------------------------------

def read_ops(root: str, slug: str) -> list[dict]:
    path = ops_path(root, slug)
    if not os.path.exists(path):
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn line never invalidates earlier evidence
    return events


def append_op(root: str, slug: str, event: dict) -> dict:
    path = ops_path(root, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = dict(event)
    event.setdefault("at", now_iso())
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return event


def current_ops(events: list[dict]) -> dict[str, dict]:
    """Collapse the append-only log into the current claim per ``op_id``.

    This is still only a *claim*: it is never sufficient on its own to treat an
    operation as complete (adjustment 2).
    """
    state: dict[str, dict] = {}
    for event in events:
        op_id = event.get("op_id")
        if not op_id:
            continue
        merged = dict(state.get(op_id, {}))
        merged.update(event)
        if event.get("state") == "verified":
            merged["verified_at"] = event.get("at")
        elif event.get("state") in ("started", "failed", "planned"):
            merged.pop("verified_at", None)
        state[op_id] = merged
    return state


def ops_summary(events: list[dict]) -> dict:
    summary = {state_name: 0 for state_name in sorted(OP_STATES)}
    for entry in current_ops(events).values():
        state_name = entry.get("state")
        if state_name in summary:
            summary[state_name] += 1
    return summary