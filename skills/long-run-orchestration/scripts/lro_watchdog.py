"""Read-only watchdog for a long-run objective.

One invocation = one tick (cron friendly). Every tick is evaluated on its own:
the progress digest is used to decide whether the step moved, but time-based
rules (runtime exhausted) are computed even when the digest did not change.

The watchdog never writes to the board: it uses a read-only adapter that
refuses anything but ``show``/``diagnostics``. Its only writes are its own
journal, alert log and latest report inside ``--state-dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import lro_contract as C
from lro_adapter import AdapterError, KanbanCLI
from lro_objective import STATE_FILE, load_state
from lro_spec import SpecError, canonical_json, load_spec, parse_marker

JOURNAL_FILE = "watchdog.jsonl"
ALERTS_FILE = "alerts.jsonl"
LATEST_FILE = "watchdog-latest.json"
RUNTIME_GRACE_SECONDS = 60
# The watchdog has its own gateway-outage code: reusing 2 would collide with
# EXIT_PENDING_APPROVAL used by ``verify`` and make wrappers unable to tell an
# outage from a pending human gate.
WATCH_EXIT_GATEWAY = 5

# Objective-level watchdog states (also documented in references/operations.md).
WATCH_OVERALL_DONE = "done"
WATCH_OVERALL_RUNNING = "running"
WATCH_OVERALL_STALLED = "stalled"
WATCH_OVERALL_RUNTIME = "runtime_exhausted"
WATCH_OVERALL_BLOCKED = "blocked"
WATCH_OVERALL_DRIFT = "drift"
WATCH_OVERALL_GATEWAY = "gateway_unavailable"
WATCH_OVERALL_UNKNOWN = "unknown"


def _now(value: Optional[str]) -> float:
    if value:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                return float(text)
            except ValueError as exc:
                raise ValueError(f"invalid timestamp {value!r}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    env = os.environ.get("LRO_NOW")
    if env:
        return _now(env)
    return datetime.now(timezone.utc).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat()


def _ts(value: Any) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return _now(str(value))
    except (ValueError, TypeError):
        return 0.0


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def _as_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("task_id") or "")
    return str(item or "")


def _task(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("task") or {}


def _parents(payload: Dict[str, Any]) -> List[str]:
    raw = payload.get("parents")
    if raw is None:
        raw = _task(payload).get("parents") or []
    return [value for value in (_as_id(item) for item in raw) if value]


def _runs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    runs = [run for run in (payload.get("runs") or []) if isinstance(run, dict)]
    return sorted(runs, key=lambda run: (_ts(run.get("started_at")), str(run.get("id", ""))))


def _body(payload: Dict[str, Any]) -> str:
    task = _task(payload)
    return str(task.get("body") or task.get("description") or "")


def _summary(payload: Dict[str, Any]) -> str:
    latest = payload.get("latest_summary")
    if isinstance(latest, dict):
        latest = latest.get("summary") or latest.get("body")
    return str(latest or "")


def _events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = payload.get("events") or []
    return [event for event in events if isinstance(event, dict)]


def _progress_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Events that represent work, excluding pure liveness signals.

    A heartbeat proves the runtime is alive; it is not progress. Counting it in
    the digest would reset the stagnation counter forever on an idle run.
    """
    return [event for event in _events(payload)
            if "heartbeat" not in str(event.get("kind") or "").lower()]


def progress_digest(payload: Dict[str, Any]) -> str:
    """Stable digest of everything that counts as observable progress."""
    task = _task(payload)
    events = _progress_events(payload)
    last_event = events[-1] if events else {}
    runs = _runs(payload)
    current_run = runs[-1] if runs else {}
    material = {
        "status": task.get("status"),
        "assignee": task.get("assignee"),
        "run_id": current_run.get("id"),
        "run_outcome": current_run.get("outcome"),
        "run_ended": current_run.get("ended_at"),
        "summary": _hash(_summary(payload)) if _summary(payload) else "",
        "events": len(events),
        "last_event": [last_event.get("kind"), last_event.get("created_at"),
                       _hash(last_event.get("payload"))] if last_event else None,
        "comments": len(payload.get("comments") or []),
        "children": len(payload.get("children") or []),
    }
    return _hash(material)


