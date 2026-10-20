"""Objective contract: schema, validation, DAG checks, hashing and body rendering.

The contract is versioned (``schema_version: 1``) and complete: acceptance
criteria, required evidence and result references live in the spec itself, not
in a section reference. ``spec_sha256`` is computed over the *normalized* spec
(defaults applied, dependencies derived) so two files that mean the same thing
hash the same.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from lro_contract import (
    APPROVE_VERDICTS,
    DEFAULT_EVIDENCE,
    EVIDENCE_FIELDS,
    HUMAN_APPROVAL_ASSIGNEE,
    KIND_APPROVAL,
    KIND_CONSOLIDATE,
    KIND_REVIEW,
    KIND_SPEC,
    KIND_WORK,
    KINDS,
    SCHEMA_VERSION,
)

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
PR_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MARKER_RE = re.compile(r"<!-- lro:v1 objective=(?P<objective>[^\s]+) step=(?P<step>[^\s]+) "
                       r"sha256=(?P<sha256>[0-9a-f]{64}) -->")

TOP_KEYS = {"schema_version", "objective_id", "title", "goal", "executor", "workspace",
            "limits", "review", "artifacts_dir", "steps"}
EXECUTOR_KEYS = {"profile", "model", "provider"}
WORKSPACE_KEYS = {"kind", "path", "branch"}
LIMIT_KEYS = {"max_runtime", "max_retries", "max_turns", "stall_ticks",
              "stall_alert_ticks", "heartbeat_ttl_seconds"}
REVIEW_KEYS = {"enabled", "profile", "model", "provider"}
STEP_KEYS = {"id", "kind", "title", "body", "body_file", "depends_on", "acceptance",
             "evidence_required", "max_runtime", "max_retries", "goal", "max_turns",
             "completion_contract", "assignee", "skills", "model", "provider"}
ACCEPTANCE_KEYS = {"id", "criterion", "evidence"}

EVIDENCE_KINDS = ("proof", "artifact", "command")


class SpecError(ValueError):
    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def parse_duration(value: Any, field: str = "duration") -> int:
    """Parse ``300``, ``"300"``, ``"90s"``, ``"30m"``, ``"2h"`` or ``"1d"``."""
    if isinstance(value, bool):
        raise ValueError(f"{field}: expected a duration, got a boolean")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"{field}: must be > 0")
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        match = re.fullmatch(r"(\d+)\s*([smhd]?)", text)
        if match:
            amount = int(match.group(1))
            factor = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
            if amount > 0:
                return amount * factor
    raise ValueError(f"{field}: invalid duration {value!r} (use 300, 90s, 30m, 2h or 1d)")


def _require(condition: bool, errors: List[str], message: str) -> None:
    if not condition:
        errors.append(message)


def _unknown_keys(data: Dict[str, Any], allowed: Iterable[str], where: str,
                  errors: List[str]) -> None:
    for key in sorted(set(data) - set(allowed)):
        errors.append(f"{where}: unknown key {key!r}")


def _str(data: Dict[str, Any], key: str, where: str, errors: List[str],
         *, required: bool = True, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        errors.append(f"{where}.{key}: expected a string")
        return default
    if required and not value.strip():
        errors.append(f"{where}.{key}: must not be empty")
    return value.strip()


def load_spec(path: str | Path) -> Tuple[Dict[str, Any], Path]:
    """Load and validate a spec file. Returns ``(normalized, spec_dir)``."""
    spec_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SpecError([f"spec not found: {spec_path}"])
    except json.JSONDecodeError as exc:
        raise SpecError([f"spec is not valid JSON: {exc}"])
    if not isinstance(raw, dict):
        raise SpecError(["spec: top level must be an object"])
    return validate(raw, spec_path.parent), spec_path.parent


def validate(raw: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    errors: List[str] = []
    _unknown_keys(raw, TOP_KEYS, "spec", errors)

    version = raw.get("schema_version")
    _require(isinstance(version, int) and not isinstance(version, bool)
             and version == SCHEMA_VERSION, errors,
             f"schema_version: expected {SCHEMA_VERSION}, got {version!r}")

    objective_id = _str(raw, "objective_id", "spec", errors)
    if objective_id and not ID_RE.match(objective_id):
        errors.append("objective_id: use 3-64 chars of [a-z0-9._-], starting alphanumeric")
    title = _str(raw, "title", "spec", errors)
    goal = _str(raw, "goal", "spec", errors)

    executor_raw = raw.get("executor")
    executor: Dict[str, Any] = {"profile": "", "model": "", "provider": ""}
    if not isinstance(executor_raw, dict):
        errors.append("executor: required object with a profile")
    else:
        _unknown_keys(executor_raw, EXECUTOR_KEYS, "executor", errors)
        executor["profile"] = _str(executor_raw, "profile", "executor", errors)
        executor["model"] = _str(executor_raw, "model", "executor", errors, required=False)
        executor["provider"] = _str(executor_raw, "provider", "executor", errors, required=False)
        if executor["provider"] and not executor["model"]:
            errors.append("executor.provider: requires executor.model")

    workspace_raw = raw.get("workspace")
    workspace: Dict[str, Any] = {"kind": "", "path": "", "branch": ""}
    if not isinstance(workspace_raw, dict):
        errors.append("workspace: required object with kind=worktree|dir|scratch")
    else:
        _unknown_keys(workspace_raw, WORKSPACE_KEYS, "workspace", errors)
        kind = _str(workspace_raw, "kind", "workspace", errors)
        if kind not in ("worktree", "dir", "scratch"):
            errors.append("workspace.kind: expected worktree, dir or scratch")
        path_value = _str(workspace_raw, "path", "workspace", errors, required=False)
        branch = _str(workspace_raw, "branch", "workspace", errors, required=False)
        if kind in ("worktree", "dir") and not path_value:
            errors.append(f"workspace.path: required when kind={kind}")
        if branch and kind != "worktree":
            errors.append("workspace.branch: only valid with kind=worktree")
        workspace.update({"kind": kind, "path": path_value, "branch": branch})

    limits_raw = raw.get("limits", {})
    limits = {"max_runtime": 7200, "max_retries": 2, "max_turns": 12,
              "stall_ticks": 2, "stall_alert_ticks": 3, "heartbeat_ttl_seconds": 900}
    if not isinstance(limits_raw, dict):
        errors.append("limits: expected an object")
    else:
        _unknown_keys(limits_raw, LIMIT_KEYS, "limits", errors)
        if "max_runtime" in limits_raw:
            try:
                limits["max_runtime"] = parse_duration(limits_raw["max_runtime"], "limits.max_runtime")
            except ValueError as exc:
                errors.append(str(exc))
        for key, minimum in (("max_retries", 1), ("max_turns", 1), ("stall_ticks", 1),
                             ("stall_alert_ticks", 2), ("heartbeat_ttl_seconds", 60)):
            if key in limits_raw:
                value = limits_raw[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                    errors.append(f"limits.{key}: expected an integer >= {minimum}")
                else:
                    limits[key] = value
        if limits["stall_alert_ticks"] <= limits["stall_ticks"]:
            errors.append("limits.stall_alert_ticks: must be greater than stall_ticks")

    review_raw = raw.get("review", {"enabled": False})
    review = {"enabled": False, "profile": "", "model": "", "provider": ""}
    if not isinstance(review_raw, dict):
        errors.append("review: expected an object")
    else:
        _unknown_keys(review_raw, REVIEW_KEYS, "review", errors)
        enabled = review_raw.get("enabled", False)
        if not isinstance(enabled, bool):
            errors.append("review.enabled: expected a boolean")
            enabled = False
        review["enabled"] = enabled
        if enabled:
            # The reviewer runs in a separate process (independent-code-review),
            # so it may use the same Hermes profile. The isolation that matters
            # is a distinct model and a fresh context.
            review["profile"] = (_str(review_raw, "profile", "review", errors, required=False)
                                 or executor["profile"])
            review["model"] = _str(review_raw, "model", "review", errors)
            review["provider"] = _str(review_raw, "provider", "review", errors, required=False)
            if review["provider"] and not review["model"]:
                errors.append("review.provider: requires review.model")
            if not executor["model"]:
                errors.append("executor.model: required when review.enabled is true "
                              "(the reviewer model must be provably different)")
            if review["model"] and executor["model"] and review["model"] == executor["model"]:
                errors.append("review.model: must differ from executor.model "
                              "(independent reviewer)")

    artifacts_dir = _str(raw, "artifacts_dir", "spec", errors, required=False) or "artifacts"

    steps_raw = raw.get("steps")
    steps: List[Dict[str, Any]] = []
    if not isinstance(steps_raw, list) or not steps_raw:
        errors.append("steps: required non-empty list")
        steps_raw = []

    seen_ids = set()
    for index, item in enumerate(steps_raw):
        where = f"steps[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected an object")
            continue
        _unknown_keys(item, STEP_KEYS, where, errors)
        step_id = _str(item, "id", where, errors)
        where = f"steps[{index}]({step_id or '?'})"
        if step_id and not ID_RE.match(step_id):
            errors.append(f"{where}.id: use 3-64 chars of [a-z0-9._-]")
        if step_id in seen_ids:
            errors.append(f"{where}.id: duplicate id {step_id!r}")
        seen_ids.add(step_id)
        kind = _str(item, "kind", where, errors)
        if kind not in KINDS:
            errors.append(f"{where}.kind: expected one of {', '.join(KINDS)}")
        step_title = _str(item, "title", where, errors)

        body = _str(item, "body", where, errors, required=False)
        body_file = _str(item, "body_file", where, errors, required=False)
        if body and body_file:
            errors.append(f"{where}: use body or body_file, not both")
        if body_file:
            candidate = (base_dir / body_file).expanduser()
            if not candidate.is_file():
                errors.append(f"{where}.body_file: not found: {candidate}")
            else:
                body = candidate.read_text(encoding="utf-8").strip()
        if kind in (KIND_WORK, KIND_REVIEW, KIND_CONSOLIDATE) and not body:
            errors.append(f"{where}.body: required for kind={kind}")

        depends_raw = item.get("depends_on", [])
        depends: List[str] = []
        if not isinstance(depends_raw, list) or any(not isinstance(d, str) for d in depends_raw):
            errors.append(f"{where}.depends_on: expected a list of step ids")
        else:
            depends = [d.strip() for d in depends_raw]

        acceptance_raw = item.get("acceptance", [])
        acceptance: List[Dict[str, str]] = []
        if not isinstance(acceptance_raw, list):
            errors.append(f"{where}.acceptance: expected a list")
            acceptance_raw = []
        acceptance_ids = set()
        for a_index, criterion in enumerate(acceptance_raw):
            a_where = f"{where}.acceptance[{a_index}]"
            if not isinstance(criterion, dict):
                errors.append(f"{a_where}: expected an object")
                continue
            _unknown_keys(criterion, ACCEPTANCE_KEYS, a_where, errors)
            a_id = _str(criterion, "id", a_where, errors)
            text = _str(criterion, "criterion", a_where, errors)
            evidence = _str(criterion, "evidence", a_where, errors, required=False) or "proof"
            if evidence not in EVIDENCE_KINDS:
                errors.append(f"{a_where}.evidence: expected one of {', '.join(EVIDENCE_KINDS)}")
            if a_id in acceptance_ids:
                errors.append(f"{a_where}.id: duplicate criterion id {a_id!r}")
            acceptance_ids.add(a_id)
            acceptance.append({"id": a_id, "criterion": text, "evidence": evidence})
        if kind in (KIND_WORK, KIND_REVIEW, KIND_CONSOLIDATE) and not acceptance:
            errors.append(f"{where}.acceptance: at least one criterion is required for kind={kind}")

        evidence_raw = item.get("evidence_required", list(DEFAULT_EVIDENCE.get(kind, ())))
        evidence_required: List[str] = []
        if not isinstance(evidence_raw, list) or any(not isinstance(e, str) for e in evidence_raw):
            errors.append(f"{where}.evidence_required: expected a list of field names")
        else:
            for field in evidence_raw:
                if field not in EVIDENCE_FIELDS:
                    errors.append(f"{where}.evidence_required: unknown field {field!r} "
                                  f"(known: {', '.join(EVIDENCE_FIELDS)})")
                elif field not in evidence_required:
                    evidence_required.append(field)
        if kind in (KIND_SPEC, KIND_APPROVAL) and evidence_required:
            errors.append(f"{where}.evidence_required: not applicable to kind={kind}")

        max_runtime = limits["max_runtime"]
        if "max_runtime" in item:
            try:
                max_runtime = parse_duration(item["max_runtime"], f"{where}.max_runtime")
            except ValueError as exc:
                errors.append(str(exc))
        max_retries = limits["max_retries"]
        if "max_retries" in item:
            value = item["max_retries"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"{where}.max_retries: expected an integer >= 1")
            else:
                max_retries = value

        goal_mode = item.get("goal", False)
        if not isinstance(goal_mode, bool):
            errors.append(f"{where}.goal: expected a boolean")
            goal_mode = False
        if goal_mode and kind != KIND_WORK:
            errors.append(f"{where}.goal: only valid for kind=work")
        goal_turns: Optional[int] = None
        if "max_turns" in item:
            value = item["max_turns"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"{where}.max_turns: expected an integer >= 1")
            elif not goal_mode:
                errors.append(f"{where}.max_turns: only valid together with goal=true")
            else:
                goal_turns = value
        elif goal_mode:
            goal_turns = limits["max_turns"]

        contract = _str(item, "completion_contract", where, errors, required=False) or "local-only"
        if contract != "local-only" and not PR_RE.match(contract) and not REPO_RE.match(contract):
            errors.append(f"{where}.completion_contract: expected local-only, OWNER/REPO "
                          f"or https://github.com/OWNER/REPO/pull/N")

        assignee = _str(item, "assignee", where, errors, required=False)
        if kind == KIND_APPROVAL:
            assignee = HUMAN_APPROVAL_ASSIGNEE
        elif kind == KIND_SPEC:
            assignee = ""
        elif not assignee:
            assignee = review["profile"] if kind == KIND_REVIEW else executor["profile"]

        skills_raw = item.get("skills", [])
        skills: List[str] = []
        if not isinstance(skills_raw, list) or any(not isinstance(s, str) for s in skills_raw):
            errors.append(f"{where}.skills: expected a list of skill names")
        else:
            skills = [s.strip() for s in skills_raw if s.strip()]
        if kind == KIND_REVIEW and "independent-code-review" not in skills:
            skills.insert(0, "independent-code-review")

        model = _str(item, "model", where, errors, required=False)
        provider = _str(item, "provider", where, errors, required=False)
        if kind == KIND_REVIEW:
            model = model or review["model"]
            provider = provider or review["provider"]
            if model and executor["model"] and model == executor["model"]:
                errors.append(f"{where}.model: must differ from executor.model "
                              f"(independent reviewer)")
        else:
            model = model or executor["model"]
            provider = provider or executor["provider"]

        steps.append({
            "id": step_id,
            "kind": kind,
            "title": step_title,
            "body": body,
            "depends_on": depends,
            "acceptance": acceptance,
            "evidence_required": evidence_required,
            "max_runtime": max_runtime,
            "max_retries": max_retries,
            "goal": goal_mode,
            "max_turns": goal_turns,
            "completion_contract": contract,
            "assignee": assignee,
            "skills": skills,
            "model": model,
            "provider": provider,
            "initial_status": "blocked" if kind in (KIND_SPEC, KIND_APPROVAL) else None,
        })

    # Structural rules that need the whole graph.
    by_kind: Dict[str, List[Dict[str, Any]]] = {kind: [] for kind in KINDS}
    for step in steps:
        by_kind.setdefault(step["kind"], []).append(step)
    for kind, expected in ((KIND_SPEC, 1), (KIND_CONSOLIDATE, 1), (KIND_APPROVAL, 1)):
        if len(by_kind.get(kind, [])) != expected:
            errors.append(f"steps: exactly one {kind} step is required "
                          f"(found {len(by_kind.get(kind, []))})")
    if len(by_kind.get(KIND_WORK, [])) < 1:
        errors.append("steps: at least one work step is required")
    if review["enabled"] and len(by_kind.get(KIND_REVIEW, [])) != 1:
        errors.append("steps: exactly one review step is required when review.enabled is true")
    if not review["enabled"] and by_kind.get(KIND_REVIEW):
        errors.append("steps: review step present but review.enabled is false")

    step_by_id = {s["id"]: s for s in steps}
    work_ids = [s["id"] for s in by_kind.get(KIND_WORK, [])]
    expected_deps: Dict[str, List[str]] = {}
    if by_kind.get(KIND_SPEC):
        expected_deps[by_kind[KIND_SPEC][0]["id"]] = []
    # Work steps run one at a time in declaration order. They share the
    # objective worktree, so each one depends on the previous work step.
    previous = by_kind[KIND_SPEC][0]["id"] if by_kind.get(KIND_SPEC) else ""
    for step_id in work_ids:
        expected_deps[step_id] = [previous] if previous else []
        previous = step_id
    if by_kind.get(KIND_REVIEW):
        expected_deps[by_kind[KIND_REVIEW][0]["id"]] = list(work_ids)
    if by_kind.get(KIND_CONSOLIDATE):
        consolidate_id = by_kind[KIND_CONSOLIDATE][0]["id"]
        expected_deps[consolidate_id] = (
            [by_kind[KIND_REVIEW][0]["id"]] if by_kind.get(KIND_REVIEW) else list(work_ids))
    if by_kind.get(KIND_APPROVAL):
        approval_id = by_kind[KIND_APPROVAL][0]["id"]
        expected_deps[approval_id] = (
            [by_kind[KIND_CONSOLIDATE][0]["id"]] if by_kind.get(KIND_CONSOLIDATE) else [])

    for step in steps:
        sid = step["id"]
        expected = expected_deps.get(sid, [])
        if sid == (by_kind.get(KIND_SPEC) or [{}])[0].get("id") and step["depends_on"]:
            errors.append(f"steps({sid}): spec step cannot depend on anything")
        elif sid in expected_deps:
            if step["kind"] == KIND_WORK and not step["depends_on"]:
                step["depends_on"] = list(expected)
            elif set(step["depends_on"]) != set(expected):
                errors.append(f"steps({sid}).depends_on: expected exactly {expected} "
                              f"for kind={step['kind']}, got {step['depends_on']}")
        for dep in step["depends_on"]:
            if dep not in step_by_id:
                errors.append(f"steps({sid}).depends_on: unknown step {dep!r}")
            elif dep == sid:
                errors.append(f"steps({sid}).depends_on: a step cannot depend on itself")

    order = _topological_order(steps, errors)
    if order is not None:
        spec_id = (by_kind.get(KIND_SPEC) or [{}])[0].get("id")
        reachable = _reachable_from(spec_id, steps) if spec_id else set()
        for step in steps:
            if step["id"] not in reachable:
                errors.append(f"steps({step['id']}): not reachable from the spec step")

    if errors:
        raise SpecError(errors)

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "objective_id": objective_id,
        "title": title,
        "goal": goal,
        "executor": executor,
        "workspace": workspace,
        "limits": limits,
        "review": review,
        "artifacts_dir": artifacts_dir,
        "steps": [{k: v for k, v in step.items()} for step in steps],
    }
    normalized["steps"] = _in_order(normalized["steps"], order or [])
    normalized["spec_sha256"] = spec_hash(normalized)
    return normalized


def _topological_order(steps: List[Dict[str, Any]], errors: List[str]) -> Optional[List[str]]:
    ids = [s["id"] for s in steps]
    index = {sid: i for i, sid in enumerate(ids)}
    deps = {s["id"]: [d for d in s["depends_on"] if d in index] for s in steps}
    children: Dict[str, List[str]] = {sid: [] for sid in ids}
    indegree = {sid: 0 for sid in ids}
    for sid, parents in deps.items():
        for parent in parents:
            children[parent].append(sid)
            indegree[sid] += 1
    ready = sorted([sid for sid, degree in indegree.items() if degree == 0],
                   key=lambda sid: index[sid])
    order: List[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=lambda sid: index[sid])
    if len(order) != len(ids):
        cyclic = sorted(set(ids) - set(order), key=index.get)
        errors.append(f"steps: dependency cycle involving {cyclic}")
        return None
    return order


def _reachable_from(start: str, steps: List[Dict[str, Any]]) -> set:
    children: Dict[str, List[str]] = {s["id"]: [] for s in steps}
    for step in steps:
        for parent in step["depends_on"]:
            if parent in children:
                children[parent].append(step["id"])
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _in_order(steps: List[Dict[str, Any]], order: List[str]) -> List[Dict[str, Any]]:
    by_id = {s["id"]: s for s in steps}
    return [by_id[sid] for sid in order if sid in by_id]


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def spec_hash(normalized: Dict[str, Any]) -> str:
    payload = {k: v for k, v in normalized.items() if k != "spec_sha256"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def marker(objective_id: str, step_id: str, sha: str) -> str:
    return f"<!-- lro:v1 objective={objective_id} step={step_id} sha256={sha} -->"


def parse_marker(body: str) -> Optional[Dict[str, str]]:
    match = MARKER_RE.search(body or "")
    if not match:
        return None
    return match.groupdict()


def render_body(spec: Dict[str, Any], step: Dict[str, Any], *, base_dir: Path) -> str:
    objective_id = spec["objective_id"]
    sha = spec["spec_sha256"]
    lines = [
        f"# Objetivo {objective_id}: {spec['title']}",
        "",
        spec["goal"],
        "",
        f"## Etapa {step['id']} ({step['kind']}): {step['title']}",
        "",
        step["body"],
        "",
        "## Critérios de aceitação (contrato versionado)",
        "",
    ]
    if step["acceptance"]:
        for criterion in step["acceptance"]:
            lines.append(f"- {criterion['id']} ({criterion['evidence']}): {criterion['criterion']}")
    else:
        lines.append("- (nenhum: etapa de registro, não executável)")
    lines += ["", "## Evidência obrigatória", ""]
    if step["evidence_required"]:
        for field in step["evidence_required"]:
            lines.append(f"- `{field}`")
        lines += [
            "",
            "Preencha cada campo em `metadata` na chamada de conclusão. Campo vazio, "
            "lista vazia ou caminho inexistente reprovam a verificação.",
        ]
    else:
        lines.append("- (nenhuma)")
    lines += [
        "",
        "## Como concluir",
        "",
        "1. Execute o trabalho e registre os artefatos no diretório de artefatos do objetivo.",
        "2. Chame a conclusão com resultado não vazio e `metadata` contendo os campos acima,",
        "   mais `acceptance` como lista de `{id, met, proof}`.",
        "3. Não faça merge, release ou instalação nesta versão.",
        "",
    ]
    if step["kind"] == KIND_REVIEW:
        lines += [
            "## Revisão independente",
            "",
            "Use a skill `independent-code-review` em contexto separado. Registre",
            "`verdict` (approve/approved/pass ou changes_requested), `reviewer_model`,",
            "`reviewer_provider` e `findings` em `metadata`. O modelo do revisor deve",
            "ser diferente do modelo executor.",
            "",
        ]
    if step["kind"] == KIND_APPROVAL:
        lines += [
            "## Gate humano",
            "",
            "Este card é o gate de aprovação. Ele não é despachável: o assignee",
            f"`{HUMAN_APPROVAL_ASSIGNEE}` não corresponde a um perfil e o dispatcher o ignora.",
            "Um humano revisa o PR e conclui este card manualmente. A conclusão dele",
            "encerra o objetivo; nenhum agente deve concluí-lo.",
            "",
        ]
    if step["completion_contract"] != "local-only":
        lines += [
            "## Contrato de conclusão",
            "",
            f"`{step['completion_contract']}`",
            "",
        ]
    lines.append(marker(objective_id, step["id"], sha))
    return "\n".join(lines).rstrip() + "\n"
