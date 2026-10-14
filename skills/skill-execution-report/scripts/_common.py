"""Shared helpers for the skill-execution-report skill.

Standard library only. This module never writes to disk, never opens a
database for writing and never talks to the network. Everything here is
deterministic given the inputs plus the optional ``SER_NOW`` clock override
used by the hermetic test suite.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from typing import NoReturn

SCHEMA_ID = "skill-execution-report/v1"
SCHEMA_VERSION = 1

#: Exit code contract, documented in README.md and references/schema.md.
EXIT_OK = 0
EXIT_NO_EVIDENCE = 1
EXIT_CORRUPT = 2
EXIT_MISSING = 3
EXIT_USAGE = 4

#: Ordered observation ladder. A skill is reported at the strongest state that
#: the collected evidence actually supports. ``unverifiable`` is not part of the
#: ladder: it means a signal exists but cannot be attributed to a verified
#: state, so it is never presented as proof of use.
SKILL_STATES = (
    "available",
    "discovered",
    "loaded",
    "invoked",
    "execution_proven",
    "unverifiable",
)
STATE_RANK = {
    "unverifiable": 0,
    "available": 0,
    "discovered": 1,
    "loaded": 2,
    "invoked": 3,
    "execution_proven": 4,
}
STATE_LABELS_PT = {
    "available": "disponível",
    "discovered": "descoberta",
    "loaded": "carregada",
    "invoked": "invocada",
    "execution_proven": "execução comprovada",
    "unverifiable": "não verificável",
}

EVIDENCE_KINDS = (
    "skill_dir",
    "mention",
    "skill_view",
    "skill_manage",
    "skill_script",
    "artifact_file",
    "artifact_commit",
    "usage_counter",
)
EVIDENCE_LABELS_PT = {
    "skill_dir": "diretório da skill existe",
    "mention": "nome citado na sessão",
    "skill_view": "chamada skill_view",
    "skill_manage": "edição via skill_manage",
    "skill_script": "script da skill executado",
    "artifact_file": "arquivo produzido verificado",
    "artifact_commit": "commit verificado no repositório",
    "usage_counter": "contador agregado (.usage.json)",
}
SOURCE_IDS = ("state_db", "usage_json", "skills_index", "git")
RESULT_STATES = ("succeeded", "failed", "unknown")
VERIFICATION_STATES = ("verified", "unverifiable")

#: Bases a report may resolve produced paths against. Anything outside these is
#: never stat-ed.
MAX_EXCERPT = 160
MAX_SKILLS_DEFAULT = 200

DENY_VALUE_RES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    re.compile(r"(?i)\b(api[_-]?key|token|password|passwd|secret)\s*[:=]\s*\S{6,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
)
#: Absolute Windows and POSIX home paths are collapsed so a shared report never
#: carries the local account name.
HOME_PATH_RES = (
    re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s\"']+"),
    # Transcript excerpts carry JSON-escaped paths (C:\\Users\\name\\AppData).
    # The single-separator pattern above does not match those doubled
    # separators, which used to leak the local account name into the report.
    re.compile(r"(?i)\b[A-Z]:\\{1,4}Users\\{1,4}[^\\/\s\"']+"),
    re.compile(r"\B/(?:home|Users)/[^/\s\"']+"),
)
#: Session identifiers are not secrets, but they are the only handle that can
#: link a public report back to a private transcript, so excerpts drop them.
SESSION_ID_RE = re.compile(r"\b\d{8}_\d{6}_[0-9a-f]{6}\b")

WS_RE = re.compile(r"\s+")
REDACTED = "[redacted]"


class ReportError(Exception):
    """Base error for this skill."""


class SourceMissing(ReportError):
    """A required data source does not exist."""


class SourceCorrupt(ReportError):
    """A data source exists but cannot be parsed or has an unknown schema."""


class UsageArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit with EXIT_USAGE (4).

    argparse's default is 2, which collides with EXIT_CORRUPT in this CLI; the
    documented contract is 4 for every usage problem.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, "%s: error: %s\n" % (self.prog, message))


def env_hermes_home() -> str:
    """Hermes home directory, honouring HERMES_HOME when set."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        return os.path.join(base, "hermes")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "hermes")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "hermes")


def default_db_path() -> str:
    return os.path.join(env_hermes_home(), "state.db")


def default_usage_path() -> str:
    return os.path.join(env_hermes_home(), "skills", ".usage.json")


def default_skills_root() -> str:
    return os.path.join(env_hermes_home(), "skills")


def now_ts() -> float:
    """Current epoch seconds, or the ``SER_NOW`` override for tests."""
    override = os.environ.get("SER_NOW")
    if override:
        return parse_iso(override)
    return time.time()


