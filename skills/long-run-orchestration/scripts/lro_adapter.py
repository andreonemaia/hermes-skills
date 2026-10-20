"""Thin adapter over the native ``hermes kanban`` CLI.

Design rules:

* No shell. Arguments are passed as a list to ``subprocess.run``.
* Only allow-listed subcommands exist. The watchdog gets a read-only adapter,
  so a bug cannot mutate the board.
* Every failure is classified, never swallowed: ``invalid_json``, ``cli_error``,
  ``not_found``, ``timeout`` and ``forbidden_command`` are distinct so callers
  can decide whether the board is unreachable (gateway down), a card is gone,
  or the output was merely noisy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Sequence

READ_ONLY_COMMANDS = frozenset({"show", "list", "ls", "runs", "diagnostics", "diag", "stats"})
WRITE_COMMANDS = frozenset({"create", "link", "complete"})
ALLOWED_COMMANDS = READ_ONLY_COMMANDS | WRITE_COMMANDS


class AdapterError(RuntimeError):
    """A native CLI call failed.

    ``kind`` is one of ``invalid_json``, ``cli_error``, ``timeout`` or
    ``forbidden_command``. ``detail`` is safe to print (stderr tail, no secrets).
    """

    def __init__(self, kind: str, detail: str, *, returncode: Optional[int] = None,
                 command: Sequence[str] = ()):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.returncode = returncode
        self.command = tuple(command)


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


class KanbanCLI:
    """Minimal, auditable wrapper around the Kanban CLI."""

    def __init__(self, prefix: Optional[Sequence[str]] = None, *, board: Optional[str] = None,
                 timeout: float = 120.0, read_only: bool = False,
                 runner=None):
        if prefix is None:
            prefix = default_prefix()
        self.prefix = list(prefix)
        self.board = board
        self.timeout = timeout
        self.read_only = read_only
        self._runner = runner or self._subprocess_runner

    # -- plumbing ---------------------------------------------------------
    def _subprocess_runner(self, argv: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

    def command_for(self, args: Sequence[str]) -> List[str]:
        argv = list(self.prefix)
        if self.board:
            argv += ["--board", self.board]
        argv += list(args)
        return argv

    def call(self, *args: str) -> Any:
        stdout = self.call_text(*args)
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            raise AdapterError("invalid_json", _tail(stdout))

    def call_text(self, *args: str) -> str:
        """Run an allow-listed subcommand and return its stdout as text."""
        if not args:
            raise AdapterError("cli_error", "empty command")
        sub = args[0]
        if sub not in ALLOWED_COMMANDS:
            raise AdapterError("forbidden_command", f"subcommand not allowed: {sub}")
        if self.read_only and sub not in READ_ONLY_COMMANDS:
            raise AdapterError("forbidden_command", f"read-only adapter refused: {sub}")
        argv = self.command_for(args)
        try:
            proc = self._runner(argv)
        except subprocess.TimeoutExpired:
            raise AdapterError("timeout", f"timed out after {self.timeout}s", command=argv)
        except OSError as exc:
            raise AdapterError("cli_error", f"could not execute: {exc}", command=argv)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            detail = _tail(stderr or stdout)
            kind = "cli_error"
            if sub == "show" and stderr.strip().startswith("no such task:"):
                kind = "not_found"
            raise AdapterError(kind, detail, returncode=proc.returncode, command=argv)
        return stdout

    # -- read side --------------------------------------------------------
    def show(self, task_id: str) -> Dict[str, Any]:
        payload = self.call("show", task_id, "--json")
        if not isinstance(payload, dict) or "task" not in payload:
            raise AdapterError("invalid_json", "show --json returned an unexpected shape")
        return payload

    def diagnostics(self) -> List[Dict[str, Any]]:
        payload = self.call("diagnostics", "--json")
        if not isinstance(payload, list):
            raise AdapterError("invalid_json", "diagnostics --json did not return a list")
        return payload

    # -- write side -------------------------------------------------------
    def create(self, *, title: str, body: str, idempotency_key: str,
               parents: Iterable[str] = (), assignee: Optional[str] = None,
               workspace: Optional[str] = None, branch: Optional[str] = None,
               max_runtime: Optional[str] = None, max_retries: Optional[int] = None,
               skills: Iterable[str] = (), model: Optional[str] = None,
               provider: Optional[str] = None, completion_contract: Optional[str] = None,
               goal: bool = False, goal_max_turns: Optional[int] = None,
               initial_status: Optional[str] = None) -> Dict[str, Any]:
        args: List[str] = ["create", title, "--body", body,
                           "--idempotency-key", idempotency_key]
        for parent in parents:
            args += ["--parent", parent]
        if assignee:
            args += ["--assignee", assignee]
        if workspace:
            args += ["--workspace", workspace]
        if branch:
            args += ["--branch", branch]
        if max_runtime:
            args += ["--max-runtime", str(max_runtime)]
        if max_retries is not None:
            args += ["--max-retries", str(max_retries)]
        for skill in skills:
            args += ["--skill", skill]
        if model:
            args += ["--model", model]
        if provider:
            args += ["--provider", provider]
        if completion_contract:
            args += ["--completion-contract", completion_contract]
        if goal:
            args += ["--goal"]
            if goal_max_turns:
                args += ["--goal-max-turns", str(goal_max_turns)]
        if initial_status:
            args += ["--initial-status", initial_status]
        args += ["--json"]
        payload = self.call(*args)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise AdapterError("invalid_json", "create --json returned no task id")
        return payload

    def link(self, parent_id: str, child_id: str) -> None:
        self.call_text("link", parent_id, child_id)

    def complete(self, task_id: str, *, result: str, summary: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None) -> None:
        """Close a task with the evidence the native board stores on its run."""
        args: List[str] = ["complete", task_id, "--result", result]
        if summary:
            args += ["--summary", summary]
        if metadata:
            args += ["--metadata", json.dumps(metadata, ensure_ascii=False, sort_keys=True)]
        self.call_text(*args)


def default_prefix() -> List[str]:
    """Resolve the CLI prefix without guessing a path.

    ``LRO_KANBAN_CMD`` may override the prefix (tests use this to point at a
    fake CLI). It is split on whitespace only; it is not a shell.
    """
    override = os.environ.get("LRO_KANBAN_CMD")
    if override:
        return override.split()
    executable = shutil.which("hermes")
    if not executable:
        raise AdapterError("cli_error", "hermes executable not found on PATH")
    return [executable, "kanban"]
