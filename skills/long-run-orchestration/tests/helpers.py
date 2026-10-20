"""Shared fixtures for the long-run-orchestration test suite.

The tests use a controlled in-memory adapter. It is not an integration with
the real Hermes gateway: it records the native calls, can inject failures and
lets the tests observe intermediate graph states deterministically.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lro_adapter import AdapterError  # noqa: E402


def base_spec() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "objective_id": "obj-test",
        "title": "Objetivo de teste",
        "goal": "Provar o contrato e o grafo.",
        "executor": {"profile": "default", "model": "executor-model", "provider": "custom"},
        "workspace": {"kind": "scratch"},
        "limits": {"max_runtime": "30m", "max_retries": 2},
        "review": {"enabled": True, "model": "reviewer-model", "provider": "custom"},
        "artifacts_dir": "artifacts",
        "steps": [
            {
                "id": "spec",
                "kind": "spec",
                "title": "Congelar contrato",
                "body": "Contrato.",
            },
            {
                "id": "work-a",
                "kind": "work",
                "title": "Primeira etapa",
                "body": "Fazer A.",
                "acceptance": [{"id": "a1", "criterion": "A feito", "evidence": "proof"}],
                "evidence_required": ["notes"],
            },
            {
                "id": "work-b",
                "kind": "work",
                "title": "Segunda etapa",
                "body": "Fazer B.",
                "acceptance": [{"id": "b1", "criterion": "B feito", "evidence": "proof"}],
                "evidence_required": ["notes"],
            },
            {
                "id": "review",
                "kind": "review",
                "title": "Revisão independente",
                "body": "Revisar.",
                "depends_on": ["work-a", "work-b"],
                "acceptance": [{"id": "r1", "criterion": "Revisado", "evidence": "proof"}],
                "evidence_required": ["notes"],
            },
            {
                "id": "consolidate",
                "kind": "consolidate",
                "title": "Consolidar",
                "body": "Consolidar.",
                "depends_on": ["review"],
                "acceptance": [{"id": "c1", "criterion": "Consolidado", "evidence": "proof"}],
                "evidence_required": ["notes"],
            },
            {
                "id": "approval",
                "kind": "approval",
                "title": "Gate humano",
                "body": "Aprovar.",
                "depends_on": ["consolidate"],
            },
        ],
    }


class FakeKanban:
    """Minimal native adapter double with observable, injectable behavior."""

    def __init__(self) -> None:
        self.cards: Dict[str, Dict[str, Any]] = {}
        self.by_key: Dict[str, str] = {}
        self.calls: List[tuple] = []
        self.fail_create_at: Optional[str] = None
        self.fail_link_at: Optional[str] = None
        self.fail_link_child_step: Optional[str] = None
        self.interrupt_after_create_step: Optional[str] = None
        self.divergent_keys: Dict[str, str] = {}
        self.fail_show_ids: set = set()
        self.complete_calls = 0
        self.created_states: Dict[str, Dict[str, Any]] = {}
        self.snapshots: List[tuple] = []
        self._next = 1

    def _step_of(self, key: str) -> str:
        return key.rsplit(":", 1)[-1]

    def _status_for(self, parents: List[str], initial_status: Optional[str]) -> str:
        # Mirrors kanban_db.initial_task_state: initial blocked wins; a parent
        # that is not done parks the child in todo; otherwise it is ready.
        if initial_status == "blocked":
            return "blocked"
        if any(self.cards[parent]["status"] != "done" for parent in parents):
            return "todo"
        return "ready"

    def _event(self, card_id: str, kind: str) -> None:
        self.cards[card_id].setdefault("events", []).append(kind)

    def _sticky(self, card_id: str) -> bool:
        # Native rule: the newest blocked/unblocked event decides. An
        # initial-status block appends a ``blocked`` event, so it is sticky.
        latest = ""
        for event in self.cards[card_id].get("events", []):
            if event in ("blocked", "unblocked"):
                latest = event
        return latest == "blocked"

    def can_execute(self, card_id: str) -> bool:
        """True when the native dispatcher could execute or promote this card.

        Derived from the installed runtime, not from the test expectations:
        ``recompute_ready`` promotes todo/blocked cards whose parents are all
        done/archived, unless the newest block is still sticky. ``ready`` and
        ``running`` are already dispatchable. A card with no parents satisfies
        ``all([])`` and is therefore executable, which is why every child must
        be linked to its gate.
        """
        card = self.cards[card_id]
        status = card["status"]
        if status not in ("ready", "running", "todo", "blocked"):
            return False
        if status in ("todo", "blocked") and self._sticky(card_id):
            return False
        return all(self.cards[parent]["status"] in ("done", "archived")
                   for parent in card["parents"])

    def eligible(self, card_id: str) -> bool:
        """Backward-compatible alias for :meth:`can_execute`."""
        return self.can_execute(card_id)

    def eligible_steps(self) -> List[str]:
        eligible = []
        for key, card_id in self.by_key.items():
            if self.eligible(card_id):
                eligible.append(self._step_of(key))
        return sorted(eligible)

    def ancestors(self, card_id: str) -> set:
        seen = set()
        stack = list(self.cards[card_id]["parents"])
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.cards[current]["parents"])
        return seen

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        key = kwargs["idempotency_key"]
        step = self._step_of(key)
        self.calls.append(("create", step, copy.deepcopy(kwargs)))
        if self.fail_create_at == step:
            raise AdapterError("injected", "create failure")
        if key in self.by_key:
            card_id = self.by_key[key]
            if key in self.divergent_keys:
                self.cards[card_id]["body"] = self.divergent_keys[key]
            return {"id": card_id}
        parents = list(kwargs.get("parents") or [])
        card_id = f"t{self._next}"
        self._next += 1
        status = self._status_for(parents, kwargs.get("initial_status"))
        self.cards[card_id] = {
            "id": card_id,
            "title": kwargs["title"],
            "body": kwargs["body"],
            "status": status,
            "parents": [],
            "runs": [],
            "metadata": {},
            "result": "",
            "events": [],
            "event_log": [],
            "comments": [],
            "children": [],
            "assignee": kwargs.get("assignee"),
            "last_heartbeat_at": None,
        }
        self.by_key[key] = card_id
        # Native create_task inserts the task and all parent links inside one
        # write transaction before returning. There is no unlinked window, and
        # a failing link rolls the whole insert back.
        try:
            for parent in parents:
                self.link(parent, card_id)
        except Exception:
            del self.cards[card_id]
            del self.by_key[key]
            raise
        self._event(card_id, "created")
        if status == "blocked":
            self._event(card_id, "blocked")
        self.created_states[step] = {
            "status": status,
            "parents": list(self.cards[card_id]["parents"]),
        }
        self.snapshots.append((step, self.eligible_steps()))
        if self.interrupt_after_create_step == step:
            # Crash after the native transaction committed, before apply can
            # record the returned id. The card and its links are already durable.
            raise AdapterError("interrupted", "interruption after create")
        return {"id": card_id}

    def show(self, card_id: str) -> Dict[str, Any]:
        self.calls.append(("show", card_id))
        if card_id in self.fail_show_ids:
            raise AdapterError("injected", f"show failure for {card_id}")
        if card_id not in self.cards:
            raise AdapterError("not_found", f"no such task: {card_id}")
        card = self.cards[card_id]
        return {
            "task": {
                "id": card["id"],
                "title": card["title"],
                "body": card["body"],
                "status": card["status"],
                "parents": list(card["parents"]),
                "metadata": copy.deepcopy(card["metadata"]),
                "result": card["result"],
                "assignee": card.get("assignee"),
                "last_heartbeat_at": card.get("last_heartbeat_at"),
                "events": copy.deepcopy(card.get("event_log") or []),
                "comments": copy.deepcopy(card.get("comments") or []),
                "children": copy.deepcopy(card.get("children") or []),
            },
            "runs": copy.deepcopy(card["runs"]),
            "parents": list(card["parents"]),
            "events": copy.deepcopy(card.get("event_log") or []),
            "comments": copy.deepcopy(card.get("comments") or []),
            "children": copy.deepcopy(card.get("children") or []),
        }

    def link(self, parent: str, child: str) -> Dict[str, Any]:
        self.calls.append(("link", parent, child))
        if self.fail_link_at == parent or self.fail_link_at == child:
            raise AdapterError("injected", f"link failure {parent}->{child}")
        if self.fail_link_child_step:
            child_key = next((k for k, v in self.by_key.items() if v == child), "")
            if self._step_of(child_key) == self.fail_link_child_step:
                raise AdapterError("injected", f"link failure for {child}")
        if parent not in self.cards or child not in self.cards:
            raise AdapterError("not_found", "unknown card")
        if parent not in self.cards[child]["parents"]:
            self.cards[child]["parents"].append(parent)
        return {"ok": True}

    def complete(self, card_id: str, *, result: str, summary: str = "",
                 metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.calls.append(("complete", card_id, metadata or {}))
        self.complete_calls += 1
        card = self.cards[card_id]
        card["status"] = "done"
        card["result"] = result
        card["metadata"] = copy.deepcopy(metadata or {})
        card["runs"].append({
            "id": f"run{len(card['runs']) + 1}",
            "outcome": "completed",
            "result": result,
            "summary": summary,
            "metadata": copy.deepcopy(metadata or {}),
        })
        self._event(card_id, "completed")
        return {"id": card_id, "status": "done"}

    def find(self, step: str) -> Optional[str]:
        return self.by_key.get(f"lro:obj-test:{step}")

    def parents_of(self, step: str) -> List[str]:
        card_id = self.find(step)
        return list(self.cards[card_id]["parents"]) if card_id else []

    def step_id_of(self, card_id: str) -> str:
        for key, value in self.by_key.items():
            if value == card_id:
                return self._step_of(key)
        return ""


def write_spec(tmp: Path, spec: Optional[Dict[str, Any]] = None) -> Path:
    path = tmp / "spec.json"
    path.write_text(json.dumps(spec or base_spec(), indent=2), encoding="utf-8")
    return path
