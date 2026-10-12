#!/usr/bin/env python3
"""Inventory, verify and (with explicit consent) fetch ComfyUI model files.

Every mutating path defaults to a dry run. Downloads are written to a
``.part`` sidecar and only moved into place after the SHA-256 digest matches,
so an interrupted transfer can always be resumed and never leaves a truncated
model behind a valid-looking filename.

Usage:
    python comfy_models.py inventory --models-dir DIR [--hash] [--json]
    python comfy_models.py verify    --manifest FILE --models-dir DIR [--json]
    python comfy_models.py install   --manifest FILE --models-dir DIR [--yes]

Exit codes: 0 clean, 1 no model directory, 4 bad manifest, 5 integrity mismatch
or a failed transfer, 6 an existing file was refused.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (  # noqa: E402
    ComfyModelManagerError,
    IntegrityError,
    ManifestError,
    min_free_bytes,
    safe_child_path,
    sha256_file,
    free_bytes,
    human_bytes,
    load_manifest,
)

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_PARTIAL = "partial"
STATUS_CONFLICT = "conflict"


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


def scan_folder(root, folder, subpaths=None):
    """List model files under ``root`` for a single logical folder."""
    entries = []
    candidates = subpaths if subpaths else [os.path.join(root, folder)]
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        for current, _dirnames, filenames in os.walk(directory):
            for filename in sorted(filenames):
                if filename.endswith(".part") or filename.startswith("."):
                    continue
                path = os.path.join(current, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                entries.append(
                    {
                        "folder": folder,
                        "name": filename,
                        "path": path,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
    return entries


def inventory(model_roots, folders=None, with_hash=False):
    """Collect model files from every root, optionally hashing each one."""
    found = []
    for root in model_roots:
        base = root["base_path"] if isinstance(root, dict) else root
        configured = root.get("folders", {}) if isinstance(root, dict) else {}
        if configured:
            targets = [(name, paths) for name, paths in configured.items()]
        else:
            targets = []
            if os.path.isdir(base):
                for name in sorted(os.listdir(base)):
                    path = os.path.join(base, name)
                    if os.path.isdir(path) and not name.startswith("."):
                        targets.append((name, [path]))
        for folder, subpaths in targets:
            if folders and folder not in folders:
                continue
            for entry in scan_folder(base, folder, subpaths):
                entry["root"] = base
                found.append(entry)
    if with_hash:
        for entry in found:
            try:
                entry["sha256"] = sha256_file(entry["path"])
            except OSError as exc:
                entry["sha256"] = None
                entry["error"] = str(exc)
    return found


def find_duplicates(entries):
    """Group entries that share a content digest, or the same name and size."""
    by_hash = {}
    by_name_size = {}
    for entry in entries:
        digest = entry.get("sha256")
        if digest:
            by_hash.setdefault(digest, []).append(entry)
        else:
            key = (entry["folder"], entry["name"].lower(), entry["size"])
            by_name_size.setdefault(key, []).append(entry)
    groups = []
    for digest, members in sorted(by_hash.items()):
        if len(members) > 1:
            groups.append({"reason": "identical-sha256", "sha256": digest, "paths": [m["path"] for m in members]})
    for (folder, name, size), members in sorted(by_name_size.items()):
        if len(members) > 1:
            groups.append(
                {
                    "reason": "same-name-and-size",
                    "folder": folder,
                    "name": name,
                    "size": size,
                    "paths": [m["path"] for m in members],
                }
            )
    return groups


# --------------------------------------------------------------------------
# Verification and planning
# --------------------------------------------------------------------------


def entry_destination(dest_root, entry):
    """Absolute destination for a manifest entry, proven to stay in ``dest_root``."""
    return safe_child_path(dest_root, os.path.join(entry["folder"], entry["filename"]))


def classify_destination(dest, entry, hasher=sha256_file):
    """Decide what, if anything, needs to happen for one manifest entry."""
    part = dest + ".part"
    if os.path.isdir(dest) or os.path.isdir(part):
        # A directory occupying the destination (or the .part sidecar) cannot
        # be replaced by a file. Reporting it as "missing" in the dry run hid
        # the truth until the transfer failed.
        blocked = dest if os.path.isdir(dest) else part
        return {"status": STATUS_CONFLICT, "detail": f"a directory occupies {blocked}",
                "size": None, "sha256": None, "part_size": 0}
    if os.path.isfile(dest):
        try:
            size = os.path.getsize(dest)
        except OSError as exc:
            return {"status": STATUS_CONFLICT, "detail": f"cannot stat: {exc}", "size": None,
                    "sha256": None, "part_size": os.path.getsize(part) if os.path.isfile(part) else 0}
        if entry.get("size") is not None and size != entry["size"]:
            return {"status": STATUS_CONFLICT, "detail": f"size {size} != expected {entry['size']}",
                    "size": size, "sha256": None,
                    "part_size": os.path.getsize(part) if os.path.isfile(part) else 0}
        try:
            digest = hasher(dest)
        except OSError as exc:
            return {"status": STATUS_CONFLICT, "detail": f"cannot hash: {exc}", "size": size,
                    "sha256": None, "part_size": os.path.getsize(part) if os.path.isfile(part) else 0}
        if digest == entry["sha256"]:
            return {"status": STATUS_OK, "detail": "digest matches", "size": size, "sha256": digest,
                    "part_size": os.path.getsize(part) if os.path.isfile(part) else 0}
        return {"status": STATUS_CONFLICT, "detail": f"sha256 {digest} != expected {entry['sha256']}",
                "size": size, "sha256": digest,
                "part_size": os.path.getsize(part) if os.path.isfile(part) else 0}
    if os.path.isfile(part):
        try:
            part_size = os.path.getsize(part)
        except OSError:
            part_size = 0
        return {"status": STATUS_PARTIAL, "detail": f"resumable partial file ({human_bytes(part_size)})",
                "size": None, "sha256": None, "part_size": part_size}
    return {"status": STATUS_MISSING, "detail": "not present", "size": None, "sha256": None, "part_size": 0}


def conflict_remedy(detail):
    """The next step for a refused entry, derived from what the conflict is.

    ``--allow-overwrite`` replaces a *file*; it cannot remove a directory that
    occupies the destination, so naming the flag for that case pointed the
    reader at a fix that could never work.
    """
    if detail.startswith("a directory occupies"):
        return "remove the directory, then re-run"
    return "pass --allow-overwrite to replace it"


def plan_manifest(entries, dest_root, hasher=sha256_file):
    """Per-entry status plus the download bytes still required."""
    rows = []
    needed = 0
    for entry in entries:
        dest = entry_destination(dest_root, entry)
        outcome = classify_destination(dest, entry, hasher=hasher)
        row = dict(entry)
        row["destination"] = dest
        # Merge the observation under its own keys: ``size`` and ``sha256``
        # must keep the *expected* values from the manifest, because
        # install_manifest uses them to verify what it downloads. Overwriting
        # them with the observed (often None) values silently disabled both
        # the size and the digest check.
        row["status"] = outcome["status"]
        row["detail"] = outcome["detail"]
        row["part_size"] = outcome.get("part_size", 0)
        row["actual_size"] = outcome.get("size")
        row["actual_sha256"] = outcome.get("sha256")
        if row["status"] != STATUS_OK:
            expected = entry.get("size")
            if expected is not None:
                part_size = row.get("part_size", 0)
                # A complete or oversized .part is rewritten from scratch, so
                # it never counts as negative remaining work.
                needed += expected if part_size >= expected else expected - part_size
        rows.append(row)
    return rows, needed


def unknown_size_rows(rows):
    """Rows that still need bytes but carry no expected size.

    They are counted as zero bytes in the plan, so the free-space check is
    incomplete whenever this list is not empty: the caller must say so instead
    of reporting a check that silently ignored part of the download.
    """
    return [row for row in rows if row["status"] != STATUS_OK and row.get("size") is None]


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def download_file(
    url,
    dest,
    expected_sha256,
    expected_size=None,
    resume=True,
    opener=urllib.request.urlopen,
    timeout=60.0,
    chunk_size=1024 * 1024,
):
    """Download ``url`` to ``dest``, verifying size and digest before the move.

    The body is streamed into ``dest + '.part'``. On success the partial file
    is atomically renamed, so a reader never observes a half-written model. On
    a digest mismatch the partial file is deleted: it is corrupt, and keeping
    it would poison the next resume attempt.
    """
    part = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except OSError as exc:
        # Permission denied, a file where a directory should be, a path that is
        # too long: report it against this entry instead of aborting the batch.
        raise IntegrityError(f"cannot create {os.path.dirname(dest)}: {exc}") from exc

    start = 0
    if resume and os.path.isfile(part):
        try:
            start = os.path.getsize(part)
        except OSError:
            start = 0  # the partial file vanished between isfile and getsize
        if expected_size is not None and start >= expected_size:
            # Complete or oversized: rewrite the body rather than trust it.
            start = 0

    headers = {"Accept": "application/octet-stream", "User-Agent": "comfyui-model-manager/0.1.0"}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)

    try:
        response = opener(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and start:
            # The server considers the partial file out of range: start over.
            with contextlib.suppress(OSError):
                os.remove(part)
            return download_file(
                url, dest, expected_sha256, expected_size=expected_size,
                resume=False, opener=opener, timeout=timeout, chunk_size=chunk_size,
            )
        raise IntegrityError(f"HTTP {exc.code} while fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise IntegrityError(f"cannot fetch {url}: {exc.reason}") from exc
    except OSError as exc:
        raise IntegrityError(f"cannot fetch {url}: {exc}") from exc

    try:
        with response:
            status = getattr(response, "status", None) or getattr(response, "code", None)
            if start and status != 206:
                start = 0  # server ignored the Range header; rewrite from the beginning
            mode = "ab" if start else "wb"
            written = start
            with open(part, mode) as handle:
                while True:
                    try:
                        chunk = response.read(chunk_size)
                    except OSError as exc:
                        raise IntegrityError(
                            f"connection dropped after {human_bytes(written)}: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)

        if expected_size is not None and written != expected_size:
            raise IntegrityError(
                f"incomplete download for {os.path.basename(dest)}: "
                f"{written} bytes != {expected_size} expected (partial file kept for resume)"
            )

        digest = sha256_file(part)
        if digest != expected_sha256:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise IntegrityError(
                f"sha256 mismatch for {os.path.basename(dest)}: {digest} != {expected_sha256} "
                "(partial file discarded)"
            )

        os.replace(part, dest)
    except OSError as exc:
        # Disk full, permission denied, a directory in the way, an antivirus
        # holding the .part file: this is a failure of *this* entry, not a
        # reason to abort the remaining downloads.
        raise IntegrityError(
            f"cannot write {os.path.basename(dest)}: {exc} (partial file kept for resume)"
        ) from exc
    return {"path": dest, "sha256": digest, "size": written, "resumed_from": start}


def install_manifest(
    entries,
    dest_root,
    allow_overwrite=False,
    dry_run=True,
    confirmed=False,
    opener=urllib.request.urlopen,
    timeout=60.0,
    hasher=sha256_file,
    space_check=True,
):
    """Install every entry in ``entries``, honouring the safety defaults.

    Refuses to run without ``confirmed``; refuses to touch a destination whose
    digest differs unless ``allow_overwrite`` is set. Before touching the
    network it checks the free space of the volumes the files will land on,
    counting only entries whose expected size is known (``bytes_unknown``
    reports how many were left out of that sum).
    """
    if dry_run or not confirmed:
        rows, needed = plan_manifest(entries, dest_root, hasher=hasher)
        return {
            "dry_run": True,
            "dest_root": dest_root,
            "rows": rows,
            "bytes_needed": needed,
            "bytes_unknown": len(unknown_size_rows(rows)),
            "free_bytes": free_bytes(dest_root),
        }

    rows, needed = plan_manifest(entries, dest_root, hasher=hasher)
    unknown = unknown_size_rows(rows)
    pending = [row["destination"] for row in rows if row["status"] != STATUS_OK]
    # Measure where the bytes actually land: a model directory can be a link to
    # another volume, in which case the free space of dest_root is irrelevant.
    available = min_free_bytes(pending or [dest_root])
    if space_check and available is not None and needed > available:
        raise IntegrityError(
            f"insufficient space in {dest_root}: need {human_bytes(needed)}, "
            f"have {human_bytes(available)}"
        )

    results = []
    for row in rows:
        if row["status"] == STATUS_OK:
            results.append({"entry": row["name"], "action": "skipped", "detail": row["detail"]})
            continue
        if row["status"] == STATUS_CONFLICT and not allow_overwrite:
            results.append(
                {
                    "entry": row["name"],
                    "action": "refused",
                    "detail": f"{row['detail']}; {conflict_remedy(row['detail'])}",
                }
            )
            continue
        try:
            outcome = download_file(
                row["url"],
                row["destination"],
                row["sha256"],
                expected_size=row.get("size"),
                opener=opener,
                timeout=timeout,
            )
        except IntegrityError as exc:
            results.append({"entry": row["name"], "action": "failed", "detail": str(exc)})
            continue
        except OSError as exc:
            # download_file already converts filesystem errors, but a failure
            # raised outside it must not take the whole batch down either.
            results.append({"entry": row["name"], "action": "failed", "detail": str(exc)})
            continue
        results.append(
            {
                "entry": row["name"],
                "action": "installed",
                "detail": f"{human_bytes(outcome['size'])} verified",
                "path": outcome["path"],
                "resumed_from": outcome["resumed_from"],
            }
        )
    return {
        "dry_run": False,
        "dest_root": dest_root,
        "rows": rows,
        "bytes_needed": needed,
        "bytes_unknown": len(unknown),
        "free_bytes": available,
        "results": results,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def default_models_dir(extra_roots=()):
    """Best-effort default model directory from the local discovery report."""
    import comfy_env

    report = comfy_env.discover(extra_roots=extra_roots)
    roots = report["model_roots"]
    for root in roots:
        if root.get("is_default") and "comfyui" in (root.get("section") or "").lower():
            return root["base_path"]
    for root in roots:
        if root.get("is_default"):
            return root["base_path"]
    return roots[0]["base_path"] if roots else None


def _print_plan(payload):
    rows = payload["rows"]
    print(f"destination : {payload['dest_root']}")
    print(f"needed      : {human_bytes(payload['bytes_needed'])}"
          f" (free: {human_bytes(payload.get('free_bytes'))})")
    unknown = payload.get("bytes_unknown") or 0
    if unknown:
        # Silence here would turn an incomplete free-space check into a
        # reassuring "needed" figure that ignores part of the download.
        print(f"unknown     : {unknown} entr{'y' if unknown == 1 else 'ies'} "
              "without a size in the manifest (not counted above)")
    print("")
    width = max([len(row["name"]) for row in rows] + [4])
    for row in rows:
        print(f"  {row['name']:<{width}}  {row['status']:<9}  {row['detail']}")
    if not payload.get("dry_run", True):
        print("")
        for result in payload.get("results", []):
            print(f"  {result['entry']}: {result['action']} ({result['detail']})")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Inventory, verify and fetch ComfyUI model files."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="list the model files already on disk")
    inv.add_argument("--models-dir", default=None)
    inv.add_argument("--folder", action="append", default=[], help="limit to a folder (repeatable)")
    inv.add_argument("--hash", action="store_true", help="hash every file (slow, reads all bytes)")
    inv.add_argument("--duplicates", action="store_true", help="report duplicate files")
    inv.add_argument("--json", action="store_true")

    ver = sub.add_parser("verify", help="compare a manifest against the files on disk")
    ver.add_argument("--manifest", required=True)
    ver.add_argument("--models-dir", default=None)
    ver.add_argument("--json", action="store_true")

    ins = sub.add_parser("install", help="fetch the models named by a manifest")
    ins.add_argument("--manifest", required=True)
    ins.add_argument("--models-dir", default=None)
    ins.add_argument("--yes", action="store_true", help="confirm the transfer (required to download)")
    ins.add_argument("--allow-overwrite", action="store_true")
    ins.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    models_dir = getattr(args, "models_dir", None) or default_models_dir()
    if not models_dir:
        print("error: no model directory given and none could be discovered", file=sys.stderr)
        return 1
    models_dir = os.path.abspath(models_dir)

    if args.command == "inventory":
        roots = [{"base_path": models_dir, "folders": {}}]
        entries = inventory(roots, folders=set(args.folder) or None, with_hash=args.hash)
        payload = {
            "models_dir": models_dir,
            "count": len(entries),
            "total_bytes": sum(entry["size"] for entry in entries),
            "entries": entries,
        }
        if args.duplicates:
            payload["duplicates"] = find_duplicates(entries)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"{payload['count']} files, {human_bytes(payload['total_bytes'])} in {models_dir}")
            for entry in entries:
                digest = f"  {entry['sha256']}" if entry.get("sha256") else ""
                print(f"  {entry['folder']}/{entry['name']}  {human_bytes(entry['size'])}{digest}")
            for group in payload.get("duplicates", []):
                print(f"  duplicate ({group['reason']}): {', '.join(group['paths'])}")
        return 0

    try:
        entries = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4

    if args.command == "verify":
        try:
            rows, needed = plan_manifest(entries, models_dir)
        except ComfyModelManagerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return exc.exit_code
        failures = [row for row in rows if row["status"] != STATUS_OK]
        if args.json:
            print(json.dumps({"models_dir": models_dir, "rows": rows,
                              "problems": len(failures),
                              "bytes_unknown": len(unknown_size_rows(rows))}, indent=2))
        else:
            _print_plan({"rows": rows, "dest_root": models_dir, "bytes_needed": needed,
                         "bytes_unknown": len(unknown_size_rows(rows)),
                         "free_bytes": free_bytes(models_dir)})
            print("")
            print("verification passed" if not failures else f"{len(failures)} file(s) need attention")
        return 0 if not failures else 5

    try:
        payload = install_manifest(
            entries,
            models_dir,
            allow_overwrite=args.allow_overwrite,
            dry_run=not args.yes,
            confirmed=args.yes,
        )
    except ComfyModelManagerError as exc:
        # Expected failures carry their own exit code (a full volume is an
        # IntegrityError, i.e. 5). Letting one escape as a traceback returned 1
        # and hid the documented status from the caller.
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if payload["dry_run"]:
            print("dry run (pass --yes to download)")
            print("")
        _print_plan(payload)
    if payload["dry_run"]:
        return 0
    results = payload.get("results") or []
    # A run that reports failures from a script exits 0 today, which makes an
    # unattended download look successful in a pipeline. Fail loudly instead.
    if any(result["action"] == "failed" for result in results):
        return 5
    if any(result["action"] == "refused" for result in results):
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