def read_last_record(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    last: Optional[Dict[str, Any]] = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    last = record
    except OSError:
        return None
    return last


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def alert_keys(path: Path) -> set:
    keys = set()
    if not path.is_file():
        return keys
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("key"):
                    keys.add(str(record["key"]))
    except OSError:
        pass
    return keys


def _state_for(status: str, *, gateway_ok: bool, drift: bool, run: Optional[Dict[str, Any]],
               runtime_exhausted: bool, stalled: bool) -> str:
    if not gateway_ok:
        return C.WATCH_UNKNOWN
    if drift:
        return C.WATCH_UNKNOWN
    if status == "done":
        return C.WATCH_DONE
    if status == "blocked":
        return C.WATCH_BLOCKED
    if status == "running":
        if runtime_exhausted:
            return C.WATCH_RUNTIME_EXHAUSTED
        if stalled:
            return C.WATCH_RUNNING_NO_PROGRESS
        return C.WATCH_RUNNING
    if status == "review":
        return C.WATCH_REVIEW
    if status in ("triage", "todo", "scheduled", "ready"):
        return C.WATCH_IDLE
    if status == "archived":
        return C.WATCH_UNKNOWN
    return C.WATCH_UNKNOWN


def tick(spec: Dict[str, Any], adapter: KanbanCLI, state_dir: Path, *,
         at: Optional[str] = None, emit=print) -> Dict[str, Any]:
    now = _now(at)
    journal_path = state_dir / JOURNAL_FILE
    alerts_path = state_dir / ALERTS_FILE
    previous = read_last_record(journal_path) or {}
    previous_steps = {item.get("step"): item for item in previous.get("steps") or []
                      if isinstance(item, dict)}
    seen_alerts = alert_keys(alerts_path)
    ledger_path = state_dir / STATE_FILE
    ledger_exists = ledger_path.is_file()
    state = load_state(state_dir)

    alerts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    gateway_ok = True
    drift = False

    if not ledger_exists:
        drift = True
        _alert(alerts, seen_alerts, kind=C.ALERT_DRIFT, step="*",
               run_id=None, bucket="ledger-missing", now=now,
               detail=f"no ledger at {ledger_path}; run apply before watching")
    elif state is None:
        drift = True
        _alert(alerts, seen_alerts, kind=C.ALERT_DRIFT, step="*",
               run_id=None, bucket="ledger-unreadable", now=now,
               detail=f"ledger {ledger_path} is unreadable or malformed")
    else:
        if state.get("objective_id") != spec["objective_id"]:
            drift = True
            _alert(alerts, seen_alerts, kind=C.ALERT_DRIFT, step="*",
                   run_id=None, bucket="ledger-objective", now=now,
                   detail=(f"ledger objective {state.get('objective_id')!r} != "
                           f"{spec['objective_id']!r}"))
        if state.get("spec_sha256") != spec["spec_sha256"]:
            drift = True
            _alert(alerts, seen_alerts, kind=C.ALERT_DRIFT, step="*",
                   run_id=None, bucket="ledger-spec", now=now,
                   detail="ledger spec hash differs from the current spec")
    cards = (state or {}).get("cards") or {}

    for step in spec["steps"]:
        card_id = cards.get(step["id"])
        entry: Dict[str, Any] = {
            "step": step["id"], "kind": step["kind"], "card": card_id,
            "status": "missing", "run_id": None, "run_outcome": None,
            "process": "unknown", "heartbeat": "unobservable",
            "digest": "", "digest_changed": False, "stall_ticks": 0,
            "elapsed_seconds": None, "runtime_exhausted": False,
            "state": C.WATCH_UNKNOWN, "note": "",
        }
        if not card_id:
            entry["note"] = "no card in local state"
            steps.append(entry)
            continue
        try:
            payload = adapter.show(card_id)
        except AdapterError as exc:
            entry["state"] = C.WATCH_UNKNOWN
            if exc.kind == "not_found":
                # The board answered; the card is gone. That is graph drift,
                # not an outage, and the watchdog must not recreate it.
                drift = True
                entry["note"] = f"card {card_id} is not on the board"
                _alert(alerts, seen_alerts, kind=C.ALERT_DRIFT, step=step["id"],
                       run_id=None, bucket="card-missing", now=now,
                       detail=entry["note"])
            else:
                gateway_ok = False
                entry["note"] = f"show failed: {exc.kind}"
            steps.append(entry)
            continue

        task = _task(payload)
        status = str(task.get("status") or "unknown")
        runs = _runs(payload)
        run = runs[-1] if runs else None
        digest = progress_digest(payload)
        marker = parse_marker(_body(payload))
        marker_ok = bool(marker and marker.get("sha256") == spec["spec_sha256"]
                         and marker.get("step") == step["id"])
        approval_breach = ""
        if step["kind"] == C.KIND_APPROVAL:
            # The human gate must stay non-spawnable. A running card means an
            # agent claimed it; any other assignee means the sentinel was
            # replaced and ``default_assignee`` could route it to a worker.
            gate_assignee = str(task.get("assignee") or "")
            if status == "running":
                approval_breach = "human approval gate is running"
            elif gate_assignee != C.HUMAN_APPROVAL_ASSIGNEE:
                approval_breach = (f"human approval gate assignee is "
                                   f"{gate_assignee or '(empty)'}, expected "
                                   f"{C.HUMAN_APPROVAL_ASSIGNEE}")
        missing_deps = [dep for dep in step["depends_on"] if dep not in cards]
        parents_expected = {cards[dep] for dep in step["depends_on"] if dep in cards}
        parents_ok = set(_parents(payload)) == parents_expected
        # An unexpectedly released card is drift: the adapter must not repair
        # it, but the operator needs to know the graph is already inconsistent.
        released_early: List[str] = []
        parent_transport_error = ""
        if status in ("ready", "running"):
            for parent in sorted(parents_expected):
                try:
                    parent_payload = adapter.show(parent)
                except AdapterError as exc:
                    if exc.kind == "not_found":
                        released_early.append(f"{parent}:missing")
                    else:
                        # A transport/CLI failure says nothing about the graph;
                        # it must surface as an outage, not as drift.
                        gateway_ok = False
                        parent_transport_error = f"{parent}:{exc.kind}"
                    continue
                parent_status = str((parent_payload.get("task") or {}).get("status") or "")
                if parent_status not in ("done", "archived"):
                    released_early.append(f"{parent}:{parent_status or 'unknown'}")
        step_drift = (not marker_ok or not parents_ok or bool(released_early)
                      or bool(approval_breach) or bool(missing_deps))
        drift = drift or step_drift

        previous_entry = previous_steps.get(step["id"]) or {}
        same_digest = bool(previous_entry.get("digest")) and previous_entry.get("digest") == digest
        if status == "running":
            stall_ticks = int(previous_entry.get("stall_ticks") or 0) + 1 if same_digest else 1
        else:
            stall_ticks = 0

        started = _ts((run or {}).get("started_at"))
        ended = _ts((run or {}).get("ended_at"))
        elapsed = None
        if status == "running" and started and not ended:
            elapsed = max(0, int(now - started))
        runtime_exhausted = bool(elapsed is not None
                                 and elapsed >= step["max_runtime"] + RUNTIME_GRACE_SECONDS)

        if ended:
            process = "ended"
        elif status == "running":
            process = "declared_running"
        else:
            process = "idle"
        heartbeat = "unobservable"
        heartbeat_value = task.get("last_heartbeat_at") or (run or {}).get("last_heartbeat_at")
        if heartbeat_value:
            age = int(now - _ts(heartbeat_value))
            heartbeat = f"{age}s ago"

        stalled = stall_ticks >= spec["limits"]["stall_alert_ticks"]
        entry.update({
            "status": status,
            "run_id": (run or {}).get("id"),
            "run_outcome": (run or {}).get("outcome"),
            "process": process,
            "heartbeat": heartbeat,
            "digest": digest,
            "digest_changed": bool(previous_entry.get("digest")) and not same_digest,
            "stall_ticks": stall_ticks,
            "elapsed_seconds": elapsed,
            "runtime_exhausted": runtime_exhausted,
            "state": _state_for(status, gateway_ok=gateway_ok, drift=step_drift, run=run,
                                runtime_exhausted=runtime_exhausted, stalled=stalled),
        })
        if step_drift or parent_transport_error:
            reasons: List[str] = []
            if not marker_ok:
                reasons.append("contract marker diverges from spec")
            if missing_deps:
                reasons.append("ledger is missing dependency cards: "
                               + ", ".join(missing_deps))
            if not parents_ok:
                reasons.append("parents diverge from spec")
            if released_early:
                reasons.append("released before parents completed: "
                               + ", ".join(released_early))
            if approval_breach:
                reasons.append(approval_breach)
            if parent_transport_error:
                reasons.append("parent lookup failed: " + parent_transport_error)
            entry["note"] = "; ".join(reasons)
        if status == "running" and ended:
            # The card still says running, but its newest run already ended.
            # That is an observation gap, not progress: report it instead of
            # pretending the step is alive or silently treating it as done.
            entry["state"] = C.WATCH_UNKNOWN
            entry["note"] = "card says running but its newest run already ended"

        if status == "running" and runtime_exhausted:
            _alert(alerts, seen_alerts, kind=C.ALERT_RUNTIME_EXHAUSTED, step=step["id"],
                   run_id=entry["run_id"], bucket="once", now=now,
                   detail=f"elapsed {elapsed}s >= {step['max_runtime']}s + grace")
        if status == "running" and stalled:
            bucket = stall_ticks // spec["limits"]["stall_alert_ticks"]
            _alert(alerts, seen_alerts, kind=C.ALERT_STAGNATION, step=step["id"],
                   run_id=entry["run_id"], bucket=str(bucket), now=now,
                   detail=f"same digest for {stall_ticks} ticks")
        if step_drift:
            _alert(alerts, seen_alerts, kind=C.ALERT_DRIFT, step=step["id"],
                   run_id=entry["run_id"], bucket=spec["spec_sha256"][:12], now=now,
                   detail=entry["note"])
        if entry["state"] == C.WATCH_UNKNOWN and not step_drift:
            _alert(alerts, seen_alerts, kind=C.ALERT_UNKNOWN_STATE, step=step["id"],
                   run_id=entry["run_id"], bucket=status, now=now,
                   detail=entry["note"] or f"unexpected status {status!r}")
        steps.append(entry)

    if not gateway_ok:
        was_ok = bool(previous.get("gateway_ok", True))
        if was_ok:
            _alert(alerts, seen_alerts, kind=C.ALERT_GATEWAY_UNAVAILABLE, step="*",
                   run_id=None, bucket="outage", now=now,
                   detail="kanban CLI unreachable")

    overall = _overall(steps, gateway_ok=gateway_ok, drift=drift,
                       stalled=any(item["state"] == C.WATCH_RUNNING_NO_PROGRESS
                                   for item in steps),
                       runtime=any(item["state"] == C.WATCH_RUNTIME_EXHAUSTED
                                   for item in steps))
    record = {
        "at": _iso(now),
        "objective_id": spec["objective_id"],
        "spec_sha256": spec["spec_sha256"],
        "gateway_ok": gateway_ok,
        "state": overall,
        "steps": steps,
        "alerts": [alert["kind"] for alert in alerts],
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    append_jsonl(journal_path, record)
    for alert in alerts:
        append_jsonl(alerts_path, alert)
    tmp = state_dir / (LATEST_FILE + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, state_dir / LATEST_FILE)

    emit(f"watchdog {record['at']} objetivo={spec['objective_id']} estado={overall}")
    for item in steps:
        emit(f"  {item['step']:<18} {item['status']:<10} run={str(item['run_id'] or '-'):<10} "
             f"stall={item['stall_ticks']:<3} {item['state']}"
             + (f"  {item['note']}" if item["note"] else ""))
    if alerts:
        emit("alertas: " + ", ".join(alert["kind"] for alert in alerts))
    return record


def _alert(alerts: List[Dict[str, Any]], seen: set, *, kind: str, step: str,
           run_id: Optional[str], bucket: str, detail: str, now: float) -> None:
    key = f"{step}:{kind}:{run_id or '-'}:{bucket}"
    if key in seen:
        return
    seen.add(key)
    alerts.append({"key": key, "kind": kind, "step": step, "run_id": run_id,
                   "detail": detail, "at": _iso(now)})


def _overall(steps: List[Dict[str, Any]], *, gateway_ok: bool, drift: bool,
             stalled: bool, runtime: bool) -> str:
    if not gateway_ok:
        return WATCH_OVERALL_GATEWAY
    if drift:
        return WATCH_OVERALL_DRIFT
    if runtime:
        return WATCH_OVERALL_RUNTIME
    if stalled:
        return WATCH_OVERALL_STALLED
    if any(item["state"] == C.WATCH_UNKNOWN for item in steps):
        return WATCH_OVERALL_UNKNOWN
    if steps and all(item["status"] == "done" for item in steps):
        return WATCH_OVERALL_DONE
    if any(item["status"] == "blocked" for item in steps):
        return WATCH_OVERALL_BLOCKED
    return WATCH_OVERALL_RUNNING


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lro_watchdog.py",
        description="One read-only watchdog tick for a long-run objective.")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--board", default=None)
    parser.add_argument("--kanban-cmd", default=None)
    parser.add_argument("--now", default=None, help="ISO-8601 or epoch (tests/replay)")
    parser.add_argument("--json", action="store_true")
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
        adapter = KanbanCLI(prefix, board=args.board, read_only=True)
        record = tick(spec, adapter, state_dir, at=args.now,
                      emit=(lambda *_a, **_k: None) if args.json else print)
    except AdapterError as exc:
        print(f"adapter: {exc}", file=sys.stderr)
        return C.EXIT_ADAPTER
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    if record["state"] == WATCH_OVERALL_GATEWAY:
        return WATCH_EXIT_GATEWAY
    if record["state"] == WATCH_OVERALL_DRIFT:
        return C.EXIT_CONTRACT
    return C.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
