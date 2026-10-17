"""Shared helpers for the skill-quality-audit skill.

Standard library only. Nothing here executes the audited target, imports it,
follows a symlink out of it or writes inside it: the target is read as analysis
material. The only writes this module performs are the report and the manifest
the caller asks for, and both are refused when they would land inside the target.

Everything is deterministic given the target plus the optional ``SQA_NOW`` clock
override used by the hermetic test suite.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from typing import NoReturn

from checks import (
    CATALOGUE,
    METHODS,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SEVERITIES,
    SEVERITY_ORDER,
    VALIDATION_STATES,
    by_code,
    worst_severity,
)

#: Exit code contract, documented in README.md and references/finding-format.md.
EXIT_OK = 0
#: The audit completed and produced at least one finding at or above --fail-on.
EXIT_FINDINGS = 1
#: The target does not exist or is not a directory: nothing could be audited.
EXIT_TARGET_MISSING = 2
#: The caller asked for something the tool refuses (for example --out inside the
#: target, or an unknown severity). Kept apart from EXIT_OUTPUT: no report exists.
EXIT_USAGE = 3
#: The audit succeeded but the requested output file could not be written.
EXIT_OUTPUT = 4
#: The target changed while the audit was running: the read-only promise broke.
EXIT_TARGET_CHANGED = 5
#: The auditor itself failed unexpectedly. Kept apart from EXIT_FINDINGS so a
#: pipeline never reads a crash as "audit ran and found problems".
EXIT_INTERNAL = 6

MAX_EVIDENCE = 200
REDACTED = "[redacted]"

#: Paths never walked, never hashed and never scanned. Declared in the report so
#: the coverage of both the manifest and the privacy scan stays explicit.
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
        ".hermes",
    }
)
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe")
EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

#: Extensions treated as binary even without a NUL probe.
BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".whl",
    ".mp3",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
)

REQUIRED_KEYS = ("name", "description", "version", "platforms")
KNOWN_PLATFORMS = ("windows", "macos", "linux")
CONVENTIONAL_TRIGGER = "use when"
TRIGGER_PREFIX_CHARS = 57

#: Wrappers that mark a value as a deliberate placeholder rather than real data.
#: ``{`` covers template interpolations such as ``{TOKEN}`` and ``{value}``.
PLACEHOLDER_WRAPPERS = ("<", "%", "${", "{")
#: Trailing escape sequences of a string literal in scanned source code.
SOURCE_ESCAPE_RE = re.compile(r"(?:\\[nrt])+$")
#: Values that are placeholders on their own, case-insensitive.
PLACEHOLDER_VALUES = frozenset(
    {
        "user",
        "usuario",
        "usuário",
        "username",
        "user-name",
        "youruser",
        "your-user",
        "yourname",
        "your-name",
        "you",
        "name",
        "example",
        "sample",
        "someone",
        "nome",
        "changeme",
        "change-me",
        "placeholder",
        "redacted",
    }
)
#: Domains that are reserved for documentation and never count as personal data.
GENERIC_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "test.invalid",
        "localhost",
        "users.noreply.github.com",
    }
)

TEXT_FILE_SUFFIXES = (
    ".md",
    ".markdown",
    ".txt",
    ".py",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".bash",
    ".ps1",
    ".bat",
    ".cmd",
    ".csv",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sql",
    ".rst",
    ".env",
)

#: Suffixes never decoded: the manifest still hashes them, the text scan skips
#: them. Any other suffix is also skipped, and the report declares the skip in
#: ``coverage.excluded_suffixes`` instead of leaving it implicit.
TEXT_FILE_SUFFIX_POLICY = (
    "Arquivos com sufixo fora de TEXT_FILE_SUFFIXES entram no manifesto mas não "
    "são lidos: a exclusão aparece em coverage.excluded_suffixes."
)

#: Paths that escape the target or are absolute are handled by SQA-014.
ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")

#: (class, code, pattern) for privacy scanning. The first capture group is the
#: sensitive span; the report never reproduces it.
PRIVACY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "credential_assignment",
        "SQA-019",
        re.compile(
            r"(?i)\b(?:password|passwd|senha|api[_-]?key|apikey|access[_-]?key|secret|token)"
            r"\b\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
        ),
    ),
    ("aws_access_key_id", "SQA-019", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("github_token", "SQA-019", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b")),
    ("github_pat", "SQA-019", re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_style_key", "SQA-019", re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b")),
    ("slack_token", "SQA-019", re.compile(r"\b(xox[abprs]-[A-Za-z0-9-]{10,})\b")),
    ("google_api_key", "SQA-019", re.compile(r"\b(AIza[0-9A-Za-z_-]{30,})\b")),
    ("jwt", "SQA-019", re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b")),
    ("bearer_token", "SQA-019", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._-]{16,})")),
    ("private_key_block", "SQA-019", re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
    ("windows_user_path", "SQA-020", re.compile(r"(?i)\b[a-z]:\\users\\([^\\\s\"'`]+)")),
    ("posix_user_path", "SQA-020", re.compile(r"(?<![\w.])/home/([a-z0-9._-]+)")),
    ("email", "SQA-020", re.compile(r"\b([A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,}))\b")),
    ("national_id", "SQA-020", re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")),
    ("session_id", "SQA-021", re.compile(r"\b(\d{8}_\d{6}_[0-9a-fA-F]{6})\b")),
    (
        "internal_domain",
        "SQA-022",
        re.compile(r"\b([A-Za-z0-9-]+\.(?:corp|internal|intranet|lan)\b)"),
    ),
    (
        "private_url",
        "SQA-022",
        re.compile(r"\b(https?://[A-Za-z0-9.-]*\.(?:corp|internal|intranet|lan)\b)"),
    ),
    (
        "private_ipv4",
        "SQA-022",
        re.compile(
            r"\b((?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)\b"
        ),
    ),
)


class AuditError(Exception):
    """Base class for the tool's own failures."""


