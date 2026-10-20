"""Apply, status and verify a long-run objective on the native Kanban board.

State model
-----------
The board is the source of truth; the local ``objective.json`` is only a cache
mapping ``step_id -> card_id``. ``apply`` is idempotent (cards are created with
``--idempotency-key lro:<objective>:<step>``), so re-running it after a crash
re-discovers existing cards instead of duplicating them.

The verifier never trusts ``done`` alone: it requires a closing run with
``outcome=completed``, a non-empty declared result, every required evidence
field and a proof for every acceptance criterion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import lro_contract as C
from lro_adapter import AdapterError, KanbanCLI
from lro_spec import PR_RE, SpecError, load_spec, parse_marker, render_body

STATE_FILE = "objective.json"
VERDICT_FIELDS = ("verdict", "review_verdict")
PR_IN_TEXT_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")


class Divergence(RuntimeError):
    """The board does not match the versioned spec."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ts(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _run_key(run: Dict[str, Any]) -> Tuple[float, str]:
    return (_ts(run.get("started_at")), str(run.get("id", "")))


def _as_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("task_id") or "")
    return str(item or "")


def _parents(payload: Dict[str, Any]) -> List[str]:
    raw = payload.get("parents")
    if raw is None:
        raw = (payload.get("task") or {}).get("parents") or []
    return [value for value in (_as_id(item) for item in raw) if value]


def _body(payload: Dict[str, Any]) -> str:
    task = payload.get("task") or {}
    return str(task.get("body") or task.get("description") or "")


def _runs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    runs = payload.get("runs") or []
    return [run for run in runs if isinstance(run, dict)]


