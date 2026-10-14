"""Classification of skill usage evidence into the documented state ladder.

The ladder is deliberately conservative:

``available`` -> ``discovered`` -> ``loaded`` -> ``invoked`` -> ``execution_proven``

A state is only reported when the collected evidence supports it. Viewing,
mentioning, or merely listing a skill never counts as execution, and
``execution_proven`` additionally requires a correlated artifact that was
verified (an existing file or a commit resolvable in the local repository).
Anything that cannot be attributed lands in ``unverifiable`` with an explicit
reason instead of being promoted.
"""

from __future__ import annotations

import os

from _common import STATE_RANK, iso

#: Counter field names as written by Hermes in ``skills/.usage.json``.
USAGE_USE_KEYS = ("use_count", "uses", "count", "invocations")
USAGE_VIEW_KEYS = ("view_count", "load_count", "loads", "views")
USAGE_EDIT_KEYS = ("patch_count", "edit_count", "edits", "writes")

STATE_REASONS = (
    "mention_only",
    "skill_view_ok",
    "skill_view_failed",
    "skill_manage_only",
    "skill_script_failed",
    "skill_script_ok",
    "skill_script_ok_verified_artifact",
    "skill_script_result_unknown",
    "usage_counter_only",
    "empty_transcript",
)

LIMITATION_CODES = (
    "no_transcript_evidence",
    "no_correlated_artifact",
    "artifact_check_disabled",
    "out_of_transcript_load",
    "edit_without_load",
    "compacted_messages_present",
    "window_filtered",
    "child_scope",
    "result_not_observable",
)

#: States a finding may carry: the whole ladder except ``available``, which is
#: documented as a summary-only state (counted in ``skills_available``) and is
#: never emitted as a finding. Anything outside this set is a bug in the caller.
FINDING_STATES = frozenset(STATE_RANK) - {"available"}


def display_path(path: str, roots: list[str]) -> str:
    """Path shown in a report: relative to an allowed root when possible."""
    for root in roots:
        if not root:
            continue
        try:
            if os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) == os.path.abspath(root):
                return os.path.relpath(path, root).replace("\\", "/")
        except ValueError:
            continue
    return os.path.basename(path)


def _result_state(scripts: list[dict]) -> dict:
    """Outcome of the observed invocations, never inferred from prose."""
    graded = [e for e in scripts if e.get("ok") is not None]
    if not graded:
        return {"state": "unknown", "basis": None, "detail": "sem código de saída observado"}
    ok = [e for e in graded if e.get("ok") is True]
    failed = [e for e in graded if e.get("ok") is False]
    basis = graded[-1].get("basis")
    if ok and not failed:
        state = "succeeded"
    elif failed and not ok:
        state = "failed"
    else:
        state = "failed"
    return {
        "state": state,
        "basis": basis,
        "detail": "%d de %d execuções com sucesso observado" % (len(ok), len(graded)),
    }


def _verified_artifact(scripts: list[dict], roots: list[str], verify_artifacts: bool) -> dict:
    """First verified artifact correlated with a successful invocation."""
    if not verify_artifacts:
        return {"state": "unverifiable", "basis": None,
                "detail": "verificação de artefatos desativada"}
    for event in scripts:
        if event.get("ok") is not True:
            continue
        files = event.get("artifact_files") or []
        commits = event.get("artifact_commits") or []
        if files:
            return {"state": "verified", "basis": "artifact_file",
                    "detail": display_path(files[0], roots)}
        if commits:
            return {"state": "verified", "basis": "artifact_commit",
                    "detail": str(commits[0])[:12]}
    return {"state": "unverifiable", "basis": None,
            "detail": "nenhum artefato correlacionado verificável"}


def classify_scope(scope: str, events: list[dict], session: dict, index_names: set[str],
                   roots: list[str], verify_artifacts: bool,
                   window_filtered: bool, compacted_present: bool) -> list[dict]:
    """Classify every skill observed in one scope (``main`` or a child session)."""
    by_skill: dict[str, list[dict]] = {}
    for event in events:
        if event.get("scope") != scope:
            continue
        by_skill.setdefault(str(event.get("skill")), []).append(event)

    findings = []
    for name in sorted(by_skill):
        findings.append(_classify_one(
            name, by_skill[name], scope, session, roots, index_names,
            verify_artifacts, window_filtered, compacted_present,
        ))
    return findings