class TargetMissing(AuditError):
    """The target does not exist or is not a directory."""


class UsageError(AuditError):
    """The caller asked for something the tool refuses to do."""


class HelpExit(UsageError):
    """argparse finished successfully (``--help``, ``--version``).

    Kept apart from :class:`UsageError` so the entry point can return 0 instead
    of the usage exit code.
    """


class OutputError(AuditError):
    """The report could not be written."""


class UsageArgumentParser(argparse.ArgumentParser):
    """Argument parser that reports usage problems through ``UsageError``."""

    def error(self, message: str) -> NoReturn:  # pragma: no cover - argparse path
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:  # pragma: no cover
        if status == 0:
            raise HelpExit(message or "ajuda solicitada")
        raise UsageError(message or f"exit status {status}")


# --------------------------------------------------------------------------- time


def now_ts() -> float:
    """Current timestamp, or the ``SQA_NOW`` override used by the test suite."""
    raw = os.environ.get("SQA_NOW")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def now_iso() -> str:
    """UTC timestamp in ISO 8601, second precision."""
    return datetime.datetime.fromtimestamp(now_ts(), datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def clock_source() -> str:
    """Where the clock came from, so a frozen report is self-explanatory."""
    return "SQA_NOW" if os.environ.get("SQA_NOW") else "system"


# ------------------------------------------------------------------- file walking


def is_placeholder(value: str) -> bool:
    """True when ``value`` is a documentation placeholder, not real data."""
    text = value.strip().strip("\"'")
    # A fixture written in source code keeps the escape sequence of the string
    # literal (``"secret: changeme\n"``): that is syntax, not part of the value.
    text = SOURCE_ESCAPE_RE.sub("", text)
    if not text:
        return True
    if text.startswith(PLACEHOLDER_WRAPPERS) or text.startswith("$"):
        return True
    if text.endswith(">") and ("<" in text or "%" in text):
        return True
    lowered = text.lower().strip("<>{}%$")
    return lowered in PLACEHOLDER_VALUES


#: Characters that no credential format uses, but source code does.
CODE_VALUE_CHARS = "()[]"


def is_code_expression(value: str) -> bool:
    """True for a code expression that merely follows ``token =``.

    The assignment pattern matches ``token = marker.group(1)`` as readily as a
    real secret; brackets and parentheses only appear in the former, so they
    are treated as evidence of source code rather than of a leaked value.
    """
    return any(char in value for char in CODE_VALUE_CHARS)


def is_repeated_pattern(value: str) -> bool:
    """True when every digit of ``value`` is the same digit.

    ``000.000.000-00`` is how the documentation spells the CPF format; no real
    document has a single repeated digit.
    """
    digits = [char for char in value if char.isdigit()]
    return bool(digits) and len(set(digits)) == 1


def walk_target(root: str) -> list[dict]:
    """Every entry under ``root`` except the excluded noise, sorted by path.

    Symlinks are reported but never followed, so a link pointing outside the
    target cannot pull foreign content into the audit.
    """
    root_abs = os.path.abspath(root)
    entries: list[dict] = []
    for current, dirnames, filenames in os.walk(root_abs, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(dirnames):
            path = os.path.join(current, name)
            entries.append(
                {
                    "abs": path,
                    "rel": _rel(path, root_abs),
                    "kind": "symlink" if os.path.islink(path) else "dir",
                }
            )
        for name in sorted(filenames):
            if name in EXCLUDED_NAMES:
                continue
            if name.endswith(EXCLUDED_SUFFIXES):
                continue
            path = os.path.join(current, name)
            entries.append(
                {
                    "abs": path,
                    "rel": _rel(path, root_abs),
                    "kind": "symlink" if os.path.islink(path) else "file",
                }
            )
    entries.sort(key=lambda item: item["rel"])
    return entries


def _rel(path: str, root_abs: str) -> str:
    return os.path.relpath(path, root_abs).replace("\\", "/")


def text_files(root: str) -> list[dict]:
    """Entries of :func:`walk_target` that are text files safe to read."""
    return text_files_with_exclusions(root)[0]


def text_files_with_exclusions(root: str) -> tuple[list[dict], list[dict]]:
    """Text-file entries plus the files skipped only because of their suffix.

    The second list is what makes the scan boundary auditable: a file that is
    never decoded still appears in the report, with the reason, instead of
    disappearing from coverage.
    """
    out = []
    excluded = []
    for entry in walk_target(root):
        if entry["kind"] != "file":
            continue
        suffix = os.path.splitext(entry["abs"])[1].lower()
        if suffix in BINARY_SUFFIXES:
            excluded.append({"rel": entry["rel"], "reason": "binary_suffix"})
            continue
        if suffix and suffix not in TEXT_FILE_SUFFIXES:
            excluded.append({"rel": entry["rel"], "reason": "suffix"})
            continue
        out.append(entry)
    return out, excluded


def read_text(path: str) -> str | None:
    """Decode a text file, or return ``None`` when it is binary or unreadable."""
    return read_text_state(path)[0]


def read_text_state(path: str) -> tuple[str | None, str]:
    """Decode a text file and report *why* it could not be decoded.

    States: ``ok``, ``unreadable`` (OSError), ``binary`` (NUL byte) and
    ``undecodable`` (no supported encoding). Callers that only need the text use
    :func:`read_text`; callers that report coverage use the state so that "the
    file is missing" and "the file is there but unreadable" never collapse into
    the same finding.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None, "unreadable"
    if b"\x00" in raw[:4096]:
        return None, "binary"
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding), "ok"
        except UnicodeDecodeError:
            continue
    return None, "undecodable"


# --------------------------------------------------------------------- manifests


def sha256_file(path: str) -> str | None:
    """Streaming SHA-256 of a file, or ``None`` when its content is unavailable.

    ``None`` is not a digest: two files that could not be read are *unknown* to
    each other, never equal. Every consumer has to carry that third state
    instead of collapsing it into "unchanged".
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def build_manifest(root: str) -> dict:
    """Content manifest of the target: per-entry hash plus an aggregate digest.

    The manifest is the evidence behind the read-only promise: the auditor
    builds it before and after the checks and reports the difference.
    """
    root_abs = os.path.abspath(root)
    entries: dict[str, dict] = {}
    for entry in walk_target(root_abs):
        rel = entry["rel"]
        if entry["kind"] == "symlink":
            try:
                target = os.readlink(entry["abs"])
            except OSError:
                target = "unreadable"
            entries[rel] = {"kind": "symlink", "target": target}
        elif entry["kind"] == "dir":
            entries[rel] = {"kind": "dir"}
        else:
            size = os.path.getsize(entry["abs"]) if os.path.exists(entry["abs"]) else 0
            entries[rel] = {
                "kind": "file",
                "size": size,
                "sha256": sha256_file(entry["abs"]),
            }
    digest = hashlib.sha256()
    for rel in sorted(entries):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(json.dumps(entries[rel], sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return {
        "root": os.path.basename(root_abs),
        "entry_count": len(entries),
        "file_count": sum(1 for v in entries.values() if v["kind"] == "file"),
        "unreadable": unreadable_entries(entries),
        "digest": digest.hexdigest(),
        "entries": entries,
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_suffixes": list(EXCLUDED_SUFFIXES),
    }


def unreadable_entries(entries: dict) -> list[str]:
    """Relative paths whose content could not be hashed, sorted."""
    return sorted(
        rel
        for rel, value in entries.items()
        if isinstance(value, dict) and value.get("sha256", "") is None
    )


def diff_manifests(before: dict, after: dict) -> dict:
    """Added, removed, changed and *unverifiable* entries between two manifests.

    A file whose content could not be read is neither equal nor different: it
    lands in ``unverifiable`` and makes ``complete`` false. ``identical`` only
    speaks about the entries that could be compared, so callers must consult
    ``complete`` before claiming the target stayed the same.
    """
    old, new = before.get("entries", {}), after.get("entries", {})
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    unreadable = set(unreadable_entries(old)) | set(unreadable_entries(new))
    changed = sorted(
        rel
        for rel in set(old) & set(new)
        if old[rel] != new[rel] and rel not in unreadable
    )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unverifiable": sorted(unreadable),
        "identical": not (added or removed or changed),
        "complete": not unreadable and not (added or removed),
        "digest_before": before.get("digest"),
        "digest_after": after.get("digest"),
    }


def load_manifest(path: str) -> dict:
    """Read a manifest written by ``manifest --out``."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise UsageError(f"manifesto ilegível: {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise UsageError(f"manifesto inválido: {path}: {exc.msg}") from exc
    if not isinstance(data, dict) or "entries" not in data:
        raise UsageError(f"manifesto sem 'entries': {path}")
    return data


# -------------------------------------------------------------------- frontmatter


def split_frontmatter(text: str):
    """Split ``SKILL.md`` into (raw block, body, status, reason)."""
    body = text.lstrip("\ufeff")
    lines = body.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != "---":
        return None, body, "missing", "o arquivo não começa com um bloco ---"
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == "---":
            raw = "\n".join(lines[start + 1 : index])
            remainder = "\n".join(lines[index + 1 :])
            return raw, remainder, "ok", ""
    return None, body, "unreadable", "bloco de frontmatter sem delimitador de fechamento"


_UNSUPPORTED_TOKENS = (
    ("&", "anchor"),
    ("*", "alias"),
    ("!", "tag"),
    ("|", "block_scalar"),
    (">", "block_scalar"),
    ("{", "flow_map"),
)


def read_frontmatter(text: str, backend: str = "auto") -> dict:
    """Read the frontmatter with a declared backend.

    ``pyyaml`` is used when it is importable and the caller allows it, which
    makes the reading a full YAML parse. The fallback is a deliberately
    restricted reader: it handles the flat keys and the shallow nesting this
    contract needs and records every construct it does not decide as an
    unsupported item (``SQA-027``) instead of guessing a value. Its output is
    therefore never presented as complete YAML validation.
    """
    raw, body, status, reason = split_frontmatter(text)
    result = {
        "status": status,
        "reason": reason,
        "backend": backend,
        "data": {},
        "unsupported": [],
        "top_level_keys": [],
        "body": body,
    }
    if status != "ok":
        return result
    if raw is None:  # pragma: no cover - split_frontmatter pairs status with raw
        return result

    chosen = backend
    if backend == "auto":
        chosen = "pyyaml" if _pyyaml_available() else "restricted"
    result["backend"] = chosen

    if chosen == "pyyaml":
        parsed = _parse_with_pyyaml(raw)
        if parsed["status"] == "ok":
            result["data"] = parsed["data"]
            result["top_level_keys"] = sorted(parsed["data"].keys())
        else:
            result["status"] = "unreadable"
            result["reason"] = parsed["reason"]
        return result

    if chosen == "restricted":
        parsed = _parse_restricted(raw)
        result["data"] = parsed["data"]
        result["unsupported"] = parsed["unsupported"]
        result["top_level_keys"] = parsed["top_level_keys"]
        return result

    raise UsageError(f"backend de frontmatter desconhecido: {backend}")


def _pyyaml_available() -> bool:
    try:
        import yaml  # noqa: F401
    except Exception:  # pragma: no cover - depends on the environment
        return False
    return True


def _parse_with_pyyaml(raw: str) -> dict:
    try:
        import yaml
    except Exception:  # pragma: no cover - guarded by _pyyaml_available
        return {"status": "unreadable", "reason": "pyyaml indisponível"}
    try:
        data = yaml.safe_load(raw)
    except Exception as exc:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return {"status": "unreadable", "reason": f"pyyaml: {first}"}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {"status": "unreadable", "reason": "frontmatter não é um mapa"}
    return {"status": "ok", "data": data}


def _parse_restricted(raw: str) -> dict:
    """Line-oriented reader with an explicitly limited grammar.

    Handles: ``key:`` mappings, plain and quoted scalars, inline ``[a, b]``
    lists, comment stripping outside quotes and nesting up to three levels.
    Records as unsupported: block scalars, anchors, aliases, tags, flow maps,
    tab indentation, duplicate keys, complex keys and deeper nesting.
    """
    data: dict = {}
    unsupported: list[dict] = []
    seen: set[tuple] = set()
    stack: list[tuple[int, str]] = []
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        line_no = index + 1
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent_text = line[: len(line) - len(line.lstrip())]
        if "\t" in indent_text:
            unsupported.append(
                {"line": line_no, "construct": "tab_indentation", "key": None}
            )
            continue
        indent = len(indent_text)
        stripped = line.strip()
        if ":" not in stripped:
            unsupported.append({"line": line_no, "construct": "scalar_line", "key": None})
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key.startswith("?") or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            unsupported.append(
                {"line": line_no, "construct": "complex_key", "key": key or None}
            )
            continue
        value = _strip_comment(value).strip()

        if value[:1] in ("|", ">"):
            unsupported.append(
                {"line": line_no, "construct": "block_scalar", "key": key}
            )
            _register(data, stack, indent, key, None, unsupported, line_no)
            continue
        for token, construct in _UNSUPPORTED_TOKENS:
            if value.startswith(token):
                unsupported.append(
                    {"line": line_no, "construct": construct, "key": key}
                )
                _register(data, stack, indent, key, None, unsupported, line_no)
                break
        else:
            if value == "":
                _register(data, stack, indent, key, {}, unsupported, line_no)
            elif value.startswith("[") and value.endswith("]"):
                items = [item.strip() for item in value[1:-1].split(",")]
                _register(
                    data,
                    stack,
                    indent,
                    key,
                    [_unquote(item) for item in items if item.strip()],
                    unsupported,
                    line_no,
                )
            else:
                _register(data, stack, indent, key, _unquote(value), unsupported, line_no)

        marker = (tuple(path for _, path in stack), key)
        if marker in seen:
            unsupported.append({"line": line_no, "construct": "duplicate_key", "key": key})
        seen.add(marker)
    return {
        "data": data,
        "unsupported": unsupported,
        "top_level_keys": sorted(data.keys()),
    }


def _strip_comment(value: str) -> str:
    quote = None
    for position, char in enumerate(value):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#" and position > 0 and value[position - 1] in " \t":
            return value[:position]
    return value


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _register(
    data: dict,
    stack: list,
    indent: int,
    key: str,
    value,
    unsupported: list | None = None,
    line_no: int | None = None,
) -> None:
    while stack and stack[-1][0] >= indent:
        stack.pop()
    if not stack:
        data[key] = value
        if isinstance(value, dict):
            stack.append((indent, key))
        return
    if len(stack) >= 3:
        # Deeper than the contract needs: keep the parent as a dict and record
        # the construct so the report can say the read was partial.
        parent = _container(data, stack)
        if isinstance(parent, dict):
            parent.setdefault(key, None)
        if unsupported is not None:
            unsupported.append(
                {"line": line_no, "construct": "deep_nesting", "key": key}
            )
        return
    parent = _container(data, stack)
    if isinstance(parent, dict):
        parent[key] = value
        if isinstance(value, dict):
            stack.append((indent, key))


def _container(data: dict, stack: list):
    node = data
    for _, key in stack:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def as_text(value) -> str:
    """Normalise a frontmatter value to text for comparison and display."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(as_text(item) for item in value)
    return str(value).strip()


def as_list(value) -> list[str]:
    """Normalise a frontmatter value to a list of texts."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def dig(data: dict, *path: str):
    """Walk a nested mapping, returning ``None`` when the path is absent."""
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


# ----------------------------------------------------------------------- findings


def finding(
    code: str,
    *,
    file: str,
    line: int | None,
    evidence: str,
    impact: str,
    recommendation: str,
    validation: str = "executed",
    severity: str | None = None,
    severity_justification: str,
    confidence: str = "confirmed",
    target: str | None = None,
) -> dict:
    """Build one finding in the documented minimal format.

    The format is fixed by ``references/finding-format.md``: identifier,
    category, justified severity, file/location, evidence, impact,
    recommendation and validation state. ``evidence`` must already be
    sanitised: privacy findings carry the class and the location, never the
    value.
    """
    check = by_code(code)
    if validation not in VALIDATION_STATES:
        raise UsageError(f"estado de validação inválido: {validation}")
    chosen = severity or check.default_severity
    if chosen not in SEVERITY_ORDER:
        raise UsageError(f"severidade inválida: {chosen}")
    if len(evidence) > MAX_EVIDENCE:
        evidence = evidence[: MAX_EVIDENCE - 3] + "..."
    location = f"{file}:{line}" if line else file
    return {
        "id": code,
        "check": check.title,
        "category": check.category,
        "method": check.method,
        "severity": chosen,
        "severity_justification": severity_justification,
        "file": file,
        "line": line,
        "location": location,
        "evidence": evidence,
        "impact": impact,
        "recommendation": recommendation,
        "validation": validation,
        "confidence": confidence,
        "needs_agent_confirmation": confidence == "signal",
        "target": target,
    }


def limit(code: str, *, file: str, line: int | None, reason: str, recommendation: str) -> dict:
    """One explicit limit: something the evidence cannot settle.

    A limit is neither an approval nor a defect, so it never changes the exit
    code. It is reported separately so a partially covered audit cannot be read
    as a clean one.
    """
    check = by_code(code)
    return {
        "id": code,
        "check": check.title,
        "category": check.category,
        "method": check.method,
        "severity": "info",
        "file": file,
        "line": line,
        "location": f"{file}:{line}" if line else file,
        "reason": reason,
        "recommendation": recommendation,
        "validation": "unverifiable",
        "target": None,
    }


def sort_findings(findings: list[dict]) -> list[dict]:
    """Worst first, then by code, then by location: stable and reviewable."""
    return sorted(
        findings,
        key=lambda item: (
            -SEVERITY_ORDER.get(item.get("severity", "info"), 0),
            item.get("id", ""),
            item.get("location", ""),
        ),
    )


def summarise(findings: list[dict]) -> dict:
    """Counts per severity plus the worst severity actually present."""
    counts = {severity: 0 for severity in SEVERITIES}
    for item in findings:
        counts[item.get("severity", "info")] = counts.get(item.get("severity", "info"), 0) + 1
    return {
        "total": len(findings),
        "by_severity": counts,
        "worst": worst_severity([item.get("severity") for item in findings]),
    }


# --------------------------------------------------------------------- rendering


def render_json(report: dict) -> str:
    """Stable JSON: sorted keys, UTF-8 text, trailing newline."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _tri_state(value) -> str:
    """Render a three-state integrity flag without collapsing ``None``."""
    if value is True:
        return "sim"
    if value is False:
        return "não"
    return "inconclusivo (conteúdo inacessível)"


def _cell(value) -> str:
    """Make a value safe inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    """Human report in PT-BR, ordered worst finding first."""
    lines: list[str] = []
    skill = report.get("skill", {})
    add = lines.append
    add(f"# Auditoria de qualidade: {skill.get('name') or skill.get('path')}")
    add("")
    add(f"- Alvo: `{skill.get('path')}`")
    add(f"- Versão declarada: {skill.get('version') or 'não declarada'}")
    add(f"- Plataformas declaradas: {', '.join(skill.get('platforms') or []) or 'nenhuma'}")
    add(f"- Backend de frontmatter: {report.get('frontmatter_backend')}")
    add(f"- Manifesto do alvo: {report['manifest']['file_count']} arquivos, "
        f"digest `{report['manifest']['digest'][:16]}...`")
    add(f"- Alvo inalterado durante a auditoria: "
        f"{_tri_state(report.get('target_unchanged'))}")
    if report.get("matches_reference") is not None:
        add(f"- Confere com o manifesto de referência: "
            f"{_tri_state(report.get('matches_reference'))}")
    unreadable = report.get("manifest_unreadable") or []
    if unreadable:
        listed = ", ".join(f"`{rel}`" for rel in unreadable)
        add(f"- Sem conteúdo legível (não comparável): {len(unreadable)} arquivo(s): {listed}")
    add(f"- Executado em: {report.get('generated_at')} (relógio: {report.get('clock')})")
    add("")

    summary = report.get("summary", {})
    add("## Resumo")
    add("")
    add("| Severidade | Achados |")
    add("| --- | --- |")
    for severity in reversed(SEVERITIES):
        add(f"| {severity} | {summary.get('by_severity', {}).get(severity, 0)} |")
    add(f"| **total** | **{summary.get('total', 0)}** |")
    add("")
    add(f"Pior severidade: {summary.get('worst') or 'nenhuma'}. "
        "Não existe nota numérica nem selo automático de segurança.")
    add("")

    findings = report.get("findings", [])
    add("## Achados")
    add("")
    if not findings:
        add("Nenhum achado estrutural no alvo, com a cobertura declarada abaixo.")
        add("")
    for item in findings:
        add(f"### {item['id']} ({item['severity']}) {item['check']}")
        add("")
        add(f"- Categoria: {item['category']} | Método: {item['method']} | "
            f"Confiança: {item['confidence']}")
        add(f"- Localização: `{item['location']}`")
        add(f"- Evidência: {item['evidence']}")
        add(f"- Impacto: {item['impact']}")
        add(f"- Recomendação: {item['recommendation']}")
        add(f"- Justificativa de severidade: {item['severity_justification']}")
        add(f"- Situação de validação: {item['validation']}")
        add("")

    limits = report.get("limits", [])
    add("## Itens não verificáveis")
    add("")
    if not limits:
        add("Nenhum item ficou fora da cobertura declarada.")
        add("")
    for item in limits:
        add(f"- `{item['location']}` {item['id']}: {item['reason']}")
        add(f"  - Recomendação: {item['recommendation']}")
    add("")

    coverage = report.get("coverage") or {}
    add("## Cobertura da varredura")
    add("")
    add(f"- Arquivos de texto lidos: {len(coverage.get('text_files') or [])} "
        f"de {coverage.get('entry_count', 0)} entradas no alvo")
    skipped = coverage.get("skipped") or []
    if skipped:
        add(f"- Arquivos presentes mas não lidos ({len(skipped)}):")
        for item in skipped:
            add(f"  - `{item['rel']}`: {item['reason']}")
    else:
        add("- Arquivos presentes mas não lidos: nenhum")
    excluded = coverage.get("excluded_suffixes") or []
    by_reason: dict[str, list[str]] = {}
    for item in excluded:
        by_reason.setdefault(item["reason"], []).append(item["rel"])
    if excluded:
        add(f"- Fora da leitura por sufixo ({len(excluded)}):")
        for reason in sorted(by_reason):
            listed = ", ".join(f"`{rel}`" for rel in by_reason[reason])
            add(f"  - {reason}: {listed}")
    else:
        add("- Fora da leitura por sufixo: nenhum")
    add("")

    add("## Verificações executadas")
    add("")
    add("| Código | Método | Situação | Motivo |")
    add("| --- | --- | --- | --- |")
    for row in report.get("checks_run", []):
        add(f"| {_cell(row.get('code'))} | {_cell(row.get('method'))} | "
            f"{_cell(row.get('status'))} | {_cell(row.get('reason'))} |")
    add("")

    reported = report.get("reported_only", [])
    add("## Resultados apenas relatados")
    add("")
    if not reported:
        add("Nenhuma alegação do alvo foi registrada como apenas relatada.")
        add("")
    for item in reported:
        add(f"- `{item['location']}` {item['id']}: {item['evidence']}")
    add("")

    add("## Limites declarados")
    add("")
    for item in report.get("declared_limits", []):
        add(f"- {item}")
    add("")

    add("## Checklist semântico (avaliação pelo agente)")
    add("")
    add("Estes itens não são decididos pelo script: o agente confirma, descarta ou "
        "registra como não verificável.")
    add("")
    for item in report.get("semantic_checklist", []):
        add(f"- {item['code']} ({item['default_severity']}) {item['title']}: {item['how']}")
    add("")
    return "\n".join(lines)


def catalogue_rows() -> list[dict]:
    """The catalogue as plain rows, for ``checks`` and for the report."""
    return [
        {
            "code": check.code,
            "title": check.title,
            "category": check.category,
            "method": check.method,
            "default_severity": check.default_severity,
            "summary": check.summary,
            "how": check.how,
            "limits": check.limits,
        }
        for check in CATALOGUE
    ]


def semantic_checklist() -> list[dict]:
    """Catalogue rows the agent has to judge, in catalogue order."""
    return [row for row in catalogue_rows() if row["method"] == "semantic"]


# ------------------------------------------------------------------------ output


def resolve_out(path: str, target_root: str) -> str:
    """Absolute output path, refusing anything inside the target.

    Comparison is case-insensitive and follows links, because on Windows the
    same directory is reachable through different spellings: comparing raw
    strings let ``--out`` point back inside the target.
    """
    out_abs = os.path.abspath(path)
    out_cmp = os.path.normcase(os.path.realpath(out_abs))
    target_cmp = os.path.normcase(os.path.realpath(os.path.abspath(target_root)))
    try:
        shared = os.path.commonpath([out_cmp, target_cmp])
    except ValueError:  # pragma: no cover - different drives on Windows
        shared = ""
    if shared == target_cmp:
        raise UsageError(
            "a saída não pode ficar dentro do alvo auditado: a auditoria é somente leitura"
        )
    return out_abs


def write_output(path: str, text: str) -> None:
    """Write the report, mapping I/O problems to ``OutputError``."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        raise OutputError(f"diretório de saída inexistente: {directory}")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise OutputError(f"não foi possível escrever {path}: {exc.strerror or exc}") from exc


def emit(stream, text: str) -> None:
    """Write to a stream, tolerating consoles that cannot encode the text."""
    try:
        stream.write(text)
    except UnicodeEncodeError:  # pragma: no cover - Windows console fallback
        stream.write(text.encode("utf-8", "replace").decode("ascii", "replace"))


def stderr(message: str) -> None:
    """Report a failure on stderr without polluting stdout."""
    sys.stderr.write(message.rstrip("\n") + "\n")


def validate_backend(value: str) -> str:
    """Accept only the documented frontmatter backends."""
    if value not in ("auto", "pyyaml", "restricted"):
        raise UsageError(f"backend inválido: {value}")
    return value


def validate_method(value: str) -> str:
    """Accept only the documented methods."""
    if value not in METHODS:
        raise UsageError(f"método inválido: {value}")
    return value


def check_metadata() -> dict:
    """Schema identifiers, so every report states what it conforms to."""
    return {"schema": SCHEMA_ID, "schema_version": SCHEMA_VERSION}