def closing_run(runs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Latest run that actually completed, regardless of arrival order."""
    completed = [run for run in runs if str(run.get("outcome") or "").lower() == "completed"]
    if not completed:
        return None
    return max(completed, key=_run_key)


def declared_result(payload: Dict[str, Any], run: Optional[Dict[str, Any]]) -> str:
    if run and str(run.get("summary") or "").strip():
        return str(run["summary"]).strip()
    latest = payload.get("latest_summary")
    if isinstance(latest, dict):
        latest = latest.get("summary") or latest.get("body")
    if str(latest or "").strip():
        return str(latest).strip()
    task = payload.get("task") or {}
    return str(task.get("result") or "").strip()


def state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE


def load_state(state_dir: Path) -> Optional[Dict[str, Any]]:
    path = state_path(state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cards"), dict):
        return None
    return data


def save_state(state_dir: Path, data: Dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_path(state_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Board view
# ---------------------------------------------------------------------------

class StepView:
    def __init__(self, step: Dict[str, Any], card_id: str, payload: Dict[str, Any],
                 expected_parents: Sequence[str]):
        self.step = step
        self.card_id = card_id
        self.payload = payload
        self.task = payload.get("task") or {}
        self.status = str(self.task.get("status") or "unknown")
        self.runs = _runs(payload)
        self.run = closing_run(self.runs)
        self.result = declared_result(payload, self.run)
        self.body = _body(payload)
        self.marker = parse_marker(self.body)
        self.parents = _parents(payload)
        self.expected_parents = list(expected_parents)

    @property
    def kind(self) -> str:
        return self.step["kind"]

    @property
    def executed(self) -> bool:
        return self.status == "done"

    @property
    def marker_ok(self) -> bool:
        return bool(self.marker and self.marker.get("sha256") == self.step["_spec_sha256"]
                    and self.marker.get("objective") == self.step["_objective_id"]
                    and self.marker.get("step") == self.step["id"])

    @property
    def parents_ok(self) -> bool:
        return set(self.parents) == set(self.expected_parents)


def _load_views(adapter: KanbanCLI, spec: Dict[str, Any], state: Dict[str, Any],
                problems: List[str]) -> Dict[str, StepView]:
    cards = state.get("cards") or {}
    expected_ids = {step["id"] for step in spec["steps"]}
    for step_id in sorted(set(cards) - expected_ids):
        problems.append(f"state: card for unknown step {step_id!r} ({cards[step_id]})")
    views: Dict[str, StepView] = {}
    for step in spec["steps"]:
        card_id = cards.get(step["id"])
        if not card_id:
            problems.append(f"state: no card recorded for step {step['id']!r}")
            continue
        try:
            payload = adapter.show(card_id)
        except AdapterError as exc:
            if exc.kind == "not_found":
                problems.append(f"{step['id']}: card {card_id} not found")
                continue
            raise
        step_view = dict(step)
        step_view["_spec_sha256"] = spec["spec_sha256"]
        step_view["_objective_id"] = spec["objective_id"]
        expected = [cards[dep] for dep in step["depends_on"] if dep in cards]
        view = StepView(step_view, card_id, payload, expected)
        if not view.marker_ok:
            problems.append(f"{step['id']}: body marker missing or divergent "
                            f"(card {card_id})")
        if not view.parents_ok:
            problems.append(f"{step['id']}: parents {view.parents} != expected {expected}")
        views[step["id"]] = view
    return views


# ---------------------------------------------------------------------------
# Evidence and acceptance
# ---------------------------------------------------------------------------

def _resolve_artifact(value: str, bases: Sequence[Path]) -> Optional[Path]:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    for base in bases:
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved
    return None


def _pr_url(metadata: Any) -> str:
    """First valid PR URL declared by the runtime, if any.

    ``pr_url`` and ``published_pr`` are both produced by different runtime
    versions. An absent, empty or malformed value proves nothing, so each
    candidate is validated independently instead of trusting the first key.
    """
    if not isinstance(metadata, dict):
        return ""
    for key in ("pr_url", "published_pr"):
        value = metadata.get(key)
        if isinstance(value, str) and PR_RE.match(value.strip()):
            return value.strip()
    return ""


def check_evidence(step: Dict[str, Any], run: Optional[Dict[str, Any]],
                   bases: Sequence[Path]) -> Dict[str, Any]:
    """Return ``{"ok": bool, "missing": [...], "problems": [...]}``."""
    missing: List[str] = []
    problems: List[str] = []
    metadata = (run or {}).get("metadata") or {}
    if not isinstance(metadata, dict):
        return {"ok": False, "missing": list(step["evidence_required"]),
                "problems": ["metadata is not an object"]}
    for field in step["evidence_required"]:
        value = metadata.get(field)
        if field == "pr_url":
            # A valid published_pr is equivalent; an invalid pr_url alone is
            # not publication evidence.
            if not _pr_url(metadata):
                if value not in (None, "", [], {}):
                    problems.append("pr_url: expected https://github.com/OWNER/REPO/pull/N")
                else:
                    missing.append(field)
            continue
        if value is None or value == "" or value == [] or value == {}:
            missing.append(field)
            continue
        if field == "artifacts":
            if not isinstance(value, list):
                problems.append("artifacts: expected a list of paths")
                continue
            for item in value:
                if not isinstance(item, str) or not _resolve_artifact(item, bases):
                    problems.append(f"artifacts: not found: {item!r}")
        elif field == "changed_files":
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                                      for v in value):
                problems.append("changed_files: expected a non-empty list of paths")
        elif field == "tests_run":
            if not isinstance(value, list) or not value:
                problems.append("tests_run: expected a non-empty list of test records")
                continue
            for record in value:
                if not isinstance(record, dict):
                    problems.append("tests_run: each record must be an object")
                    continue
                command = record.get("cmd") or record.get("command")
                exit_code = record.get("exit")
                if not isinstance(command, str) or not command.strip():
                    problems.append("tests_run: record is missing a non-empty 'cmd'")
                if exit_code != 0 or isinstance(exit_code, bool):
                    problems.append("tests_run: record must declare 'exit': 0")
        elif field == "commands":
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                                      for v in value):
                problems.append("commands: expected a non-empty list of commands")
        elif field == "notes":
            if not isinstance(value, str) or not value.strip():
                problems.append("notes: expected non-empty text")
    return {"ok": not missing and not problems, "missing": missing, "problems": problems}


def check_acceptance(step: Dict[str, Any], run: Optional[Dict[str, Any]],
                     bases: Sequence[Path]) -> Dict[str, Any]:
    metadata = (run or {}).get("metadata") or {}
    entries = metadata.get("acceptance") if isinstance(metadata, dict) else None
    if not isinstance(entries, list):
        return {"ok": False, "missing": [c["id"] for c in step["acceptance"]],
                "unmet": [], "unproven": [], "problems": ["metadata.acceptance missing"]}
    by_id: Dict[str, Dict[str, Any]] = {}
    problems: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            problems.append("acceptance: malformed entry")
            continue
        by_id[str(entry["id"])] = entry
    missing: List[str] = []
    unmet: List[str] = []
    unproven: List[str] = []
    for criterion in step["acceptance"]:
        entry = by_id.get(criterion["id"])
        if entry is None:
            missing.append(criterion["id"])
            continue
        if entry.get("met") is not True:
            unmet.append(criterion["id"])
            continue
        proof = entry.get("proof")
        if not isinstance(proof, str) or not proof.strip():
            unproven.append(criterion["id"])
            continue
        if criterion["evidence"] == "artifact" and not _resolve_artifact(proof, bases):
            problems.append(f"{criterion['id']}: proof artifact not found: {proof!r}")
        if criterion["evidence"] == "command" and len(proof.strip()) < 2:
            problems.append(f"{criterion['id']}: command proof too short")
    ok = not missing and not unmet and not unproven and not problems
    return {"ok": ok, "missing": missing, "unmet": unmet, "unproven": unproven,
            "problems": problems}


def check_review(step: Dict[str, Any], run: Optional[Dict[str, Any]],
                 spec: Dict[str, Any]) -> Dict[str, Any]:
    metadata = (run or {}).get("metadata") or {}
    problems: List[str] = []
    verdict = ""
    for field in VERDICT_FIELDS:
        if isinstance(metadata, dict) and metadata.get(field):
            verdict = str(metadata[field]).strip().lower()
            break
    if verdict not in C.APPROVE_VERDICTS:
        problems.append(f"verdict: expected one of {', '.join(C.APPROVE_VERDICTS)}, "
                        f"got {verdict or 'missing'}")
    reviewer_model = ""
    if isinstance(metadata, dict):
        reviewer_model = str(metadata.get("reviewer_model") or "").strip()
    if not reviewer_model:
        problems.append("reviewer_model: missing")
    executor_model = str(spec["executor"].get("model") or "").strip()
    if reviewer_model and executor_model and reviewer_model == executor_model:
        problems.append("reviewer_model: must differ from executor.model")
    return {"ok": not problems, "verdict": verdict, "problems": problems}


def delivery_status(step: Dict[str, Any], view: StepView) -> Dict[str, Any]:
    """A prepared delivery has a PR URL when the contract demands one."""
    metadata = (view.run or {}).get("metadata") or {}
    pr_url = _pr_url(metadata)
    if not pr_url:
        for candidate in (view.result, view.body):
            match = PR_IN_TEXT_RE.search(candidate or "")
            if match:
                pr_url = match.group(0)
                break
    if pr_url and not PR_RE.match(pr_url):
        pr_url = ""
    required = step["completion_contract"] != "local-only" or "pr_url" in step["evidence_required"]
    ok = bool(pr_url) if required else True
    return {"ok": ok, "required": required, "pr_url": pr_url}


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def workspace_flag(workspace: Dict[str, Any]) -> Optional[str]:
    """Translate the objective workspace into the native ``--workspace`` value.

    The native CLI accepts only ``scratch``, ``worktree``, ``worktree:<path>``
    and ``dir:<path>``. A bare path is rejected, and treating a worktree as a
    shared directory would silently drop isolation, so the kind is always
    encoded explicitly.
    """
    kind = str(workspace.get("kind") or "").strip()
    path = str(workspace.get("path") or "").strip()
    if kind == "scratch":
        return "scratch"
    if kind == "worktree":
        return f"worktree:{path}" if path else "worktree"
    if kind == "dir":
        return f"dir:{path}"
    return None


def _status_of(adapter: KanbanCLI, card_id: str, *, label: str) -> str:
    """Read a card status, turning a deleted card into a graph divergence."""
    try:
        payload = adapter.show(card_id)
    except AdapterError as exc:
        if exc.kind == "not_found":
            raise Divergence([
                f"{label} card {card_id} is no longer on the board",
                "  local state points at a deleted card; no card was modified",
                "  reconcile the board manually or use a new objective_id",
            ])
        raise
    return str((payload.get("task") or {}).get("status") or "")


def apply_objective(spec: Dict[str, Any], base_dir: Path, adapter: Optional[KanbanCLI],
                    state_dir: Path, *, dry_run: bool = False,
                    emit=print) -> Dict[str, Any]:
    state = load_state(state_dir) or {"cards": {}}
    cards: Dict[str, str] = dict(state.get("cards") or {})
    if state.get("objective_id") and state["objective_id"] != spec["objective_id"]:
        raise Divergence([f"state: belongs to objective {state['objective_id']!r}, "
                          f"not {spec['objective_id']!r}"])

    plan: List[Dict[str, Any]] = []
    for step in spec["steps"]:
        plan.append({
            "step": step["id"],
            "kind": step["kind"],
            "title": step["title"],
            "depends_on": list(step["depends_on"]),
            "assignee": step["assignee"],
            "card": cards.get(step["id"]),
            "action": "reuse" if cards.get(step["id"]) else "create",
        })
    if dry_run:
        emit(f"plano: {len(plan)} etapas (nenhuma escrita)")
        for item in plan:
            emit(f"  {item['action']:<6} {item['step']:<18} {item['kind']:<12} "
                 f"deps={','.join(item['depends_on']) or '-'}")
        return {"dry_run": True, "plan": plan, "cards": cards}
    if adapter is None:
        raise ValueError("adapter is required unless dry_run=True")

    # A missing child can only be created safely while the spec gate is still
    # closed. If the spec card is already done (or running), a fresh child
    # would be born ready and the dispatcher could execute a partial graph.
    spec_step = next((s for s in spec["steps"] if s["kind"] == C.KIND_SPEC), None)
    spec_card = cards.get(spec_step["id"]) if spec_step else None
    missing_steps = [s["id"] for s in spec["steps"] if s["id"] not in cards]
    if spec_card and missing_steps:
        spec_status = _status_of(adapter, spec_card, label="spec")
        if spec_status not in ("blocked", "todo"):
            raise Divergence([
                f"spec card {spec_card} is {spec_status!r} but children are missing: "
                f"{', '.join(missing_steps)}",
                "  creating them now would release work into a partially built graph",
                "  restore the spec gate to blocked/todo or use a new objective_id",
            ])

    created = 0
    reused = 0
    try:
        for step in spec["steps"]:
            card_id = cards.get(step["id"])
            payload = adapter.create(
                title=f"[{spec['objective_id']}] {step['title']}",
                body=render_body(spec, step, base_dir=base_dir),
                idempotency_key=f"lro:{spec['objective_id']}:{step['id']}",
                parents=[cards[dep] for dep in step["depends_on"]],
                assignee=step["assignee"] or None,
                workspace=workspace_flag(spec["workspace"]),
                branch=spec["workspace"]["branch"] or None,
                max_runtime=str(step["max_runtime"]),
                max_retries=step["max_retries"],
                skills=step["skills"],
                model=step["model"] or None,
                provider=step["provider"] or None,
                completion_contract=step["completion_contract"],
                goal=step["goal"],
                goal_max_turns=step["max_turns"],
                initial_status=step["initial_status"],
            )
            card_id = str(payload["id"])
            if cards.get(step["id"]) == card_id:
                reused += 1
            else:
                created += 1
                cards[step["id"]] = card_id
                save_state(state_dir, {
                    "schema_version": C.SCHEMA_VERSION,
                    "objective_id": spec["objective_id"],
                    "spec_sha256": spec["spec_sha256"],
                    "cards": cards,
                    "applied_at": now_iso(),
                })

            # The pre-loop check above can only see cards recorded in local
            # state. If that state was lost, ``create`` may reuse a spec card
            # that is already done while the remaining children do not exist.
            # Re-check immediately after the spec card is materialized, before
            # any child is created under an open gate.
            if step["kind"] == C.KIND_SPEC:
                remaining = [s["id"] for s in spec["steps"] if s["id"] not in cards]
                if remaining:
                    spec_status = _status_of(adapter, card_id, label="spec")
                    if spec_status not in ("blocked", "todo"):
                        raise Divergence([
                            f"spec card {card_id} is {spec_status!r} but children are "
                            f"missing: {', '.join(remaining)}",
                            "  local state does not cover the graph and the spec gate "
                            "is already open",
                            "  creating children now would release work into a "
                            "partially built graph; reconcile manually or use a new "
                            "objective_id",
                        ])

            try:
                card_payload = adapter.show(card_id)
            except AdapterError as exc:
                if exc.kind == "not_found":
                    raise Divergence([
                        f"{step['id']}: card {card_id} is no longer on the board",
                        "  local state points at a deleted card; no card was modified",
                        "  reconcile the board manually or use a new objective_id",
                    ])
                raise
            view = StepView(
                {**step, "_spec_sha256": spec["spec_sha256"],
                 "_objective_id": spec["objective_id"]},
                card_id, card_payload,
                [cards[dep] for dep in step["depends_on"]],
            )
            if not view.marker_ok:
                raise Divergence([
                    f"{step['id']}: card {card_id} has a divergent contract marker",
                    f"  board: {view.marker}",
                    f"  spec:  objective={spec['objective_id']} step={step['id']} "
                    f"sha256={spec['spec_sha256']}",
                    "  no card was modified; use a new objective_id or reconcile manually",
                ])
            missing = [parent for parent in view.expected_parents if parent not in view.parents]
            if missing:
                # Repairing a link is only safe while the spec gate is closed
                # and the child has not been released. Otherwise this is a
                # partial reconstruction of an already-released graph.
                if spec_card:
                    spec_status = _status_of(adapter, spec_card, label="spec")
                    if spec_status not in ("blocked", "todo"):
                        raise Divergence([
                            f"{step['id']}: card {card_id} is missing parents {missing} "
                            f"but spec card {spec_card} is {spec_status!r}",
                            "  repairing links now would rebuild an already-released graph",
                            "  no card was modified; reconcile manually or use a new objective_id",
                        ])
                if view.status in ("ready", "running"):
                    raise Divergence([
                        f"{step['id']}: card {card_id} is {view.status!r} with missing "
                        f"parents {missing}",
                        "  a released card cannot be safely relinked",
                        "  no card was modified; reconcile manually",
                    ])
                for parent in missing:
                    _status_of(adapter, parent, label="parent")
                for parent in missing:
                    adapter.link(parent, card_id)
                view = StepView(
                    {**step, "_spec_sha256": spec["spec_sha256"],
                     "_objective_id": spec["objective_id"]},
                    card_id, adapter.show(card_id),
                    [cards[dep] for dep in step["depends_on"]],
                )
            if not view.parents_ok:
                raise Divergence([
                    f"{step['id']}: parents {view.parents} != expected {view.expected_parents}",
                    "  extra parents are never removed automatically",
                ])
            # A card that is already ready/running must have satisfied parents.
            # If an external actor promoted it while a parent is still open,
            # the graph is already inconsistent: stop instead of linking or
            # "repairing" it (the adapter cannot un-dispatch a running card).
            if view.status in ("ready", "running"):
                for parent in view.expected_parents:
                    try:
                        parent_payload = adapter.show(parent)
                    except AdapterError as exc:
                        if getattr(exc, "kind", "") == "not_found":
                            raise Divergence([
                                f"{step['id']}: parent card {parent} is missing",
                                "  the objective graph cannot be verified; no repair attempted",
                            ]) from exc
                        raise
                    parent_status = str((parent_payload.get("task") or {}).get("status") or "")
                    if parent_status not in ("done", "archived"):
                        raise Divergence([
                            f"{step['id']}: card {card_id} is {view.status!r} while "
                            f"parent {parent} is {parent_status!r}",
                            "  work was released before its dependency completed",
                            "  no automatic repair or process kill is performed",
                        ])

        # Only now close the spec card: every child exists, so nothing can be
        # dispatched into a half-applied graph. A crash before this point
        # leaves the spec blocked and the graph inert, which is resumable.
        spec_step = next((s for s in spec["steps"] if s["kind"] == C.KIND_SPEC), None)
        spec_card = cards.get(spec_step["id"]) if spec_step else None
        if spec_step and spec_card:
            spec_payload = adapter.show(spec_card)
            spec_status = str((spec_payload.get("task") or {}).get("status") or "")
            if spec_status != "done":
                result = (f"contrato v{C.SCHEMA_VERSION} congelado; "
                          f"sha256={spec['spec_sha256']}")
                adapter.complete(
                    spec_card, result=result, summary=result,
                    metadata={"objective_id": spec["objective_id"],
                              "spec_sha256": spec["spec_sha256"],
                              "step": spec_step["id"], "acceptance": []})
    except AdapterError:
        save_state(state_dir, {
            "schema_version": C.SCHEMA_VERSION,
            "objective_id": spec["objective_id"],
            "spec_sha256": spec["spec_sha256"],
            "cards": cards,
            "applied_at": now_iso(),
            "partial": True,
        })
        raise

    save_state(state_dir, {
        "schema_version": C.SCHEMA_VERSION,
        "objective_id": spec["objective_id"],
        "spec_sha256": spec["spec_sha256"],
        "cards": cards,
        "applied_at": now_iso(),
    })
    emit(f"apply: {created} criados, {reused} reutilizados, {len(cards)} cartões")
    return {"created": created, "reused": reused, "cards": cards}


# ---------------------------------------------------------------------------
# status / verify
# ---------------------------------------------------------------------------

def evaluate(spec: Dict[str, Any], base_dir: Path, adapter: KanbanCLI,
             state_dir: Path) -> Dict[str, Any]:
    state = load_state(state_dir)
    problems: List[str] = []
    if not state and state_path(state_dir).exists():
        return {
            "objective_id": spec["objective_id"],
            "state": C.STATE_DRIFT,
            "spec_sha256": spec["spec_sha256"],
            "problems": ["local state exists but is unreadable (invalid JSON or "
                         "missing required fields); inspect .lro/objective.json"],
            "steps": [],
            "approval_done": False,
            "delivery": {"ok": False, "required": True, "pr_url": ""},
        }
    if not state:
        return {
            "objective_id": spec["objective_id"],
            "state": C.STATE_NOT_APPLIED,
            "spec_sha256": spec["spec_sha256"],
            "problems": ["no local state; run apply first"],
            "steps": [],
            "approval_done": False,
            "delivery": {"ok": False, "required": True, "pr_url": ""},
        }
    if state.get("objective_id") != spec["objective_id"]:
        problems.append(f"state: objective {state.get('objective_id')!r} != "
                        f"{spec['objective_id']!r}")
    if state.get("spec_sha256") != spec["spec_sha256"]:
        problems.append(f"state: spec hash {state.get('spec_sha256')} != "
                        f"{spec['spec_sha256']} (spec changed; run apply)")

    views = _load_views(adapter, spec, state, problems)
    workspace_base = Path(spec["workspace"]["path"]).expanduser() if spec["workspace"]["path"] else base_dir
    bases = [base_dir, workspace_base, base_dir / spec["artifacts_dir"]]

    step_results: List[Dict[str, Any]] = []
    for step in spec["steps"]:
        view = views.get(step["id"])
        if view is None:
            step_results.append({"step": step["id"], "kind": step["kind"],
                                 "status": "missing", "problems": ["card missing"]})
            continue
        evidence = check_evidence(step, view.run, bases)
        acceptance = check_acceptance(step, view.run, bases)
        result: Dict[str, Any] = {
            "step": step["id"],
            "kind": step["kind"],
            "card": view.card_id,
            "status": view.status,
            "executed": view.executed,
            "in_review": view.status == "review",
            "has_closing_run": view.run is not None,
            "run_id": (view.run or {}).get("id"),
            "run_outcome": (view.run or {}).get("outcome"),
            "declared_result": bool(view.result),
            "evidence": evidence,
            "acceptance": acceptance,
            "verified": False,
            "problems": [],
        }
        if step["kind"] == C.KIND_REVIEW:
            review = check_review(step, view.run, spec)
            result["review"] = review
            result["problems"] += review["problems"]
        if step["kind"] == C.KIND_CONSOLIDATE:
            delivery = delivery_status(step, view)
            result["delivery"] = delivery
            if not delivery["ok"]:
                result["problems"].append("delivery: PR URL required but missing")
        if not view.executed:
            result["problems"].append(f"status: {view.status}, expected done")
        if view.executed and view.run is None:
            result["problems"].append("run: done without a run that completed")
        if view.executed and not view.result:
            result["problems"].append("result: empty declared result")
        if not evidence["ok"]:
            result["problems"].append(f"evidence: missing={evidence['missing']} "
                                      f"problems={evidence['problems']}")
        if not acceptance["ok"]:
            result["problems"].append(f"acceptance: missing={acceptance['missing']} "
                                      f"unmet={acceptance['unmet']} "
                                      f"unproven={acceptance['unproven']} "
                                      f"problems={acceptance['problems']}")
        if step["kind"] in (C.KIND_WORK, C.KIND_REVIEW, C.KIND_CONSOLIDATE):
            result["verified"] = (view.executed and view.run is not None and bool(view.result)
                                  and evidence["ok"] and acceptance["ok"]
                                  and not result["problems"])
        step_results.append(result)

    approval = next((r for r in step_results if r["kind"] == C.KIND_APPROVAL), None)
    approval_done = bool(approval and approval.get("executed"))
    executable = [r for r in step_results if r["kind"] in
                  (C.KIND_WORK, C.KIND_REVIEW, C.KIND_CONSOLIDATE)]
    all_verified = bool(executable) and all(r.get("verified") for r in executable)
    consolidate = next((r for r in executable if r["kind"] == C.KIND_CONSOLIDATE), None)
    delivery = (consolidate or {}).get("delivery", {"ok": False, "required": True, "pr_url": ""})

    drift = bool(problems)
    # The approval card is created blocked on purpose: that is the human gate,
    # not a failure of the objective. Only an executable step being blocked is
    # a real block.
    blocked = any(r.get("status") == "blocked" for r in step_results
                  if r["kind"] != C.KIND_APPROVAL)
    if drift:
        state_name = C.STATE_DRIFT
    elif approval_done and all_verified:
        state_name = C.STATE_CLOSED
    elif approval_done and not all_verified:
        state_name = C.STATE_BLOCKED
        problems.append("approval: done but verification failed")
    elif all_verified and delivery.get("ok"):
        state_name = C.STATE_AWAITING_APPROVAL
    elif blocked:
        state_name = C.STATE_BLOCKED
    else:
        state_name = C.STATE_IN_PROGRESS

    return {
        "objective_id": spec["objective_id"],
        "state": state_name,
        "spec_sha256": spec["spec_sha256"],
        "approval_done": approval_done,
        "delivery": delivery,
        "steps": step_results,
        "problems": problems,
    }


def print_table(report: Dict[str, Any], emit=print) -> None:
    emit(f"objetivo {report['objective_id']}  estado={report['state']}")
    emit(f"{'ETAPA':<18} {'TIPO':<12} {'CARD':<10} {'STATUS':<10} {'RUN':<10} "
         f"{'RESULT':<7} {'EVID':<5} {'ACEPT':<6} VERIF")
    for row in report["steps"]:
        evidence = row.get("evidence") or {}
        acceptance = row.get("acceptance") or {}
        emit(f"{row['step']:<18} {row['kind']:<12} {str(row.get('card') or '-'):<10} "
             f"{str(row.get('status') or '-'):<10} {str(row.get('run_outcome') or '-'):<10} "
             f"{'ok' if row.get('declared_result') else '-':<7} "
             f"{'ok' if evidence.get('ok') else '-':<5} "
             f"{'ok' if acceptance.get('ok') else '-':<6} "
             f"{'sim' if row.get('verified') else 'nao'}")
    for problem in report.get("problems") or []:
        emit(f"  problema: {problem}")
    for row in report["steps"]:
        for problem in row.get("problems") or []:
            if problem not in (report.get("problems") or []):
                emit(f"  {row['step']}: {problem}")
    if report.get("delivery", {}).get("required"):
        emit(f"entrega: {'preparada' if report['delivery'].get('ok') else 'nao preparada'}"
             f" {report['delivery'].get('pr_url') or ''}".rstrip())


def verify_exit_code(report: Dict[str, Any]) -> int:
    if report["state"] == C.STATE_DRIFT:
        return C.EXIT_CONTRACT
    if report["state"] == C.STATE_CLOSED:
        return C.EXIT_OK
    if report["state"] == C.STATE_AWAITING_APPROVAL:
        return C.EXIT_PENDING_APPROVAL
    return C.EXIT_CRITERIA


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lro_objective.py",
        description="Apply/status/verify a versioned long-run objective.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("apply", "create or reuse the objective cards"),
                            ("status", "report per-step and objective state"),
                            ("verify", "check evidence and acceptance criteria")):
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--spec", required=True)
        child.add_argument("--state-dir", default=None)
        child.add_argument("--board", default=None)
        child.add_argument("--kanban-cmd", default=None)
        child.add_argument("--json", action="store_true")
        if name == "apply":
            child.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec, base_dir = load_spec(args.spec)
    except SpecError as exc:
        for error in exc.errors:
            print(f"spec: {error}", file=sys.stderr)
        return C.EXIT_CONTRACT

    state_dir = (Path(args.state_dir).expanduser().resolve() if args.state_dir
                 else (base_dir / ".lro").resolve())
    prefix = args.kanban_cmd.split() if args.kanban_cmd else None
    try:
        adapter = None
        if not (args.command == "apply" and args.dry_run):
            adapter = KanbanCLI(prefix, board=args.board, read_only=args.command != "apply")
        if args.command == "apply":
            result = apply_objective(spec, base_dir, adapter, state_dir,
                                     dry_run=args.dry_run)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            return C.EXIT_OK
        assert adapter is not None
        report = evaluate(spec, base_dir, adapter, state_dir)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_table(report)
        if args.command == "status":
            return C.EXIT_CONTRACT if report["state"] == C.STATE_DRIFT else C.EXIT_OK
        return verify_exit_code(report)
    except Divergence as exc:
        print("divergencia:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return C.EXIT_CONTRACT
    except AdapterError as exc:
        print(f"adapter: {exc}", file=sys.stderr)
        return C.EXIT_ADAPTER


if __name__ == "__main__":
    raise SystemExit(main())