def clock_source() -> str:
    return "SER_NOW" if os.environ.get("SER_NOW") else "system"


def parse_iso(value: str) -> float:
    """Parse an ISO-8601 timestamp or a bare epoch number into epoch seconds."""
    text = (value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        return float(text)
    except ValueError:
        pass
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp: %s" % value) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def iso(ts: float | None) -> str | None:
    """Render epoch seconds as UTC ISO-8601 with a ``Z`` suffix.

    Out-of-range values return ``None`` instead of raising: a corrupt or
    hostile timestamp must never take down the report.
    """
    if ts is None:
        return None
    try:
        moment = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_local(ts: float | None) -> str | None:
    """Render epoch seconds in the local timezone (human report only).

    Some platforms cannot resolve local time for instants before the epoch
    (Windows raises ``OSError`` for those), so the UTC rendering is used as a
    fallback instead of failing the whole report.
    """
    if ts is None:
        return None
    try:
        moment = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    try:
        moment = moment.astimezone()
    except (OverflowError, OSError, ValueError):
        pass
    return moment.replace(microsecond=0).isoformat()


def redact(text: str, limit: int = MAX_EXCERPT) -> str:
    """Sanitise a short excerpt taken from a transcript record.

    Drops credentials, local home paths and session identifiers, collapses
    whitespace, then truncates. The result is meant to be quotable in a report
    without carrying secrets or private paths.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    cleaned = text.replace("\r", "\n")
    for pattern in DENY_VALUE_RES:
        cleaned = pattern.sub(REDACTED, cleaned)
    for pattern in HOME_PATH_RES:
        cleaned = pattern.sub("<home>", cleaned)
    cleaned = SESSION_ID_RE.sub("<session>", cleaned)
    cleaned = WS_RE.sub(" ", cleaned).strip()
    if limit and len(cleaned) > limit:
        cleaned = cleaned[: max(0, limit - 3)].rstrip() + "..."
    return cleaned


def contains_secret(text: str) -> bool:
    """True when a value still looks like a credential after redaction."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in DENY_VALUE_RES)


def open_ro(path: str) -> sqlite3.Connection:
    """Open a SQLite database strictly read-only.

    Uses a ``mode=ro`` URI so a missing file raises instead of being created,
    then pins ``query_only`` so no statement can mutate the database even if a
    caller makes a mistake.
    """
    if not os.path.exists(path):
        raise SourceMissing("database not found: %s" % os.path.basename(path))
    escaped = (path.replace("%", "%25").replace("\\", "/")
               .replace("?", "%3f").replace("#", "%23"))
    uri = "file:%s?mode=ro" % escaped
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise SourceCorrupt("cannot open database read-only: %s" % exc) from exc
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
    except sqlite3.Error as exc:
        conn.close()
        raise SourceCorrupt("cannot configure read-only connection: %s" % exc) from exc
    return conn


def dumps(obj) -> str:
    """Deterministic JSON: sorted keys, UTF-8, stable separators."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def load_json_file(path: str):
    """Parse a JSON file, mapping failures onto SourceCorrupt."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SourceMissing("cannot read %s: %s" % (os.path.basename(path), exc)) from exc
    except ValueError as exc:
        raise SourceCorrupt("invalid JSON in %s: %s" % (os.path.basename(path), exc)) from exc


def load_usage(path: str | None) -> dict:
    """Normalise ``skills/.usage.json`` into a flat counter view.

    The file is a per-skill counter store keyed by skill name; a ``skills``
    wrapper is accepted too. These counters are global to the Hermes
    installation, not per session, so they are always reported as aggregate
    signals that cannot be attributed to one session.
    """
    if not path or not os.path.exists(path):
        return {"present": False, "skills": {}, "counters_are_global": True}
    data = load_json_file(path)
    if isinstance(data, dict) and isinstance(data.get("skills"), dict):
        skills = data["skills"]
    elif isinstance(data, dict):
        skills = {key: value for key, value in data.items() if isinstance(value, dict)}
    else:
        raise SourceCorrupt("unexpected .usage.json structure")
    return {
        "present": True,
        "skills": skills,
        "counters_are_global": True,
        "file": file_facts(path),
    }


def file_facts(path: str) -> dict:
    """Non-identifying facts about a source file (no absolute path)."""
    try:
        stat = os.stat(path)
    except OSError:
        return {"present": False}
    return {
        "present": True,
        "name": os.path.basename(path),
        "bytes": stat.st_size,
        "modified_at": iso(stat.st_mtime),
    }