def _classify_one(name: str, events: list[dict], scope: str, session: dict,
                  roots: list[str], index_names: set[str], verify_artifacts: bool,
                  window_filtered: bool, compacted_present: bool) -> dict:
    views = [e for e in events if e.get("kind") == "skill_view"]
    manages = [e for e in events if e.get("kind") == "skill_manage"]
    scripts = [e for e in events if e.get("kind") == "skill_script"]
    mentions = [e for e in events if e.get("kind") == "mention"]

    loads_ok = [e for e in views if e.get("ok") is True]
    loads_failed = [e for e in views if e.get("ok") is False]
    scripts_ok = [e for e in scripts if e.get("ok") is True]
    scripts_failed = [e for e in scripts if e.get("ok") is False]

    artifact = _verified_artifact(scripts, roots, verify_artifacts)
    limitations: list[str] = []

    if scripts_ok and artifact["state"] == "verified":
        state, reason = "execution_proven", "skill_script_ok_verified_artifact"
    elif scripts and scripts_ok:
        state, reason = "invoked", "skill_script_ok"
        limitations.append("no_correlated_artifact" if verify_artifacts
                           else "artifact_check_disabled")
    elif scripts and scripts_failed:
        state, reason = "invoked", "skill_script_failed"
        limitations.append("no_correlated_artifact")
    elif scripts:
        # Invoked, but no exit code was observed: the outcome is unknown, so the
        # reason must not read as a successful run.
        state, reason = "invoked", "skill_script_result_unknown"
        limitations.append("result_not_observable")
    elif loads_ok:
        state, reason = "loaded", "skill_view_ok"
    elif loads_failed:
        state, reason = "discovered", "skill_view_failed"
    elif manages:
        state, reason = "discovered", "skill_manage_only"
        limitations.append("edit_without_load")
    elif mentions:
        state, reason = "discovered", "mention_only"
    else:
        state, reason = "discovered", "empty_transcript"

    if state in ("invoked", "execution_proven"):
        result = _result_state(scripts)
    elif state == "loaded":
        result = {"state": "unknown", "basis": "success_flag",
                  "detail": "carregamento observado; execução não observada"}
    else:
        result = {"state": "unknown", "basis": None,
                  "detail": "nenhuma execução observável"}

    if compacted_present:
        limitations.append("compacted_messages_present")
    if window_filtered:
        limitations.append("window_filtered")
    if not verify_artifacts and "artifact_check_disabled" not in limitations:
        # --no-artifact-check removes the strongest form of evidence, so the
        # report must say so instead of looking identical to a full run. The
        # invoked-without-artifact branch above already records it, hence the
        # guard: a limitation must never be listed twice.
        limitations.append("artifact_check_disabled")
    if scope != "main":
        limitations.append("child_scope")
    # Limitations observed on the individual events must survive into the
    # finding: an orphan skill_view result carries out_of_transcript_load, and
    # dropping it here would present a load that the transcript cannot confirm
    # as a clean confirmation.
    for event in events:
        for code in event.get("limitations") or ():
            if code not in limitations:
                limitations.append(code)

    timestamps = [
        float(e["ts"]) for e in events
        if isinstance(e.get("ts"), (int, float))
    ]
    evidence = sorted(
        (
            {
                "kind": e.get("kind"),
                "at": e.get("at"),
                "message_uid": e.get("message_uid"),
                "tool": e.get("tool"),
                "detail": e.get("detail"),
                "ok": e.get("ok"),
                "basis": e.get("basis"),
                "excerpt": e.get("excerpt"),
                "error": e.get("error"),
                "artifact": (e.get("artifact_files") or [None])[0] if e.get("artifact_files")
                else (e.get("artifact_commits") or [None])[0],
                "source": e.get("source", "state_db"),
                "orphan_result": True if e.get("orphan_result") else None,
            }
            for e in events
        ),
        key=lambda item: (str(item["at"] or ""), str(item["kind"]), str(item["message_uid"] or "")),
    )
    for item in evidence:
        if item["artifact"]:
            item["artifact"] = display_path(str(item["artifact"]), roots)

    finding = {
        "skill": name,
        "scope": scope,
        "session_id": session.get("id") if scope == "main" else scope.split(":", 1)[-1],
        "state": state,
        "state_rank": STATE_RANK[state],
        "state_reason": reason,
        "first_seen_at": iso(min(timestamps)) if timestamps else None,
        "last_seen_at": iso(max(timestamps)) if timestamps else None,
        "counts": {
            "loads_ok": len(loads_ok),
            "loads_failed": len(loads_failed),
            "invocations": len(scripts),
            "invocations_ok": len(scripts_ok),
            "invocations_failed": len(scripts_failed),
            "edits": len(manages),
            "mentions": len(mentions),
        },
        "evidence": evidence,
        "result": result,
        "verification": artifact,
        "limitations": sorted(set(limitations)),
        "in_skill_index": name in index_names,
    }
    return finding


