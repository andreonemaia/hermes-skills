"""Shared helpers for the comfyui-model-manager scripts.

Standard library only, so the scripts run with whatever Python ships inside a
ComfyUI install. Everything here is importable: the test suite exercises these
functions directly instead of spawning subprocesses.

Nothing in this module writes to disk on import, and nothing resolves a path
relative to the machine it was written on.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8188"
CHUNK_SIZE = 1024 * 1024

HEX_DIGITS = frozenset("0123456789abcdef")


class ComfyModelManagerError(Exception):
    """Base class for expected failures.

    ``exit_code`` lets the CLIs map a failure to a distinct process status so
    callers can branch on it without parsing text.
    """

    exit_code = 1


class ApiError(ComfyModelManagerError):
    """The ComfyUI HTTP API was unreachable or answered with invalid data."""

    exit_code = 3


class ManifestError(ComfyModelManagerError):
    """A model manifest is missing, malformed, or internally inconsistent."""

    exit_code = 4


class IntegrityError(ComfyModelManagerError):
    """A downloaded or existing file does not match its expected digest."""

    exit_code = 5


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def resolve_base_url(explicit=None, env=None):
    """Return the API base URL, preferring an explicit value over the env var."""
    env = os.environ if env is None else env
    value = explicit or env.get("COMFYUI_BASE_URL") or DEFAULT_BASE_URL
    return value.rstrip("/")


def http_get_json(base_url, path, timeout=10.0, opener=urllib.request.urlopen):
    """GET ``base_url + path`` and decode the JSON body.

    Any transport or decoding problem surfaces as :class:`ApiError` so callers
    never have to distinguish urllib exception types.
    """
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ApiError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:  # connection refused, timeout, DNS failure
        raise ApiError(f"cannot reach {url}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(f"invalid JSON from {url}: {exc}") from exc


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------


def sha256_file(path, chunk_size=CHUNK_SIZE):
    """Stream ``path`` and return its lowercase SHA-256 hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def human_bytes(count):
    """Format a byte count with a binary unit suffix."""
    if count is None:
        return "unknown"
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TiB"


def safe_child_path(root, relative):
    """Resolve ``relative`` inside ``root``, refusing anything that escapes.

    Rejects absolute paths, drive-relative paths (``C:file``), UNC roots,
    embedded control characters (a NUL byte reaches ``open`` as a ``ValueError``
    on some builds and truncates the name on others), anything whose normalised
    form climbs above ``root``, and anything that resolves to ``root`` itself
    rather than a child of it. This is the single choke point for every path
    that comes from a manifest or a user argument.

    The strictness matters: a manifest entry of ``folder="checkpoints",
    filename=".."`` resolves to the root, so it would pass a plain containment
    test and let ``<file>.part`` be written *beside* the model directory.

    Both sides are resolved with ``realpath`` before the comparison, so a
    symlink or a Windows junction *inside* the root that points outside it is
    rejected instead of letting the write land elsewhere. The returned path is
    the resolved one: callers must write to it, and measure free space on it.
    """
    if not isinstance(relative, str) or not relative.strip():
        raise ComfyModelManagerError("path must be a non-empty string")
    if any(character < " " for character in relative):
        raise ComfyModelManagerError(
            f"path must not contain control characters: {relative!r}"
        )
    if os.path.splitdrive(relative)[0]:
        raise ComfyModelManagerError(f"path must not carry a drive letter: {relative!r}")
    if os.path.isabs(relative) or relative.startswith(("/", "\\")):
        raise ComfyModelManagerError(f"path must be relative: {relative!r}")
    root_abs = os.path.realpath(root)
    try:
        candidate = os.path.realpath(os.path.abspath(os.path.join(root_abs, relative)))
        inside = os.path.normcase(os.path.commonpath([candidate, root_abs])) == os.path.normcase(
            root_abs
        )
    except (OSError, ValueError):  # unmappable path, or mixed relative/absolute
        raise ComfyModelManagerError(f"path cannot be resolved inside its root: {relative!r}")
    if not inside:
        raise ComfyModelManagerError(f"path escapes its root: {relative!r}")
    if os.path.normcase(candidate) == os.path.normcase(root_abs):
        raise ComfyModelManagerError(
            f"path resolves to its root instead of a child of it: {relative!r}"
        )
    return candidate


