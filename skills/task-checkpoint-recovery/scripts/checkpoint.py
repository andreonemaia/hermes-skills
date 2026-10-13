#!/usr/bin/env python3
"""task-checkpoint-recovery CLI.

Usage examples
--------------
    python checkpoint.py save --task my-task --title "..." --objective "..."
    python checkpoint.py verify --task my-task --json
    python checkpoint.py resume-report --task my-task
    python checkpoint.py record-op --task my-task --kind push --target origin/x --state started
    python checkpoint.py check-op --task my-task --kind push --target origin/x
    python checkpoint.py list
    python checkpoint.py prune --task my-task --keep 10

Exit codes (save/show/list/prune/record-op/check-op/verify/resume-report):
    0 consistent / recorded        1 findings require review
    2 corrupt or refused           3 checkpoint not found
    4 usage, security or lock error (argparse usage errors included)
``check-op`` additionally uses 0 to proceed, 1 already done, 2 unknown, 3 failed.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# --------------------------------------------------------------------------
# small parsing helpers
# --------------------------------------------------------------------------

def _split(spec: str, separator: str = "=") -> tuple[str, str]:
    if separator not in spec:
        raise ValueError("expected <key>%s<value>, got %r" % (separator, spec))
    key, value = spec.split(separator, 1)
    return key.strip(), value.strip()


def _parse_pair(spec: str, sep: str = "|") -> list[str]:
    return [part.strip() for part in spec.split(sep)]


def _new_id(prefix: str, existing: list[dict]) -> str:
    used = {item.get("id") for item in existing}
    index = 1
    while "%s%d" % (prefix, index) in used:
        index += 1
    return "%s%d" % (prefix, index)


def _find(items: list[dict], item_id: str, prefix: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    item = {"id": item_id}
    items.append(item)
    return item


def _require(items: list[dict], item_id: str, kind: str) -> dict:
    """Look up an existing entry, refusing to invent one.

    ``--step-status``/``--step-evidence`` must not silently create a step with
    no title, which would produce a checkpoint the schema then rejects.
    """
    for item in items:
        if item.get("id") == item_id:
            return item
    raise ValueError(
        "unknown %s %r: declare it first (e.g. --step %s=Title)" % (kind, item_id, item_id))


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_markdown(cp: dict) -> str:
    task = cp.get("task", {})
    repo = cp.get("repo", {})
    lines: list[str] = []
    lines.append("# Checkpoint: %s" % task.get("title", task.get("id", "?")))
    lines.append("")
    lines.append("- **task id:** `%s`" % task.get("id", "?"))
    lines.append("- **status:** `%s`" % task.get("status", "?"))
    lines.append("- **revision:** %s" % cp.get("revision", "?"))
    lines.append("- **created:** %s" % task.get("created_at", "?"))
    lines.append("- **updated:** %s" % task.get("updated_at", "?"))
    lines.append("")
    lines.append("## Objective")
    lines.append("")
    lines.append(task.get("objective", "(none)"))
    lines.append("")

    lines.append("## Repository state")
    lines.append("")
    if repo.get("available"):
        lines.append("- branch: `%s`" % repo.get("branch"))
        lines.append("- head: `%s`" % repo.get("head"))
        lines.append("- dirty: `%s`" % repo.get("dirty"))
        if repo.get("ahead") is not None:
            lines.append("- ahead/behind: %s/%s" % (repo.get("ahead"), repo.get("behind")))
        if repo.get("remote_head"):
            lines.append("- remote head at save time: `%s`" % repo.get("remote_head"))
    else:
        lines.append("- unavailable: %s" % repo.get("reason", "unknown reason"))
    lines.append("")

    def section(title: str, items: list[dict], formatter) -> None:
        lines.append("## %s" % title)
        lines.append("")
        if not items:
            lines.append("(none)")
        for item in items:
            lines.append(formatter(item))
        lines.append("")

    section("Steps", cp.get("steps") or [], lambda s: "- [%s] `%s` %s%s" % (
        s.get("status"), s.get("id"), s.get("title"),
        (" — evidence: %s" % s["evidence"]) if s.get("evidence") else ""))
    section("Decisions", cp.get("decisions") or [], lambda d: "- `%s` %s%s" % (
        d.get("id"), d.get("decision"),
        (" (rationale: %s)" % d["rationale"]) if d.get("rationale") else ""))
    section("Blockers", cp.get("blockers") or [], lambda b: "- `%s` %s (needs: %s)" % (
        b.get("id"), b.get("what"), b.get("needs")))
    section("Next actions", cp.get("next_actions") or [], lambda n: "- `%s` %s%s" % (
        n.get("id"), n.get("action"),
        " [requires approval]" if n.get("requires_approval") else ""))
    section("Files", cp.get("files") or [], lambda f: "- `%s` %s sha256:%s" % (
        f.get("state"), f.get("path"), (f.get("sha256") or "?")[:12]))
    section("Artifacts", cp.get("artifacts") or [], lambda a: "- %s `%s` (%s) %s" % (
        a.get("kind"), a.get("id"), a.get("state", "?"), a.get("at", "")))
    section("Checks", cp.get("checks") or [], lambda c: "- exit %s: `%s`" % (
        c.get("exit"), c.get("command")))

    summary = cp.get("operations_summary")
    if summary:
        lines.append("## Operations ledger (claims, not proof)")
        lines.append("")
        lines.append(", ".join("%s=%s" % (k, v) for k, v in sorted(summary.items())))
        lines.append("")

    if cp.get("notes"):
        lines.append("## Notes")
        lines.append("")
        for note in cp["notes"]:
            lines.append("- %s" % note)
        lines.append("")
    if cp.get("limitations"):
        lines.append("## Limitations")
        lines.append("")
        for item in cp["limitations"]:
            lines.append("- %s" % item)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# load / save
# --------------------------------------------------------------------------

def _load(root: str, slug: str):
    return C.load_checkpoint(root, slug)  # (data, path, origin)


def _default_checkpoint(task_id: str, title: str, objective: str, now: str) -> dict:
    # revision 0: cmd_save increments it, so a brand-new checkpoint lands on 1.
    return {
        "schema": C.SCHEMA_ID,
        "revision": 0,
        "task": {
            "id": task_id, "title": title, "objective": objective,
            "status": "in_progress", "created_at": now, "updated_at": now,
        },
        "repo": {"available": False, "root": ".", "branch": None, "head": None,
                 "remote_head": None, "dirty": None, "ahead": None, "behind": None,
                 "reason": None},
        "steps": [], "decisions": [], "blockers": [], "next_actions": [],
        "files": [], "artifacts": [], "operations_summary": {}, "checks": [],
        "notes": [], "limitations": [],
    }


def _fresh_content_hash(cp: dict) -> str:
    """Hash ignoring revision/updated_at, for history de-duplication."""
    clone = json.loads(json.dumps(cp))
    clone.pop("revision", None)
    if isinstance(clone.get("task"), dict):
        clone["task"].pop("updated_at", None)
    return C.short_hash(json.dumps(clone, sort_keys=True, ensure_ascii=False), 16)


def _write_history(root: str, slug: str, previous: dict | None, current: dict) -> str | None:
    """Preserve the version just written, once, under ``history/``.

    Nothing is preserved for a brand-new checkpoint (there is no previous
    revision to compare against) and nothing is preserved when the content did
    not change, so re-saving identical content never duplicates a snapshot.
    Storing the version being written - not the one being replaced - is what
    lets ``recover_from_history`` restore the state that existed *before* the
    live file was damaged (adjustment 3).
    """
    if not previous:
        return None
    digest = _fresh_content_hash(current)
    if digest == _fresh_content_hash(previous):
        return None  # unchanged content: no new version to preserve
    directory = C.history_dir(root, slug)
    os.makedirs(directory, exist_ok=True)
    snapshots = C.history_snapshots(root, slug)
    next_index = 1
    for path in snapshots:
        name = os.path.basename(path)
        if len(name) >= 4 and name[:4].isdigit():
            # Sequential numbering, never the revision: a re-save that changes
            # nothing consumes a revision without producing a snapshot, so
            # revision numbers would leave gaps. ``max`` (not ``len``) also
            # survives a ``prune`` that removed the oldest entries.
            next_index = max(next_index, int(name[:4]) + 1)
        try:
            existing = C.load_json_strict(path)
        except C.CorruptCheckpoint:
            continue
        if _fresh_content_hash(existing) == digest:
            return None  # already preserved, do not duplicate
    stamp = str(current["task"]["updated_at"]).replace(":", "").replace("-", "")
    base = "%04d-%s" % (next_index, stamp)
    json_path = os.path.join(directory, base + ".json")
    C.atomic_write_json(json_path, current)
    C.atomic_write_text(os.path.join(directory, base + ".md"), render_markdown(current))
    return json_path


def cmd_save(args) -> int:
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    now = C.now_iso(args.now)
    store = C.store_dir(root, slug)
    os.makedirs(store, exist_ok=True)

    with C.FileLock(os.path.join(store, ".lock"), timeout=args.lock_timeout):
        previous = None
        try:
            cp, _, origin = _load(root, slug)
            previous = copy.deepcopy(cp)
            if origin == "history-fallback":
                cp = dict(cp)
                cp["notes"] = list(cp.get("notes") or []) + [
                    "recovered from history after a corrupt checkpoint at %s" % now
                ]
        except FileNotFoundError:
            cp = _default_checkpoint(slug, args.title or slug, args.objective or "", now)
        except C.CorruptCheckpoint as exc:
            return _fail(C.EXIT_CORRUPT, "cannot save: %s" % exc)

        current_revision = int(cp.get("revision", 1))
        if args.expect_revision is not None and args.expect_revision != current_revision:
            return _fail(
                C.EXIT_DRIFT,
                "revision conflict: checkpoint is at revision %d, caller expected %d "
                "(a concurrent writer, subagent or process changed it); re-read before "
                "saving" % (current_revision, args.expect_revision),
            )

        if args.title:
            cp["task"]["title"] = args.title
        if args.objective:
            cp["task"]["objective"] = args.objective
        if args.status:
            cp["task"]["status"] = args.status

        for spec in args.step or []:
            step_id, title = _split(spec)
            entry = _find(cp["steps"], step_id, "s")
            entry["title"] = title
            entry.setdefault("status", "pending")
        for spec in args.step_status or []:
            step_id, status = _split(spec)
            if status not in C.STEP_STATUS:
                raise ValueError("step status %r must be one of %s" % (status, sorted(C.STEP_STATUS)))
            _require(cp["steps"], step_id, "step")["status"] = status
        for spec in args.step_evidence or []:
            step_id, evidence = _split(spec)
            entry = _require(cp["steps"], step_id, "step")
            entry["evidence"] = evidence
            entry.setdefault("completed_at", now)

        for spec in args.decision or []:
            spec_id, rest = _split(spec)
            parts = _parse_pair(rest)
            entry = _find(cp["decisions"], spec_id, "d")
            entry["decision"] = parts[0]
            if len(parts) > 1 and parts[1]:
                entry["rationale"] = parts[1]
            entry.setdefault("at", now)

        for spec in args.blocker or []:
            spec_id, rest = _split(spec)
            parts = _parse_pair(rest)
            entry = _find(cp["blockers"], spec_id, "b")
            entry["what"] = parts[0]
            entry["needs"] = parts[1] if len(parts) > 1 and parts[1] else "unknown"
            entry.setdefault("at", now)

        for spec in args.next or []:
            spec_id, action = _split(spec)
            entry = _find(cp["next_actions"], spec_id, "n")
            entry["action"] = action
            entry.setdefault("requires_approval", True)
        for spec_id in args.next_open or []:
            _find(cp["next_actions"], spec_id, "n")["requires_approval"] = False

        for spec in args.check or []:
            parts = _parse_pair(spec)
            entry = {"command": parts[0], "exit": int(parts[1]) if len(parts) > 1 and parts[1] else 0, "at": now}
            cp["checks"].append(entry)

        for note in args.note or []:
            cp["notes"] = list(cp.get("notes") or []) + [note]

        # Snapshot the repository once, before classifying files: the previous
        # checkpoint's ``repo`` describes an older tree and would misclassify a
        # tracked file as ``created`` whenever that snapshot said unavailable.
        repo_now = C.git_state(root)
        for rel in args.file or []:
            norm = C.safe_relpath(root, rel)
            abspath = os.path.join(root, norm)
            state = "deleted"
            digest = None
            if os.path.isfile(abspath):
                digest = C.sha256_file(abspath)
                state = "modified"
                if repo_now.get("available") and C.git_available():
                    rc, _, _ = C.git_run(root, "ls-files", "--error-unmatch", norm)
                    if rc != 0:
                        state = "created"
                elif not any(item.get("path") == norm for item in cp["files"]):
                    state = "created"
            entry = {"path": norm, "sha256": digest, "state": state}
            cp["files"] = [item for item in cp["files"] if item.get("path") != norm] + [entry]

        repo_before = cp["repo"]
        # Only schema-approved fields are persisted (adjustment 5).
        cp["repo"] = {k: v for k, v in repo_now.items() if k in C.ALLOWED_REPO}
        cp["repo"]["root"] = "."
        if not cp["repo"]["available"]:
            cp["repo"]["remote_head"] = None
            limitation = cp["repo"]["reason"]
            if limitation and limitation not in cp["limitations"]:
                cp["limitations"] = list(cp.get("limitations") or []) + [limitation]
        elif repo_before.get("head") and repo_before.get("head") != cp["repo"]["head"]:
            cp["notes"] = list(cp.get("notes") or []) + [
                "HEAD moved from %s to %s since the previous checkpoint" % (
                    repo_before["head"][:12], cp["repo"]["head"][:12])
            ]

        events = C.read_ops(root, slug)
        cp["operations_summary"] = C.ops_summary(events)
        cp["revision"] = current_revision + 1
        cp["task"]["updated_at"] = now
        cp["task"]["id"] = slug

        # Privacy first: a secret must never be written, even when the rest of
        # the checkpoint would also fail schema validation.
        try:
            C.assert_no_secrets(cp)
        except C.SecurityError as exc:
            return _fail(C.EXIT_USAGE, str(exc))
        errors = C.validate_checkpoint(cp)
        if errors:
            return _fail(C.EXIT_CORRUPT,
                         "refusing to save an invalid checkpoint: %s" % "; ".join(errors))

        _write_history(root, slug, previous, cp)
        C.atomic_write_json(C.checkpoint_path(root, slug), cp)
        C.atomic_write_text(C.markdown_path(root, slug), render_markdown(cp))

    print("saved %s (revision %d, %s)" % (
        C.relative_to_root(root, C.checkpoint_path(root, slug)), cp["revision"], cp["task"]["status"]))
    return C.EXIT_OK


# --------------------------------------------------------------------------
# operation verification (the authority, not the ledger)
# --------------------------------------------------------------------------

def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _repo_available(root: str) -> bool:
    """True when ``root`` is inside a git work tree that git can actually read."""
    if not shutil.which("git"):
        return False
    rc, out, _ = C.git_run(root, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def verify_operation(root: str, entry: dict, offline: bool) -> tuple[str, str]:
    """Return ``(state, detail)`` for a ledger claim, checked against reality."""
    kind = entry.get("kind")
    target = entry.get("target") or ""
    if kind == "commit":
        if not entry.get("target"):
            return "unknown", "commit verification needs a SHA"
        if not _repo_available(root):
            # Without a readable repository we cannot tell a missing commit from
            # an unreadable one: that is ``unknown``, never a false ``failed``.
            return "unknown", "not a git repository: cannot confirm commit %s" % target[:12]
        rc, out, err = C.git_run(root, "cat-file", "-e", "%s^{commit}" % target)
        return ("verified", "commit %s exists" % target[:12]) if rc == 0 else (
            "failed", "commit %s not found" % target[:12])
    if kind == "push":
        if offline:
            return "unknown", "offline: remote push not revalidated"
        if not _repo_available(root):
            # A push claim is about this project's remote. With no repository
            # there is no remote it could have been pushed to, so the claimed
            # effect is absent - not merely unverifiable.
            return "failed", "not a git repository: no push could have come from this project"
        remote, ref = "origin", ""
        if "/" in target:
            remote, ref = target.split("/", 1)
        elif target:
            ref = target
        if not ref:
            return "unknown", "push verification needs <remote>/<ref>"
        remote_sha = C.git_remote_ref(root, remote, ref)
        if remote_sha is None:
            return "unknown", "cannot read %s/%s (remote unreachable?)" % (remote, ref)
        rc, local_sha, _ = C.git_run(root, "rev-parse", ref)
        if rc != 0:
            # The remote ref exists but nothing local resolves it. Falling back
            # to HEAD would "verify" a branch this project does not have, so the
            # claim stays unverified instead.
            return "unknown", "no local ref %s to compare against" % ref
        if local_sha and remote_sha == local_sha:
            return "verified", "%s/%s = %s" % (remote, ref, remote_sha[:12])
        if local_sha and C.git_is_ancestor(root, remote_sha, local_sha) is True:
            # The remote ref is behind the local one but contains this commit:
            # later local commits do not undo a push that already landed.
            return "verified", "%s/%s = %s is contained in local %s" % (
                remote, ref, remote_sha[:12], local_sha[:12])
        return "failed", "%s/%s is %s, local %s" % (remote, ref, remote_sha[:12], (local_sha or "?")[:12])
    if kind in ("pr", "merge"):
        if offline:
            return "unknown", "offline: PR state not revalidated"
        if not _gh_available():
            return "unknown", "gh CLI unavailable: cannot revalidate the PR"
        branch = target or ""
        if not branch:
            return "unknown", "PR verification needs a branch name"
        try:
            proc = subprocess.run(
                ["gh", "pr", "list", "--head", branch, "--state", "all",
                 "--json", "number,state,url"],
                capture_output=True, text=True, timeout=30, check=False,
                cwd=root)
        except (OSError, subprocess.SubprocessError) as exc:
            return "unknown", "gh failed: %s" % exc
        if proc.returncode != 0:
            return "unknown", "gh error: %s" % (proc.stderr.strip()[:120] or "unknown")
        try:
            prs = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return "unknown", "gh returned unparsable output"
        if not prs:
            return "failed", "no pull request found for head %s" % branch
        pr = prs[0]
        state = pr.get("state")
        if kind == "merge":
            return ("verified", "PR #%s is %s" % (pr.get("number"), state)) if state == "MERGED" else (
                "failed", "PR #%s is %s, not MERGED" % (pr.get("number"), state))
        return "verified", "PR #%s is %s (%s)" % (pr.get("number"), state, pr.get("url"))
    if kind == "download":
        path = entry.get("detail_path") or target
        if path and os.path.isfile(os.path.join(root, path)):
            return "unknown", "download present but no digest recorded; cannot confirm identity"
        return "unknown", "no digest recorded for the download"
    return "unknown", "no verifier for kind %r" % kind


def _verify_ops(root: str, events: list[dict], offline: bool) -> tuple[list[dict], list[dict], dict]:
    """Return (events, findings, claims) after revalidating every claim.

    ``claims`` is the enriched view: each entry carries ``live_state`` /
    ``live_detail`` from the real source of truth, so callers report what was
    revalidated instead of what the ledger merely asserts.
    """
    findings: list[dict] = []
    claims = C.current_ops(events)
    for op_id, entry in sorted(claims.items()):
        recorded = entry.get("state")
        live, detail = verify_operation(root, entry, offline)
        entry["live_state"] = live
        entry["live_detail"] = detail
        if recorded == "planned":
            if live == "verified":
                findings.append({"id": "op_planned_landed", "severity": "warning",
                                 "message": "%s was still recorded as planned but reality confirms "
                                            "it is done (%s): record it and do not repeat the work"
                                            % (op_id, detail)})
            else:
                findings.append({"id": "op_planned", "severity": "info",
                                 "message": "%s is planned but never started" % op_id})
            continue
        if live == "verified":
            if recorded != "verified":
                findings.append({"id": "op_completed_unrecorded", "severity": "info",
                                 "message": "%s is recorded as %s but reality confirms it is done (%s); record it"
                                            % (op_id, recorded, detail)})
        elif live == "failed":
            if recorded == "verified":
                findings.append({"id": "op_stale_claim", "severity": "critical",
                                 "message": "%s claims verified but reality says failed (%s)"
                                            % (op_id, detail)})
            else:
                findings.append({"id": "op_unresolved", "severity": "warning",
                                 "message": "%s is recorded as %s and is still not present (%s): "
                                            "needs an explicit decision before retrying"
                                            % (op_id, recorded, detail)})
        else:
            findings.append({"id": "op_unknown", "severity": "warning",
                             "message": "%s could not be revalidated (%s): ask before acting"
                                        % (op_id, detail)})
    return events, findings, claims


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------

def _drift_findings(root: str, cp: dict, now_epoch: float, stale_after: float) -> list[dict]:
    findings: list[dict] = []
    repo = cp.get("repo") or {}
    if not repo.get("available"):
        findings.append({"id": "git_unavailable", "severity": "info",
                         "message": repo.get("reason") or "git state unavailable"})
    else:
        state = C.git_state(root)
        if not state.get("available"):
            findings.append({"id": "git_unavailable", "severity": "info",
                             "message": state.get("reason") or "git state unavailable"})
        else:
            if repo.get("branch") and state.get("branch") != repo.get("branch"):
                findings.append({"id": "branch_mismatch", "severity": "warning",
                                 "message": "checkpoint branch %s, current branch %s: revalidate before continuing"
                                            % (repo.get("branch"), state.get("branch"))})
            if repo.get("head") and state.get("head") and state["head"] != repo["head"]:
                ancestor = C.git_is_ancestor(root, repo["head"], state["head"])
                if ancestor is True:
                    findings.append({"id": "head_advanced", "severity": "info",
                                     "message": "HEAD advanced from %s to %s (checkpoint is an ancestor)"
                                                % (repo["head"][:12], state["head"][:12])})
                elif ancestor is False:
                    findings.append({"id": "history_rewritten", "severity": "critical",
                                     "message": "checkpoint HEAD %s is not an ancestor of %s: history was rewritten"
                                                % (repo["head"][:12], state["head"][:12])})
                else:
                    findings.append({"id": "head_diverged", "severity": "warning",
                                     "message": "cannot relate checkpoint HEAD %s to current %s"
                                                % (repo["head"][:12], state["head"][:12])})
            if repo.get("dirty") is False and state.get("dirty") is True:
                findings.append({"id": "dirty_tree", "severity": "warning",
                                 "message": "working tree is dirty but the checkpoint recorded it clean"})

    for item in cp.get("files") or []:
        try:
            norm = C.safe_relpath(root, item.get("path", ""))
        except C.SecurityError as exc:
            findings.append({"id": "unsafe_path", "severity": "critical", "message": str(exc)})
            continue
        abspath = os.path.join(root, norm)
        if not os.path.isfile(abspath):
            findings.append({"id": "file_missing", "severity": "warning",
                             "message": "recorded file is gone: %s" % norm})
            continue
        if item.get("sha256") and C.sha256_file(abspath) != item["sha256"]:
            findings.append({"id": "file_drift", "severity": "warning",
                             "message": "file changed since the checkpoint: %s" % norm})

    updated = (cp.get("task") or {}).get("updated_at")
    if updated and stale_after > 0:
        age = now_epoch - _timestamp_epoch(updated)
        if age > stale_after:
            findings.append({"id": "stale_checkpoint", "severity": "info",
                             "message": "checkpoint is %.1f h old: revalidate before trusting it"
                                        % (age / 3600.0)})
    return findings


def _timestamp_epoch(text: str) -> float:
    import datetime
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    try:
        # A store written before bare epochs were normalised to ISO-8601 may
        # still hold one; read it back instead of dating the checkpoint to 1970.
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _now_epoch(args) -> float:
    """Epoch seconds for ``--now``; falls back to the clock (or ``TCR_EPOCH``)."""
    override = getattr(args, "now", None)
    if override:
        return _timestamp_epoch(override)
    return C.now_epoch()


def _collect_findings(root: str, cp: dict, offline: bool, stale_after: float,
                      now_epoch: float | None = None):
    """Return (findings, events, claims) for one checkpoint."""
    if now_epoch is None:
        now_epoch = C.now_epoch()
    findings = _drift_findings(root, cp, now_epoch, stale_after)
    events = C.read_ops(root, cp["task"]["id"])
    events, op_findings, claims = _verify_ops(root, events, offline)
    findings.extend(op_findings)
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["id"]))
    return findings, events, claims


#: Findings describing a supported, explicitly reduced mode or a benign
#: observation. They are always reported, but they change neither the overall
#: result nor the exit code unless ``--strict`` is passed (adjustment 4).
BENIGN_FINDING_IDS = {"git_unavailable", "op_planned", "op_completed_unrecorded"}


def _actionable(findings: list[dict]) -> list[dict]:
    """Findings that ask the caller to do something before continuing."""
    return [f for f in findings if f["id"] not in BENIGN_FINDING_IDS]


def _worst(findings: list[dict]) -> str:
    actionable = _actionable(findings)
    if not actionable:
        return "clean"
    return min((f["severity"] for f in actionable), key=lambda s: SEVERITY_ORDER.get(s, 9))


def _exit_for(findings: list[dict], strict: bool = False) -> int:
    """Any actionable finding fails; ``--strict`` also promotes benign ones.

    A checkpoint in a non-git project is a supported, explicitly limited mode,
    so ``git_unavailable`` alone must not make every ``verify`` exit non-zero
    (adjustment 4). An old checkpoint, by contrast, must fail: exposing
    obsolescence before the state is trusted is the point of ``verify``.
    """
    if _actionable(findings):
        return C.EXIT_DRIFT
    if strict and findings:
        return C.EXIT_DRIFT
    return C.EXIT_OK


def cmd_verify(args) -> int:
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    try:
        cp, path, origin = _load(root, slug)
    except FileNotFoundError:
        return _fail(C.EXIT_MISSING, "no checkpoint for task %r (looked in %s)"
                     % (slug, C.relative_to_root(root, C.checkpoint_path(root, slug))))
    except C.CorruptCheckpoint as exc:
        return _fail(C.EXIT_CORRUPT, str(exc))

    findings, events, claims = _collect_findings(root, cp, args.offline, args.stale_after,
                                                 now_epoch=_now_epoch(args))
    payload = {
        "task": slug, "revision": cp.get("revision"), "source": origin,
        "checkpoint_path": C.relative_to_root(root, path),
        "result": _worst(findings), "findings": findings,
        "operations": claims,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("verify %s (revision %s, %s): %s" % (slug, cp.get("revision"), origin, payload["result"]))
        for finding in findings:
            print("  [%-8s] %-22s %s" % (finding["severity"], finding["id"], finding["message"]))
        if not findings:
            print("  no divergences found")
        print("  (a clean result is not a guarantee: it revalidates what the checkpoint recorded)")
    return _exit_for(findings, args.strict)


# --------------------------------------------------------------------------
# resume report
# --------------------------------------------------------------------------

def cmd_resume_report(args) -> int:
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    try:
        cp, path, origin = _load(root, slug)
    except FileNotFoundError:
        return _fail(C.EXIT_MISSING, "no checkpoint for task %r" % slug)
    except C.CorruptCheckpoint as exc:
        return _fail(C.EXIT_CORRUPT, str(exc))

    findings, events, claims = _collect_findings(root, cp, args.offline, args.stale_after,
                                                 now_epoch=_now_epoch(args))
    task = cp.get("task", {})
    by_sev = {sev: [f for f in findings if f["severity"] == sev] for sev in SEVERITY_ORDER}

    out: list[str] = []
    out.append("# Resume report: %s" % task.get("title", slug))
    out.append("")
    out.append("Source: `%s` (revision %s, %s)" % (C.relative_to_root(root, path), cp.get("revision"), origin))
    if origin == "history-fallback":
        out.append("")
        out.append("> The current checkpoint was corrupt and was quarantined; this report uses the "
                   "newest valid snapshot from `history/`.")
    out.append("")
    out.append("## Recovered from the checkpoint")
    out.append("")
    out.append("- objective: %s" % (task.get("objective") or "(none)"))
    out.append("- status: `%s`" % task.get("status"))
    done = [s for s in cp.get("steps") or [] if s.get("status") == "done"]
    out.append("- steps done: %d/%d" % (len(done), len(cp.get("steps") or [])))
    for step in cp.get("steps") or []:
        out.append("  - [%s] `%s` %s%s" % (step.get("status"), step.get("id"), step.get("title"),
                                           (" — %s" % step["evidence"]) if step.get("evidence") else ""))
    for decision in cp.get("decisions") or []:
        out.append("- decision `%s`: %s" % (decision.get("id"), decision.get("decision")))
    out.append("")

    out.append("## Revalidated against reality")
    out.append("")
    if not findings:
        out.append("- no divergences: the checkpoint still matches the project state as far as "
                   "could be checked")
    for finding in findings:
        out.append("- [%s] %s" % (finding["severity"], finding["message"]))
    out.append("")

    if claims:
        out.append("## External effects (revalidated, never assumed)")
        out.append("")
        for op_id, entry in sorted(claims.items()):
            live = entry.get("live_state", "not checked")
            out.append("- `%s`: ledger says `%s`, reality says `%s` (%s)"
                       % (op_id, entry.get("state"), live, entry.get("live_detail", "n/a")))
        out.append("")

    out.append("## Still pending")
    out.append("")
    blockers = cp.get("blockers") or []
    if blockers:
        for blocker in blockers:
            out.append("- blocker `%s`: %s (needs: %s)" % (blocker.get("id"), blocker.get("what"), blocker.get("needs")))
    actions = cp.get("next_actions") or []
    for action in actions:
        flag = " [requires approval]" if action.get("requires_approval") else ""
        out.append("- next `%s`: %s%s" % (action.get("id"), action.get("action"), flag))
    if not blockers and not actions:
        out.append("- nothing recorded")
    out.append("")

    out.append("## Recommended next step")
    out.append("")
    unknowns = [f for f in findings if f["id"] in ("op_unknown", "op_stale_claim", "op_unresolved")]
    criticals = by_sev.get("critical") or []
    if unknowns or criticals:
        out.append("- STOP and ask: unresolved or unverifiable effects must be decided by the user "
                   "before any operation with side effects is repeated.")
    elif any(f["id"] == "branch_mismatch" for f in findings):
        out.append("- revalidate the branch situation with the user, then continue from the "
                   "pending steps above.")
    else:
        out.append("- the state is consistent; continue with the first pending next action "
                   "(after re-reading the files listed in the checkpoint).")
    out.append("")
    out.append("_This report revalidates; it does not guarantee. Nothing was executed._")
    report = "\n".join(out) + "\n"

    if args.json:
        print(json.dumps({"task": slug, "revision": cp.get("revision"), "source": origin,
                          "result": _worst(findings), "findings": findings,
                          "operations": claims, "report": report}, ensure_ascii=False, indent=2))
    else:
        print(report)
    return _exit_for(findings)


# --------------------------------------------------------------------------
# operations ledger commands
# --------------------------------------------------------------------------

def cmd_record_op(args) -> int:
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    if args.kind not in C.OP_KINDS:
        return _fail(C.EXIT_USAGE, "unknown kind %r (expected %s)" % (args.kind, sorted(C.OP_KINDS)))
    if args.state not in C.OP_STATES:
        return _fail(C.EXIT_USAGE, "unknown state %r (expected %s)" % (args.state, sorted(C.OP_STATES)))
    if not args.op_id and not args.target:
        # Otherwise the id collapses to ``kind:?`` and every operation of this
        # kind overwrites the previous one in the ledger.
        return _fail(C.EXIT_USAGE, "--op-id or --target is required for kind %r" % (args.kind,))
    now = C.now_iso(args.now)
    store = C.store_dir(root, slug)
    os.makedirs(store, exist_ok=True)
    event = {
        "op_id": args.op_id or ("%s:%s" % (args.kind, args.target)),
        "kind": args.kind, "state": args.state, "target": args.target,
        "detail": args.detail, "verification": args.verification,
        "at": now,
    }
    try:
        C.assert_no_secrets(event, "$.operation")
    except C.SecurityError as exc:
        return _fail(C.EXIT_USAGE, str(exc))
    with C.FileLock(os.path.join(store, ".lock"), timeout=args.lock_timeout):
        appended = C.append_op(root, slug, event)
        events = C.read_ops(root, slug)
        cp_path = C.checkpoint_path(root, slug)
        if os.path.exists(cp_path):
            try:
                cp, _, _ = _load(root, slug)
                previous = copy.deepcopy(cp)
                cp["operations_summary"] = C.ops_summary(events)
                # The ledger changed, so the checkpoint changed: bump the
                # revision and keep the previous version in history exactly as
                # ``save`` does, so a concurrent writer sees the drift.
                cp["revision"] = int(cp.get("revision", 1)) + 1
                cp["task"]["updated_at"] = now
                _write_history(root, slug, previous, cp)
                C.atomic_write_json(cp_path, cp)
                C.atomic_write_text(C.markdown_path(root, slug), render_markdown(cp))
            except C.CorruptCheckpoint as exc:
                print("warning: checkpoint.json is corrupt (%s); the operation was "
                      "recorded in the ledger but the checkpoint was not updated" % exc,
                      file=sys.stderr)
    print(json.dumps({"recorded": appended, "summary": C.ops_summary(events)}, ensure_ascii=False, indent=2))
    print("note: this is a claim; revalidate before treating %s as complete" % appended["op_id"],
          file=sys.stderr)
    return C.EXIT_OK


def cmd_check_op(args) -> int:
    """Decide whether an effect-carrying operation may run."""
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    if not args.op_id and not args.target:
        # The ledger id must be reproducible: without either, the lookup would
        # silently miss every entry that was recorded with an explicit id.
        return _fail(C.EXIT_USAGE, "--op-id or --target is required for kind %r" % (args.kind,))
    op_id = args.op_id or ("%s:%s" % (args.kind, args.target))
    claims = C.current_ops(C.read_ops(root, slug))
    entry = claims.get(op_id)
    if entry is None:
        print("check-op %s: no ledger entry -> proceed (nothing recorded)" % op_id)
        return 0
    live, detail = verify_operation(root, entry, args.offline)
    recorded = entry.get("state")
    print("check-op %s: ledger=%s reality=%s (%s)" % (op_id, recorded, live, detail))
    if live == "verified":
        print("=> already done and confirmed (ledger=%s): do not repeat." % recorded)
        return 1
    if recorded == "planned":
        print("=> planned and not present in reality: proceed (nothing was executed yet).")
        return 0
    if live == "failed":
        if recorded == "verified":
            print("=> reality contradicts the ledger: stop and ask the user before acting.")
        else:
            print("=> the effect is not present (ledger=%s): a previous attempt did not land; "
                  "ask the user before retrying." % recorded)
        return 3
    print("=> cannot confirm: state `unknown`. Ask the user before repeating an operation with "
          "side effects. No exactly-once guarantee is offered.")
    return 2


# --------------------------------------------------------------------------
# show / list / prune
# --------------------------------------------------------------------------

def _list_tasks(root: str) -> list[dict]:
    base = os.path.join(os.path.abspath(root), ".hermes", "checkpoints")
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        if not C.SLUG_RE.match(name):
            continue
        path = os.path.join(base, name, "checkpoint.json")
        entry = {"task": name, "status": "unreadable", "revision": None, "updated_at": None,
                 "steps_done": 0, "steps_total": 0, "blockers": 0}
        try:
            cp = C.load_checkpoint(root, name, quarantine_corrupt=False)[0]
        except Exception:  # noqa: BLE001 - listing must never crash
            out.append(entry)
            continue
        entry.update({
            "status": cp["task"]["status"], "revision": cp.get("revision"),
            "updated_at": cp["task"]["updated_at"],
            "steps_done": sum(1 for s in cp.get("steps") or [] if s.get("status") == "done"),
            "steps_total": len(cp.get("steps") or []),
            "blockers": len(cp.get("blockers") or []),
        })
        out.append(entry)
    return out


def cmd_list(args) -> int:
    root = os.path.abspath(args.project)
    tasks = _list_tasks(root)
    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return C.EXIT_OK
    if not tasks:
        print("no checkpoints under %s" % C.relative_to_root(root, os.path.join(root, ".hermes", "checkpoints")))
        return C.EXIT_OK
    print("%-28s %-18s %5s %6s %-20s %s" % ("TASK", "STATUS", "REV", "STEPS", "UPDATED", "BLOCKERS"))
    for task in tasks:
        print("%-28s %-18s %5s %6s %-20s %s" % (
            task["task"], task["status"], task["revision"] or "-",
            "%s/%s" % (task["steps_done"], task["steps_total"]),
            task["updated_at"] or "-", task["blockers"]))
    return C.EXIT_OK


def cmd_show(args) -> int:
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    try:
        cp, _, _ = _load(root, slug)
    except FileNotFoundError:
        return _fail(C.EXIT_MISSING, "no checkpoint for task %r" % slug)
    except C.CorruptCheckpoint as exc:
        return _fail(C.EXIT_CORRUPT, str(exc))
    if args.json:
        print(json.dumps(cp, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(cp), end="")
    return C.EXIT_OK


def cmd_prune(args) -> int:
    root = os.path.abspath(args.project)
    slug = C.validate_slug(args.task)
    snapshots = C.history_snapshots(root, slug)
    if len(snapshots) <= args.keep:
        print("prune: %d snapshots, nothing to remove (keep=%d)" % (len(snapshots), args.keep))
        return C.EXIT_OK
    victims = snapshots[: len(snapshots) - args.keep]
    for path in victims:
        target = path[:-5] + ".md"
        if args.dry_run:
            print("would remove %s" % os.path.basename(path))
            continue
        for candidate in (path, target):
            try:
                os.unlink(candidate)
            except OSError:
                pass
        print("removed %s" % os.path.basename(path))
    if not args.dry_run:
        print("prune: kept %d most recent snapshots" % args.keep)
    return C.EXIT_OK


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------

def _fail(code: int, message: str) -> int:
    print("error: %s" % message, file=sys.stderr)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = C.UsageArgumentParser(
        prog="checkpoint.py",
        description="Structured, revalidated checkpoints for long-running agent tasks.")
    parser.add_argument("--project", default=".", help="project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--task", required=True, help="task id (slug)")
        # SUPPRESS (not None): a subparser default would silently overwrite the
        # global --project value parsed before the subcommand.
        sp.add_argument("--project", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--now", default=None, help="ISO timestamp override (deterministic runs)")
        sp.add_argument("--lock-timeout", type=float, default=10.0, help="seconds to wait for the store lock")

    p = sub.add_parser("save", help="write or update a checkpoint")
    add_common(p)
    p.add_argument("--title")
    p.add_argument("--objective")
    p.add_argument("--status", choices=sorted(C.TASK_STATUS))
    p.add_argument("--step", action="append", metavar="ID=TITLE")
    p.add_argument("--step-status", action="append", metavar="ID=STATUS")
    p.add_argument("--step-evidence", action="append", metavar="ID=EVIDENCE")
    p.add_argument("--decision", action="append", metavar="ID=TEXT[|RATIONALE]")
    p.add_argument("--blocker", action="append", metavar="ID=WHAT[|NEEDS]")
    p.add_argument("--next", action="append", metavar="ID=ACTION")
    p.add_argument("--next-open", action="append", metavar="ID", help="mark next action as not needing approval")
    p.add_argument("--check", action="append", metavar="COMMAND|EXIT")
    p.add_argument("--note", action="append")
    p.add_argument("--file", action="append", metavar="RELATIVE_PATH")
    p.add_argument("--expect-revision", type=int, default=None,
                   help="compare-and-swap guard against concurrent writers")
    p.set_defaults(func=cmd_save, _name="save")

    p = sub.add_parser("verify", help="revalidate a checkpoint against reality")
    add_common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--offline", action="store_true", help="skip remote verification (results become unknown)")
    p.add_argument("--strict", action="store_true", help="treat info findings as failures too")
    p.add_argument("--stale-after", type=float, default=C.STALE_AFTER_SECONDS_DEFAULT,
                   help="seconds before a checkpoint counts as stale")
    p.set_defaults(func=cmd_verify, _name="verify")

    p = sub.add_parser("resume-report", help="human report for resuming the task")
    add_common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--stale-after", type=float, default=C.STALE_AFTER_SECONDS_DEFAULT)
    p.set_defaults(func=cmd_resume_report, _name="resume-report")

    p = sub.add_parser("record-op", help="append an effect claim to the ledger")
    add_common(p)
    # No ``choices`` here on purpose: an unknown kind/state must be a usage
    # error (exit 4) that names the accepted values, not argparse's generic 2.
    p.add_argument("--kind", required=True)
    p.add_argument("--target", default="")
    p.add_argument("--state", required=True)
    p.add_argument("--op-id", default=None)
    p.add_argument("--detail", default=None)
    p.add_argument("--verification", default=None, help="what was run to confirm the effect")
    p.set_defaults(func=cmd_record_op, _name="record-op")

    p = sub.add_parser("check-op", help="decide whether an effect-carrying operation may run")
    add_common(p)
    p.add_argument("--kind", required=True, choices=sorted(C.OP_KINDS))
    p.add_argument("--target", default="")
    p.add_argument("--op-id", default=None)
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_check_op, _name="check-op")

    p = sub.add_parser("show", help="print a checkpoint")
    add_common(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show, _name="show")

    p = sub.add_parser("list", help="list checkpoints in the project")
    p.add_argument("--project", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list, _name="list")

    p = sub.add_parser("prune", help="trim the history directory")
    add_common(p)
    p.add_argument("--keep", type=int, default=C.HISTORY_KEEP_DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune, _name="prune")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits directly for --help and for usage errors; report the
        # code to the caller instead of letting SystemExit escape a function
        # that returns an int (tests call main() directly).
        code = exc.code
        if code is None:
            return C.EXIT_OK
        return code if isinstance(code, int) else C.EXIT_USAGE
    if getattr(args, "expect_revision", None) is not None and args.expect_revision < 0:
        return _fail(C.EXIT_USAGE, "--expect-revision must be >= 0")
    if getattr(args, "keep", None) is not None and args.keep < 0:
        return _fail(C.EXIT_USAGE, "--keep must be >= 0")
    now = getattr(args, "now", None)
    if now:
        try:
            C.parse_timestamp(now)
        except ValueError as exc:
            # Refused before any store is opened: an unusable --now must not
            # create a checkpoint whose timestamps cannot be parsed again.
            return _fail(C.EXIT_USAGE, str(exc))
    if args.command in ("save", "verify", "resume-report", "record-op", "check-op", "show", "prune"):
        if args.project is None:
            args.project = "."
    try:
        return args.func(args)
    except C.LockTimeout as exc:
        return _fail(C.EXIT_USAGE, str(exc))
    except C.SecurityError as exc:
        return _fail(C.EXIT_USAGE, str(exc))
    except C.RevisionConflict as exc:
        return _fail(C.EXIT_DRIFT, str(exc))
    except ValueError as exc:
        return _fail(C.EXIT_USAGE, str(exc))
    except OSError as exc:
        # Permissions, a missing git/gh binary, a full disk: report the run as
        # refused instead of escaping main with a traceback.
        return _fail(C.EXIT_CORRUPT, "cannot complete: %s" % exc)
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())