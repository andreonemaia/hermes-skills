#!/usr/bin/env python3
"""Read-only validator for task checkpoints.

Never writes and never quarantines, so it is safe in CI and pre-commit hooks.

usage:
    python scripts/validate.py [--project DIR] [--task SLUG] [--json] [--digests]

exit codes:
    0  every checkpoint is valid
    2  at least one checkpoint is invalid or corrupt
    3  no checkpoint found
    4  invalid arguments
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402


def _digest_problems(root, data):
    problems = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = item.get("path")
        if not isinstance(rel, str) or not rel:
            continue
        try:
            rel_safe = C.safe_relpath(root, rel)
        except C.SecurityError as exc:
            problems.append("files path %r is unsafe: %s" % (rel, exc))
            continue
        target = os.path.join(root, rel_safe)
        if not os.path.exists(target):
            problems.append("files path %r is missing on disk" % rel)
            continue
        recorded = item.get("sha256")
        if recorded:
            actual = C.sha256_file(target)
            if actual != recorded:
                problems.append(
                    "files path %r digest mismatch (recorded %s..., on disk %s...)"
                    % (rel, recorded[:12], actual[:12])
                )
    return problems


def validate_task(root, slug, digests=False):
    """Validate one task. Read-only: never quarantines and never writes."""
    path = C.checkpoint_path(root, slug)
    result = {"task": slug, "path": path, "ok": False, "problems": [], "recoverable": True}
    problems = []
    data = None

    if not os.path.exists(path):
        snapshots = C.history_snapshots(root, slug)
        result["recoverable"] = bool(snapshots)
        problems.append(
            "checkpoint.json is missing (%d history snapshot(s) available)"
            % len(snapshots)
        )
    else:
        try:
            data = C.load_json_strict(path)
        except C.CorruptCheckpoint as exc:
            snapshots = C.history_snapshots(root, slug)
            result["recoverable"] = bool(snapshots)
            problems.append("invalid JSON: %s" % exc)
        except OSError as exc:
            problems.append("cannot read: %s" % exc)

    if data is not None:
        problems += list(C.validate_checkpoint(data))
        problems += ["secret-looking value: %s" % item for item in C.scan_secrets(data)]
        task = data.get("task") if isinstance(data.get("task"), dict) else {}
        if task.get("id") != slug:
            problems.append(
                "task.id %r does not match the store directory %r" % (task.get("id"), slug)
            )
        result["revision"] = data.get("revision")
        if digests:
            problems += _digest_problems(root, data)

    for index, event in enumerate(C.read_ops(root, slug), 1):
        if not isinstance(event, dict):
            problems.append("operations.jsonl line %d is not a JSON object" % index)
            continue
        if not event.get("op_id"):
            problems.append("operations.jsonl line %d has no op_id" % index)
        if event.get("kind") not in C.OP_KINDS:
            problems.append(
                "operations.jsonl line %d has unknown kind %r" % (index, event.get("kind"))
            )
        if event.get("state") not in C.OP_STATES:
            problems.append(
                "operations.jsonl line %d has unknown state %r" % (index, event.get("state"))
            )
        problems += [
            "operations.jsonl line %d: %s" % (index, item)
            for item in C.scan_secrets(event)
        ]

    result["problems"] = problems
    result["ok"] = not problems
    return result


def discover(root, task=None):
    if task:
        return [C.validate_slug(task)]
    base = os.path.join(root, ".hermes", "checkpoints")
    if not os.path.isdir(base):
        return []
    return sorted(
        name for name in os.listdir(base) if os.path.isdir(os.path.join(base, name))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=".", help="project root (default: .)")
    parser.add_argument("--task", help="validate only this task slug")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--digests",
        action="store_true",
        help="also recompute the sha256 of every recorded file",
    )
    args = parser.parse_args(argv)

    root = os.path.abspath(args.project)
    try:
        slugs = discover(root, args.task)
    except (C.CheckpointError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return C.EXIT_USAGE

    if not slugs:
        print(
            "no checkpoints found under %s" % os.path.join(root, ".hermes", "checkpoints"),
            file=sys.stderr,
        )
        return C.EXIT_MISSING

    results = []
    for slug in slugs:
        try:
            results.append(validate_task(root, slug, args.digests))
        except C.CheckpointError as exc:
            results.append(
                {"task": slug, "ok": False, "problems": ["cannot validate: %s" % exc]}
            )
    ok = all(item["ok"] for item in results)

    if args.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            if item["ok"]:
                print("ok   %-30s revision %s" % (item["task"], item.get("revision")))
                continue
            print(
                "FAIL %-30s %d problem(s)%s"
                % (
                    item["task"],
                    len(item["problems"]),
                    " [recoverable from history]" if item.get("recoverable") else "",
                )
            )
            for problem in item["problems"]:
                print("       - %s" % problem)
        print("%d task(s) checked, %d invalid" % (len(results), sum(1 for r in results if not r["ok"])))

    return C.EXIT_OK if ok else C.EXIT_CORRUPT


if __name__ == "__main__":
    sys.exit(main())