def aggregate_findings(usage: dict, covered: set[tuple[str, str]],
                       roots: list[str]) -> list[dict]:
    """Findings that exist only as aggregate counters, never merged with the
    transcript counts. ``covered`` holds ``(skill, scope)`` pairs already
    reported from the transcript."""
    skills = usage.get("skills") or {}
    findings = []
    for name in sorted(skills):
        entry = skills[name] if isinstance(skills[name], dict) else {}
        if any(skill == name for skill, _scope in covered):
            continue
        uses = _counter(entry, USAGE_USE_KEYS)
        loads = _counter(entry, USAGE_VIEW_KEYS)
        edits = _counter(entry, USAGE_EDIT_KEYS)
        if not (uses or loads or edits):
            continue
        findings.append({
            "skill": name,
            "scope": "aggregate",
            "session_id": None,
            "state": "unverifiable",
            "state_rank": STATE_RANK["unverifiable"],
            "state_reason": "usage_counter_only",
            "first_seen_at": entry.get("first_seen_at"),
            "last_seen_at": (entry.get("last_used_at") or entry.get("last_viewed_at")
                             or entry.get("last_patched_at")),
            "counts": {
                "loads_ok": loads, "loads_failed": 0, "invocations": uses,
                "invocations_ok": 0, "invocations_failed": 0, "edits": edits,
                "mentions": 0,
            },
            "evidence": [{
                "kind": "usage_counter",
                "at": (entry.get("last_used_at") or entry.get("last_viewed_at")
                       or entry.get("last_patched_at")),
                "message_uid": None,
                "tool": None,
                "detail": "contadores agregados, sem vínculo com a sessão analisada",
                "ok": None,
                "basis": None,
                "excerpt": None,
                "error": None,
                "artifact": None,
                "source": "usage_json",
                "orphan_result": None,
            }],
            "result": {"state": "unknown", "basis": None,
                       "detail": "contador agregado não comprova execução"},
            "verification": {"state": "unverifiable", "basis": None,
                             "detail": "fora do transcript analisado"},
            "limitations": ["out_of_transcript_load", "no_transcript_evidence"],
            "in_skill_index": bool(usage.get("index", {}).get(name, True)),
        })
    return findings


def _counter_value(entry: dict, keys: tuple[str, ...]) -> int | None:
    """First whole-number counter among ``keys``, or ``None`` when there is none.

    ``bool`` is rejected (it is an ``int`` subclass, so ``true`` would otherwise
    be read as ``1``) and integral floats such as ``4.0`` are accepted, because
    JSON writers that emit ``4.0`` must not be reported as a counter of zero.
    A value that is not a whole number is skipped rather than invented. The first
    alias holding a whole number wins: later aliases are ignored even when they
    disagree, so the reading stays deterministic.
    """
    for key in keys:
        value = entry.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def _counter(entry: dict, keys: tuple[str, ...]) -> int:
    """``_counter_value`` with ``None`` collapsed to ``0``.

    For counters that must always produce a number. Callers that have to tell
    "the file says zero" from "the file says nothing" use ``_counter_value``.
    """
    value = _counter_value(entry, keys)
    return 0 if value is None else value


def divergences(transcript_findings: list[dict], usage: dict) -> list[dict]:
    """Skills where aggregate counters and transcript counts disagree.

    Counters are never summed across sources; disagreements are reported so the
    reader can see both numbers and their provenance. A field that
    ``.usage.json`` does not carry at all is skipped rather than compared
    against zero, which would invent a divergence out of a missing number.
    """
    skills = usage.get("skills") or {}
    out = []
    for finding in transcript_findings:
        name = finding["skill"]
        entry = skills.get(name)
        if not isinstance(entry, dict):
            continue
        counters = {
            "loads": _counter_value(entry, USAGE_VIEW_KEYS),
            "uses": _counter_value(entry, USAGE_USE_KEYS),
            "edits": _counter_value(entry, USAGE_EDIT_KEYS),
        }
        observed = {
            "loads": finding["counts"]["loads_ok"],
            "uses": finding["counts"]["invocations"],
            "edits": finding["counts"]["edits"],
        }
        for field in ("loads", "uses", "edits"):
            if counters[field] is None:
                # .usage.json carries no real counter for this field: a missing
                # number is not a divergence from zero.
                continue
            if counters[field] == observed[field]:
                continue
            out.append({
                "skill": name,
                "field": field,
                "usage_json": counters[field],
                "state_db": observed[field],
                "note": "contadores de fontes distintas; não devem ser somados",
            })
    return sorted(out, key=lambda item: (item["skill"], item["field"]))


def summarise(findings: list[dict], available: set[str], discovered: set[str]) -> dict:
    """Counts per state, computed from the findings list only."""
    counts = {
        "skills_available": len(available),
        "skills_discovered": len(discovered),
        "skills_loaded": 0,
        "skills_invoked": 0,
        "skills_execution_proven": 0,
        "skills_unverifiable": 0,
        "skills_aggregate_only": 0,
        "findings": len(findings),
    }
    for finding in findings:
        state = finding["state"]
        if state not in FINDING_STATES:
            # A state outside the ladder means these counters have drifted away
            # from it, and the finding would be counted in ``findings`` but in
            # no bucket at all: fail loudly instead of under-reporting.
            raise ValueError("estado inesperado em summarise: %r" % state)
        if state == "loaded":
            counts["skills_loaded"] += 1
        elif state == "invoked":
            counts["skills_invoked"] += 1
        elif state == "execution_proven":
            counts["skills_execution_proven"] += 1
        elif state == "unverifiable":
            counts["skills_unverifiable"] += 1
            if finding.get("scope") == "aggregate":
                counts["skills_aggregate_only"] += 1
        # ``discovered`` is a real finding state with no bucket of its own: it is
        # the session-level ``skills_discovered`` count, computed by the caller.
    return counts