def nearest_existing(path):
    """Walk up from ``path`` until an existing directory is found.

    The path is resolved first, so the answer describes the volume the file
    will really land on when a component of the path is a link or a junction.
    """
    current = os.path.realpath(path)
    while True:
        if os.path.isdir(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def free_bytes(path):
    """Free bytes on the volume holding ``path`` (or its nearest ancestor)."""
    anchor = nearest_existing(path)
    if anchor is None:
        return None
    try:
        return shutil.disk_usage(anchor).free
    except OSError:
        return None


def min_free_bytes(paths):
    """Smallest free space across the volumes holding ``paths``.

    A model folder may be a link to another volume, so the space that matters
    is the space where each file actually lands, not the space of the root.
    Returns ``None`` when no volume could be measured.
    """
    values = [value for value in (free_bytes(path) for path in paths) if value is not None]
    return min(values) if values else None


# --------------------------------------------------------------------------
# Model-path configuration (the flat YAML subset ComfyUI actually uses)
# --------------------------------------------------------------------------


def unquote(value):
    """Strip one matching pair of surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_model_paths_yaml(text):
    """Parse the flat YAML subset used by ComfyUI model-path configs.

    Handles full-line comments, blank lines, ``section:`` headers at zero
    indent, ``key: value`` pairs, quoted scalars, and ``|`` / ``|-`` block
    scalars holding one path per line. Nested maps, anchors, flow collections,
    inline comments and multi-document streams are deliberately unsupported;
    ComfyUI's ``extra_model_paths.yaml`` and the Comfy Desktop instance files
    use none of them.

    Returns ``{section: {"base_path": str, "is_default": bool,
    "folders": {folder: [subpath, ...]}}}``.
    """
    sections = {}
    current = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if indent == 0:
            if not stripped.endswith(":"):
                current = None
                continue
            name = unquote(stripped[:-1].strip())
            current = sections.setdefault(
                name, {"base_path": "", "is_default": False, "folders": {}}
            )
            continue
        if current is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = unquote(key.strip())
        value = value.strip()
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            block = []
            while index < len(lines):
                following = lines[index]
                if not following.strip():
                    index += 1
                    continue
                following_indent = len(following) - len(following.lstrip(" \t"))
                if following_indent <= indent:
                    break
                block.append(following.strip())
                index += 1
            values = block
        else:
            values = [unquote(value)] if value else []
        if key == "base_path":
            current["base_path"] = values[0] if values else ""
        elif key == "is_default":
            current["is_default"] = bool(values) and values[0].lower() in ("true", "yes", "1")
        else:
            current["folders"][key] = [item for item in values if item]
    return sections


def resolve_config_paths(base, subpaths):
    """Resolve config subpaths against a base directory, keeping absolutes."""
    resolved = []
    for subpath in subpaths:
        if os.path.isabs(subpath):
            resolved.append(os.path.normpath(subpath))
        else:
            resolved.append(os.path.normpath(os.path.join(base, subpath)))
    return resolved


def model_roots_from_config(config_path, base_dir=None):
    """Read a model-path config and return normalised root descriptors.

    ``base_dir`` is the ComfyUI install directory used to resolve relative
    ``base_path`` values; it defaults to the config file's own directory.
    """
    with open(config_path, "r", encoding="utf-8") as handle:
        sections = parse_model_paths_yaml(handle.read())
    fallback = base_dir or os.path.dirname(os.path.abspath(config_path))
    roots = []
    for name, section in sections.items():
        base = section.get("base_path") or fallback
        if not os.path.isabs(base):
            base = os.path.join(fallback, base)
        base = os.path.normpath(base)
        folders = {
            folder: resolve_config_paths(base, subpaths)
            for folder, subpaths in section.get("folders", {}).items()
        }
        roots.append(
            {
                "section": name,
                "base_path": base,
                "is_default": bool(section.get("is_default")),
                "folders": folders,
            }
        )
    return roots


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


def is_sha256(value):
    """True when ``value`` looks like a 64-character hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in HEX_DIGITS for char in value.lower())
    )


def load_manifest(path):
    """Load and validate a model manifest, returning normalised entries.

    Accepts either a bare array or an object with a ``models`` array. Every
    entry must carry ``url``, ``folder``, ``filename`` and ``sha256``; a
    missing digest is a hard error because an unverifiable download cannot be
    distinguished from a corrupted one.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: invalid JSON: {exc}") from exc

    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("models")
    else:
        entries = None
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"{path}: expected a non-empty 'models' array")

    normalised = []
    destinations = {}
    for index, raw in enumerate(entries):
        where = f"{path}: models[{index}]"
        if not isinstance(raw, dict):
            raise ManifestError(f"{where}: expected an object")
        entry = {}
        for key in ("url", "folder", "filename"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"{where}: missing required string field '{key}'")
            entry[key] = value.strip()
        if not is_sha256(raw.get("sha256")):
            raise ManifestError(f"{where}: 'sha256' must be a 64-character hex digest")
        entry["sha256"] = raw["sha256"].lower()
        size = raw.get("size")
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ManifestError(f"{where}: 'size' must be a non-negative integer")
        entry["size"] = size
        entry["name"] = str(raw.get("name") or entry["filename"])
        entry["source"] = str(raw.get("source") or "")
        # A manifest is untrusted input: prove the destination stays inside
        # the model folder before anything touches the filesystem.
        # ``safe_child_path`` raises the base class; translate it here, because
        # the manifest is what is wrong and the CLI maps that to exit 4. Left
        # untranslated it escaped ``main`` as a traceback with exit 1.
        try:
            safe_child_path(".", os.path.join(entry["folder"], entry["filename"]))
        except ComfyModelManagerError as exc:
            raise ManifestError(f"{where}: {exc}") from exc
        # Two entries resolving to the same destination would silently
        # overwrite each other, and "the last one wins" would never surface.
        key = os.path.normcase(
            os.path.normpath(os.path.join(entry["folder"], entry["filename"]))
        )
        if key in destinations:
            raise ManifestError(
                f"{where}: destination '{entry['folder']}/{entry['filename']}' is "
                f"already defined by {destinations[key]}"
            )
        destinations[key] = where
        normalised.append(entry)
    return normalised
