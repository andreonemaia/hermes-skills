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
#: Reported as the location of a traversal failure whose own path is unknown.
#: A bare "." would claim the target root failed, which is a different fact.
UNKNOWN_PATH = "(caminho desconhecido)"
#: Reported when a traversal record names its directory but not why it failed.
#: Empty would read as "no reason", which is a different claim from "not stated".
UNKNOWN_REASON = "(motivo desconhecido)"

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
        # The optional inner quote covers a value that itself starts with a
        # quote (``password = "'synthAbc9!"``): without it the char class stops
        # at the first character and the value is never masked.
        re.compile(
            r"(?i)\b(?:password|passwd|senha|api[_-]?key|apikey|access[_-]?key|secret|token)"
            r"\b\s*[:=]\s*[\"']?(['\"]?[^\s\"',;]{8,})"
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
    (
        # Both separators: a Windows profile printed by Python or by git-bash
        # uses forward slashes as often as backslashes.
        "windows_user_path",
        "SQA-020",
        re.compile(r"(?i)(?<![\w./])[a-z]:[\\/]users[\\/]([^\\/\s\"'`]+)"),
    ),
    (
        # The same Windows profile reached through an MSYS/git-bash spelling,
        # which is how paths appear in this repository's own docs.
        "msys_user_path",
        "SQA-020",
        re.compile(r"(?i)(?<![\w./])/[a-z]/users/([^/\s\"'`]+)"),
    ),
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
            # The lookbehind keeps a version like ``v3.10.0.1`` from matching on
            # its tail: a dotted number that continues an earlier dotted number
            # is not an address.
            r"(?<![.\d])((?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)\b"
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


#: Text that replaces a value matching a privacy pattern.
SECRET_MASK = "<redacted>"


def redact(value: str) -> str:
    """Mask every value in ``value`` that matches a privacy pattern.

    Only the matched value is replaced; the key, the operator and the
    punctuation around it survive, so a masked field still shows *what kind* of
    value was there. No length is kept in the mask: the length is reported per
    class by SQA-019..SQA-022, which is where a reader needs it.

    This is the single masking primitive of the report: exported fields, the
    Markdown renderer and the evidence of a finding all call it, so no format
    can be masked in one output and leak in another.
    """
    text = value
    for _cls, _code, pattern in PRIVACY_PATTERNS:

        def _replace(match: re.Match) -> str:
            # A pattern whose group 1 can match empty would otherwise inject the
            # mask between every character: an empty value has nothing to hide.
            if not match.group(1):
                return match.group(0)
            return match.group(0).replace(match.group(1), SECRET_MASK)

        text = pattern.sub(_replace, text)
    return text


#: A whole call or subscript expression: ``marker.group(1)``, ``os.environ["X"]``,
#: ``get_token()``. Bracket characters alone are not evidence of code:
#: ``synthAbc9[xyz]!`` is a literal that happens to contain them, and it is not
#: a bare identifier followed by accessors, so it does not match.


def _is_identifier_start(char: str) -> bool:
    return char.isascii() and (char.isalpha() or char == "_")


def _is_identifier_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


def is_code_expression(value: str) -> bool:
    """True only for a value that is one complete call or subscript expression.

    The assignment pattern matches ``token = marker.group(1)`` as readily as a
    real secret. A value is source code when the *whole* value is an identifier
    followed by accessors: ``name(...)``, ``name.attr``, ``name[...]``, or a
    chain of them. A literal that merely contains a bracket or a parenthesis is
    a leaked value, not an expression, so ``synthAbc9[xyz]!`` stays a finding.

    Brackets are matched by scanning, not by a nested regular expression, so
    ``fmt(a(b(c(d))))`` is recognised at any depth. An accessor left open at
    the end of the value counts as code too: the assignment pattern stops at a
    quote, so ``os.environ["X"]`` reaches this function truncated as
    ``os.environ[``. Characters are only read: nothing is executed or evaluated.
    """
    text = value.strip()
    if not text or not _is_identifier_start(text[0]):
        return False
    index = 1
    while index < len(text) and _is_identifier_char(text[index]):
        index += 1
    accessors = 0
    while index < len(text):
        char = text[index]
        if char == ".":
            index += 1
            start = index
            while index < len(text) and _is_identifier_char(text[index]):
                index += 1
            if index == start:
                return False
            accessors += 1
            continue
        if char in "([":
            closer = ")" if char == "(" else "]"
            depth = 1
            index += 1
            while index < len(text) and depth:
                if text[index] == char:
                    depth += 1
                elif text[index] == closer:
                    depth -= 1
                index += 1
            if depth:
                # The value ended inside the accessor: truncated, not literal.
                return True
            accessors += 1
            continue
        return False
    return accessors > 0


def is_repeated_pattern(value: str) -> bool:
    """True when every digit of ``value`` is the same digit.

    ``000.000.000-00`` is how the documentation spells the CPF format; no real
    document has a single repeated digit.
    """
    digits = [char for char in value if char.isdigit()]
    return bool(digits) and len(set(digits)) == 1


def walk_target(root: str, errors: list[dict] | None = None) -> list[dict]:
    """Every entry under ``root`` except the excluded noise, sorted by path.

    Symlinks are reported but never followed, so a link pointing outside the
    target cannot pull foreign content into the audit.

    ``errors``, when given, receives one item per directory whose listing
    failed. A subtree that cannot be entered is a hole in the manifest, not an
    empty subtree: without the hole the report would describe a shorter tree as
    the whole target and claim stability over content it never saw. Permissions
    are never worked around, only declared.
    """
    root_abs = os.path.abspath(root)
    entries: list[dict] = []

    def _on_error(exc: OSError) -> None:
        if errors is None:
            return
        path = getattr(exc, "filename", None)
        try:
            rel = _rel(path, root_abs) if path else UNKNOWN_PATH
        except (OSError, ValueError):
            # A path outside the target (another drive, a broken mount) has no
            # relative form; the failure still has to be recorded, and its
            # location stays explicitly unknown instead of naming the root.
            rel = UNKNOWN_PATH
        errors.append({"rel": rel, "reason": type(exc).__name__})

    for current, dirnames, filenames in os.walk(
        root_abs, followlinks=False, onerror=_on_error
    ):
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


def text_files_with_exclusions(root: str, errors: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Text-file entries plus the files skipped only because of their suffix.

    The second list is what makes the scan boundary auditable: a file that is
    never decoded still appears in the report, with the reason, instead of
    disappearing from coverage. ``errors`` is passed through to
    :func:`walk_target` so a directory that could not be listed is declared
    instead of looking like an empty one.
    """
    out = []
    excluded = []
    for entry in walk_target(root, errors):
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
    # ``utf-8-sig`` first: it decodes BOM-less UTF-8 as well, and strips the
    # BOM when present, so no file silently keeps ``\ufeff`` in its text.
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
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

    ``walk_errors`` and ``complete`` are part of the evidence, not decoration.
    A manifest that could not list part of the target is shorter, not equal: it
    must not be read as proof that nothing changed inside the subtree it never
    reached. The manifest itself is a *technical* artifact and stays raw (paths
    and symlink destinations unmasked) so that two manifests can be compared
    exactly; only the report's copy of it is masked.
    """
    root_abs = os.path.abspath(root)
    entries: dict[str, dict] = {}
    walk_errors: list[dict] = []
    for entry in walk_target(root_abs, walk_errors):
        rel = entry["rel"]
        if entry["kind"] == "symlink":
            try:
                target = os.readlink(entry["abs"])
            except OSError:
                # ``None`` keeps "could not read the link" out of the value
                # space: two unknown targets never compare as equal.
                target = None
            entries[rel] = {"kind": "symlink", "target": target}
        elif entry["kind"] == "dir":
            entries[rel] = {"kind": "dir"}
        else:
            try:
                size = os.path.getsize(entry["abs"])
            except OSError:
                size = 0
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
    unreadable = unreadable_entries(entries)
    return {
        "root": os.path.basename(root_abs),
        "entry_count": len(entries),
        "file_count": sum(1 for v in entries.values() if v["kind"] == "file"),
        "unreadable": unreadable,
        "walk_errors": walk_errors,
        # ``complete`` answers one question only: could this manifest see every
        # directory of the target? A hole in the traversal or a file whose
        # content could not be read both make it false.
        "complete": not walk_errors and not unreadable,
        "digest": digest.hexdigest(),
        "entries": entries,
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_suffixes": list(EXCLUDED_SUFFIXES),
        # Declared on purpose: this artifact is not a publishable output. The
        # published report masks its copy; this one keeps raw paths so the
        # comparison stays exact.
        "private": True,
    }


def unreadable_entries(entries: dict) -> list[str]:
    """Relative paths whose content could not be read, sorted.

    A file whose content could not be hashed and a symlink whose target could
    not be resolved are both unknown values: they never prove equality.
    """
    return sorted(
        rel
        for rel, value in entries.items()
        if isinstance(value, dict)
        and (
            value.get("sha256", "") is None
            or (value.get("kind") == "symlink" and value.get("target", "") is None)
        )
    )


def normalize_hole(value: object) -> str:
    """Canonical form of a hole path, so prefix matching cannot be fooled.

    Manifests can be written by hand or by an older version, and ``privado/``
    would otherwise produce the prefix ``privado//`` and stop covering the
    entries below it. A failure that cannot name its directory becomes
    ``UNKNOWN_PATH``; anything that names no directory at all becomes ``"."``,
    because an unnamed failure leaves the whole tree unobserved.
    """
    rel = str(value or "").strip().replace("\\", "/")
    if rel == UNKNOWN_PATH:
        return UNKNOWN_PATH
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.rstrip("/")
    return rel or "."


def walk_error_record(item: object) -> dict:
    """One traversal-failure record in canonical form.

    This is the single boundary where the two accepted shapes meet: the dict
    the walker writes and the bare string a hand-written or older manifest may
    carry. Both become ``{"rel", "reason"}`` so every consumer (hole matching,
    diff aggregation, the report and its Markdown) reads one shape. The
    location keeps its canonical form, and a record that does not state why it
    failed says so, instead of pretending the reason is empty.
    """
    if isinstance(item, dict):
        rel, reason = item.get("rel"), item.get("reason")
    else:
        rel, reason = item, None
    text = str(reason).strip() if reason is not None else ""
    return {"rel": normalize_hole(rel), "reason": text or UNKNOWN_REASON}


def walk_error_records(manifest: dict) -> list[dict]:
    """Every traversal failure of one manifest, in canonical form.

    A list is the contract, but a hand-written manifest may carry the single
    record unwrapped. Reading a bare string as an iterable would walk it
    character by character and turn one hole into a nonsense hole at ``/``.
    """
    raw = manifest.get("walk_errors") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    return [walk_error_record(item) for item in raw]


def hole_paths(manifest: dict) -> list[str]:
    """Directories whose listing failed in one manifest, as relative paths.

    ``"."`` is the target root itself and ``UNKNOWN_PATH`` a failure whose
    location could not be named: both leave the whole tree unobserved. Every
    other hole leaves only the entries below it unobserved.
    """
    return [record["rel"] for record in walk_error_records(manifest)]


def hole_covers(holes: list[str], rel: str) -> bool:
    """Whether a traversal hole explains the absence of ``rel``.

    An entry missing from a manifest is only evidence that it is missing from
    the *tree* when the directory that should have listed it was actually read.
    A hole on the side that lacks the entry means the entry may simply not have
    been seen, so absence there proves nothing.
    """
    rel = normalize_hole(rel)
    for hole in holes:
        hole = normalize_hole(hole)
        if hole in (".", UNKNOWN_PATH):
            return True
        if rel == hole or rel.startswith(hole + "/"):
            return True
    return False


def diff_manifests(before: dict, after: dict) -> dict:
    """Added, removed, changed and *unverifiable* entries between two manifests.

    A file whose content could not be read is neither equal nor different: it
    lands in ``unverifiable`` and makes ``complete`` false. A traversal hole on
    either side does the same: the two manifests may agree only because both
    stopped at the same locked door, which is not evidence of stability.

    Absence is judged per side, because each side is the only witness for its
    own gaps. An entry present in ``after`` and absent from ``before`` is a
    proven addition only when ``before`` actually listed the directory that
    would hold it; otherwise it lands in ``unconfirmed_added``. Symmetrically,
    an entry present in ``before`` and absent from ``after`` is a proven removal
    only when ``after`` observed that directory; otherwise it lands in
    ``unconfirmed_removed``. ``identical`` only speaks about the entries that
    could be compared, and ``proven_same`` is the single flag both the
    reference comparison and the run comparison read before claiming agreement.
    """
    old, new = before.get("entries", {}), after.get("entries", {})
    holes_before = hole_paths(before)
    holes_after = hole_paths(after)
    added: list[str] = []
    unconfirmed_added: list[str] = []
    for rel in sorted(set(new) - set(old)):
        target = unconfirmed_added if hole_covers(holes_before, rel) else added
        target.append(rel)
    removed: list[str] = []
    unconfirmed_removed: list[str] = []
    for rel in sorted(set(old) - set(new)):
        target = unconfirmed_removed if hole_covers(holes_after, rel) else removed
        target.append(rel)
    unreadable = set(unreadable_entries(old)) | set(unreadable_entries(new))
    # The same hole seen on both passes is one hole, not two: a reader counting
    # entries must not conclude the target is worse than it is. Identity is the
    # directory and the failure kind, the same pair the report dedupes on.
    walk_errors: list[dict] = []
    seen_holes: set[tuple[str, str]] = set()
    for record in walk_error_records(before) + walk_error_records(after):
        key = (record["rel"], record["reason"])
        if key in seen_holes:
            continue
        seen_holes.add(key)
        walk_errors.append(record)
    walk_errors.sort(key=lambda item: (item["rel"], item["reason"]))
    changed = sorted(
        rel
        for rel in set(old) & set(new)
        if old[rel] != new[rel] and rel not in unreadable
    )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unconfirmed_added": unconfirmed_added,
        "unconfirmed_removed": unconfirmed_removed,
        "unverifiable": sorted(unreadable),
        "walk_errors": walk_errors,
        "holes_before": sorted(set(holes_before)),
        "holes_after": sorted(set(holes_after)),
        "identical": not (added or removed or changed),
        # Agreement that covers the whole tree: no proven difference, no entry
        # whose absence a hole could explain, and no content left unhashed.
        "proven_same": not (
            added
            or removed
            or changed
            or unconfirmed_added
            or unconfirmed_removed
            or unreadable
            or walk_errors
        ),
        "complete": not unreadable
        and not walk_errors
        and not (added or removed or unconfirmed_added or unconfirmed_removed),
        "digest_before": before.get("digest"),
        "digest_after": after.get("digest"),
    }


def proven_change(diff: dict) -> bool:
    """Whether a manifest comparison observed a difference it can prove.

    Only entries read on both sides (``changed``) or entries whose absence the
    observing side actually witnessed (``added``/``removed``) count. Both the
    reference comparison and the run comparison ask this one function, so the
    two paths cannot disagree about what a proven change is.
    """
    return bool(diff.get("changed") or diff.get("added") or diff.get("removed"))


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
    entries = data["entries"]
    if not isinstance(entries, dict) or not all(
        isinstance(item, dict) for item in entries.values()
    ):
        # A malformed manifest is a caller error, not an auditor crash: reading
        # it as-is turns ``set(entries)`` into TypeError and exits 6 with no
        # report, which is the failure mode this boundary exists to prevent.
        raise UsageError(
            f"manifesto inválido: 'entries' deve ser um objeto de objetos: {path}"
        )
    raw_errors = data.get("walk_errors")
    if isinstance(raw_errors, (str, dict)):
        # Same normalization the readers apply: one unwrapped record is one
        # hole, not a string walked character by character.
        raw_errors = [raw_errors]
        data["walk_errors"] = raw_errors
    if raw_errors is not None and (
        not isinstance(raw_errors, list)
        or not all(isinstance(item, (str, dict)) for item in raw_errors)
    ):
        raise UsageError(
            f"manifesto inválido: 'walk_errors' deve ser uma lista de objetos ou textos: {path}"
        )
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

        # The parent chain is what the stack will be after ``_register`` pops
        # the levels at or below this indent. Computing it *before* the call
        # keeps this key out of its own path, so a child that repeats its
        # parent's name (``name:`` then ``  name: x``) is not a duplicate.
        marker = (tuple(path for level, path in stack if level < indent), key)
        if marker in seen:
            unsupported.append({"line": line_no, "construct": "duplicate_key", "key": key})
        seen.add(marker)

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
            elif value.startswith("["):
                items, closed = _split_flow_items(value)
                if not closed:
                    unsupported.append(
                        {"line": line_no, "construct": "malformed_flow", "key": key}
                    )
                    _register(data, stack, indent, key, None, unsupported, line_no)
                elif any(item[:1] in ("[", "{") for item in items):
                    # Nested flow collections are outside the restricted grammar.
                    unsupported.append(
                        {"line": line_no, "construct": "flow_nesting", "key": key}
                    )
                    _register(data, stack, indent, key, None, unsupported, line_no)
                else:
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


def _split_flow_items(value: str) -> tuple[list[str], bool]:
    """Split ``[a, b]`` on commas that are outside quotes.

    Returns the raw items and whether the closing bracket was found. A comma
    inside quotes is data, not a separator: ``["win, linux"]`` is one item.
    """
    body = value[1:]
    items: list[str] = []
    current: list[str] = []
    quote = None
    escaped = False
    for position, char in enumerate(body):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            current.append(char)
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char == ",":
            items.append("".join(current).strip())
            current = []
            continue
        if char == "]":
            rest = body[position + 1 :].strip()
            if rest and not rest.startswith("#"):
                # Trailing content after the list: not the restricted grammar.
                return items, False
            items.append("".join(current).strip())
            return [item for item in items if item], True
        current.append(char)
    return items, False


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


#: Sections of the report made of records that quote the audited target. Every
#: string in them is masked, keys included: a field this tool writes itself can
#: still interpolate a value read from the target (``alinhar o README à versão
#: <version>``), so listing field names one by one leaves the next such field
#: free to leak. The tool's own vocabulary in these records (codes, severities,
#: titles) carries no privacy-shaped text, so masking it changes nothing.
MASKED_RECORDS = ("findings", "reported_only", "limits", "checks_run")

#: Sections masked in full: every string inside them, keys included.
MASKED_TREES = ("skill", "frontmatter", "coverage")


def _mask_text(node):
    """Mask every string in ``node``, dictionary keys included."""
    if isinstance(node, str):
        return redact(node)
    if isinstance(node, (list, tuple, set)):
        # A hand-written manifest may carry a tuple or a set; mask every item
        # instead of letting the raw value through.
        return [_mask_text(item) for item in node]
    if isinstance(node, dict):
        return {redact(str(key)): _mask_text(value) for key, value in node.items()}
    return node


def _mask_entry_map(entries: dict) -> dict:
    """Mask entry paths, keeping one entry per raw path.

    Two paths that differ only inside a masked value collapse into the same
    key. The second one keeps its place under a numeric suffix, so the published
    copy of the manifest never silently loses an entry.
    """
    out: dict = {}
    for rel, value in entries.items():
        key = redact(str(rel))
        if key in out:
            suffix = 2
            while f"{key}#{suffix}" in out:
                suffix += 1
            key = f"{key}#{suffix}"
        out[key] = _mask_text(value)
    return out


def _mask_diff(diff: dict) -> dict:
    """Redact the paths of a manifest comparison.

    Shared by the reference comparison and the run comparison: both carry the
    same path lists, and masking only one would leak the other.
    """
    out = dict(diff)
    for key in (
        "added",
        "removed",
        "changed",
        "unconfirmed_added",
        "unconfirmed_removed",
        "unverifiable",
        "holes_before",
        "holes_after",
    ):
        out[key] = [redact(str(item)) for item in out.get(key) or []]
    out["walk_errors"] = _mask_text(out.get("walk_errors") or [])
    return out


def publishable(report: dict) -> dict:
    """Copy of ``report`` with every target-derived string masked.

    The report mixes two kinds of text: the tool's own vocabulary (check titles,
    declared limits, the recommendations it wrote itself) and text read from the
    target (frontmatter values, file names, symlink destinations, evidence, and
    the reasons that quote a value). The second kind is what makes a raw report
    unsafe to attach: a description, a version string or a symlink destination
    can hold a credential, an address or a private host even when no privacy
    finding fired.

    Masking happens here, at export time, after every comparison and digest was
    computed on raw values, so the integrity check keeps its exactness while the
    exported report carries the mask. It is **not** a promise that the report is
    safe to attach: only the formats in ``PRIVACY_PATTERNS`` are detected, so
    free-form text and private names without a recognisable format survive
    untouched. The report states that limit in its own ``publication`` block.

    The technical manifest written by ``manifest --out`` is a different
    artifact: it stays raw by design and is declared private, because masking it
    would break the exact comparison it exists for.
    """
    out = dict(report)
    for section in MASKED_TREES:
        if isinstance(out.get(section), (dict, list)):
            out[section] = _mask_text(out[section])
    if out.get("manifest_unreadable"):
        out["manifest_unreadable"] = [redact(str(rel)) for rel in out["manifest_unreadable"]]
    manifest = out.get("manifest")
    if isinstance(manifest, dict):
        manifest = dict(manifest)
        manifest["root"] = redact(str(manifest.get("root", "")))
        manifest["unreadable"] = [redact(str(rel)) for rel in manifest.get("unreadable") or []]
        manifest["walk_errors"] = _mask_text(manifest.get("walk_errors") or [])
        manifest["entries"] = _mask_entry_map(manifest.get("entries") or {})
        out["manifest"] = manifest
    diff = out.get("manifest_diff")
    if isinstance(diff, dict):
        out["manifest_diff"] = _mask_diff(diff)
    reference_diff = out.get("reference_diff")
    if isinstance(reference_diff, dict):
        out["reference_diff"] = _mask_diff(reference_diff)
    for section in MASKED_RECORDS:
        if isinstance(out.get(section), list):
            out[section] = [_mask_text(item) for item in out[section]]
    return out


def publication_policy() -> dict:
    """Which artifact of this tool may be published, and under which limit.

    Two artifacts leave the auditor and they are not the same kind of file:

    * the **report** (JSON and Markdown, ``audit``) is the one meant to be
      shared. Every field read from the target is masked at export;
    * the **manifest** (``manifest --out``) is a technical artifact, kept raw so
      two manifests compare exactly. It declares itself ``private`` and must not
      be attached to a PR, an issue or a chat.

    Masking is not a safety guarantee: only the formats in
    ``PRIVACY_PATTERNS`` are recognised, so free-form prose, private names and
    paths without a credential shape survive. The block is exported so the
    report states its own status instead of relying on prose elsewhere.
    """
    return {
        "report_masked": True,
        "report_safe_to_attach": False,
        "report_limit": (
            "campos lidos do alvo são mascarados no JSON e no Markdown, mas apenas "
            "para formatos reconhecidos (credencial, e-mail, URL, host, IP, caminho "
            "pessoal); texto livre, nomes privados sem formato e caminhos relativos "
            "do alvo permanecem como estão, então o relatório exige revisão antes "
            "de ser publicado"
        ),
        "manifest_raw": True,
        "manifest_private": (
            "o manifesto escrito por 'manifest --out' é técnico e não mascarado, "
            "para que a comparação de integridade seja exata; não é publicável"
        ),
        "masking_primitive": "redact",
    }


def render_json(report: dict, *, mask: bool = True) -> str:
    """Stable JSON: sorted keys, UTF-8 text, trailing newline.

    ``mask`` is on by default, so the exported report is the masked one even if
    a caller hands over a raw report. Only the technical manifest is exported
    unmasked, and it says so in its own ``private`` field.
    """
    payload = publishable(report) if mask else report
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _tri_state(value) -> str:
    """Render a three-state integrity flag without collapsing ``None``."""
    if value is True:
        return "sim"
    if value is False:
        return "não"
    return "inconclusivo (cobertura incompleta)"


def _cell(value) -> str:
    """Make a value safe inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict, *, mask: bool = True) -> str:
    """Human report in PT-BR, ordered worst finding first.

    Masked by default for the same reason as :func:`render_json`: this is the
    text people paste into a review, so it must not reproduce a raw value that
    the JSON masks.
    """
    if mask:
        report = publishable(report)
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
        f"digest `{report['manifest']['digest'][:16]}...`"
        + ("" if report["manifest"].get("complete", True) else " (manifesto incompleto)"))
    add(f"- Alvo inalterado durante a auditoria: "
        f"{_tri_state(report.get('target_unchanged'))}")
    if report.get("reference_compared"):
        add(f"- Confere com o manifesto de referência: "
            f"{_tri_state(report.get('matches_reference'))}")
    else:
        add("- Confere com o manifesto de referência: não comparado "
            "(sem `--check-unchanged`)")
    reference_diff = report.get("reference_diff") or {}
    if report.get("reference_compared") and reference_diff.get("walk_errors"):
        listed = ", ".join(
            f"`{item['rel']}` ({item['reason']})"
            for item in reference_diff["walk_errors"]
        )
        add(
            f"- Referência com diretórios não listados: "
            f"{len(reference_diff['walk_errors'])}: {listed}"
        )
        add(
            "- A comparação com a referência é inconclusiva sob esses diretórios: "
            "ausência de uma entrada ali não prova remoção."
        )
    if report.get("reference_compared") and reference_diff.get("unverifiable"):
        listed = ", ".join(f"`{rel}`" for rel in reference_diff["unverifiable"])
        add(f"- Sem conteúdo legível para comparar com a referência: {listed}")
    unreadable = report.get("manifest_unreadable") or []
    if unreadable:
        listed = ", ".join(f"`{rel}`" for rel in unreadable)
        add(f"- Sem conteúdo legível (não comparável): {len(unreadable)} arquivo(s): {listed}")
    walk_errors = (report.get("manifest") or {}).get("walk_errors") or []
    if walk_errors:
        listed = ", ".join(f"`{item['rel']}` ({item['reason']})" for item in walk_errors)
        add(f"- Diretórios não listados (travessia falhou): {len(walk_errors)}: {listed}")
        add("- A cobertura está incompleta: os arquivos sob esses diretórios não "
            "foram lidos nem comparados.")
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
    symlinks = coverage.get("symlinks") or []
    if symlinks:
        add(f"- Links simbólicos (não seguidos, não lidos) ({len(symlinks)}):")
        for rel in symlinks:
            add(f"  - `{rel}`")
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
    traversal = coverage.get("traversal_errors") or []
    if traversal:
        add(f"- Diretórios com travessia falha ({len(traversal)}):")
        for item in traversal:
            add(f"  - `{item['rel']}`: {item['reason']}")
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

    publication = report.get("publication") or {}
    if publication:
        add("## Publicação deste relatório")
        add("")
        add("- Campos lidos do alvo (frontmatter, caminhos, destinos de link, "
            "evidência, motivos que citam valores) são mascarados neste Markdown "
            "e no JSON, pelo mesmo primitivo.")
        add("- Pronto para anexar sem revisão: **não**. "
            f"{publication.get('report_limit', '')}")
        add(f"- O manifesto técnico de `manifest --out` continua bruto e privado: "
            f"{publication.get('manifest_private', '')}")
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


def resolve_out(path: str, target_root: str, protected: str | None = None) -> str:
    """Absolute output path, refusing anything inside the target.

    Comparison is case-insensitive and follows links, because on Windows the
    same directory is reachable through different spellings: comparing raw
    strings let ``--out`` point back inside the target. ``protected`` is an
    input the run must not destroy, such as the reference manifest: it lives
    outside the target, so the target check alone would let the report
    overwrite the very file the comparison is being made against.
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
    if protected is not None:
        protected_cmp = os.path.normcase(os.path.realpath(os.path.abspath(protected)))
        if out_cmp == protected_cmp:
            raise UsageError(
                "a saída não pode sobrescrever o manifesto de referência"
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
