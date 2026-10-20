"""Shared literals for the long-run orchestration candidate.

Every identifier that appears in documentation or in tests is defined here once,
so the documentation-consistency test can prove that what the code emits is
what the docs promise.
"""

SCHEMA_VERSION = 1

# Exit codes (documented in references/schema.md and README.md).
EXIT_OK = 0
EXIT_CRITERIA = 1
EXIT_PENDING_APPROVAL = 2
EXIT_CONTRACT = 3
EXIT_ADAPTER = 4

# Step kinds.
KIND_SPEC = "spec"
KIND_WORK = "work"
KIND_REVIEW = "review"
KIND_CONSOLIDATE = "consolidate"
KIND_APPROVAL = "approval"
KINDS = (KIND_SPEC, KIND_WORK, KIND_REVIEW, KIND_CONSOLIDATE, KIND_APPROVAL)

# The approval gate is owned by a sentinel that is deliberately not a Hermes
# profile. The kanban dispatcher buckets non-profile assignees as
# ``skipped_nonspawnable`` instead of spawning ``hermes -p <assignee>``, and a
# non-empty assignee also prevents ``kanban.default_assignee`` from routing the
# human gate to an agent. The watchdog treats any other assignee, or a running
# gate, as contract drift.
HUMAN_APPROVAL_ASSIGNEE = "lro-human-approval"

# Evidence fields a step may require from its closing run metadata.
EVIDENCE_FIELDS = (
    "changed_files",
    "tests_run",
    "artifacts",
    "commands",
    "notes",
    "pr_url",
)

# The native completion contract stores the published PR as ``published_pr``;
# the objective contract calls the same fact ``pr_url``. Both are accepted.
EVIDENCE_ALIASES = {
    "pr_url": ("pr_url", "published_pr"),
}

DEFAULT_EVIDENCE = {
    KIND_WORK: ("changed_files", "tests_run", "artifacts"),
    KIND_REVIEW: ("artifacts", "notes"),
    KIND_CONSOLIDATE: ("artifacts", "tests_run", "pr_url"),
}

# Objective-level states reported by ``status``.
STATE_NOT_APPLIED = "not_applied"
STATE_IN_PROGRESS = "in_progress"
STATE_BLOCKED = "blocked"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATE_CLOSED = "closed"
STATE_DRIFT = "drift"
STATES = (
    STATE_NOT_APPLIED,
    STATE_IN_PROGRESS,
    STATE_BLOCKED,
    STATE_AWAITING_APPROVAL,
    STATE_CLOSED,
    STATE_DRIFT,
)

# Watchdog alert kinds.
ALERT_STAGNATION = "stagnation"
ALERT_RUNTIME_EXHAUSTED = "runtime_exhausted"
ALERT_UNKNOWN_STATE = "unknown_state"
ALERT_GATEWAY_UNAVAILABLE = "gateway_unavailable"
ALERT_DRIFT = "drift"
ALERT_KINDS = (
    ALERT_STAGNATION,
    ALERT_RUNTIME_EXHAUSTED,
    ALERT_UNKNOWN_STATE,
    ALERT_GATEWAY_UNAVAILABLE,
    ALERT_DRIFT,
)

# Per-step watchdog states.
WATCH_IDLE = "idle"
WATCH_RUNNING = "running"
WATCH_RUNNING_NO_PROGRESS = "running_no_progress"
WATCH_RUNTIME_EXHAUSTED = "runtime_exhausted"
WATCH_BLOCKED = "blocked"
WATCH_REVIEW = "review"
WATCH_DONE = "done"
WATCH_UNKNOWN = "unknown"
WATCH_STATES = (
    WATCH_IDLE,
    WATCH_RUNNING,
    WATCH_RUNNING_NO_PROGRESS,
    WATCH_RUNTIME_EXHAUSTED,
    WATCH_BLOCKED,
    WATCH_REVIEW,
    WATCH_DONE,
    WATCH_UNKNOWN,
)

# Card statuses accepted by the native CLI (observed in the installed build).
CARD_STATUSES = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
    "archived",
)

APPROVE_VERDICTS = ("approve", "approved", "pass")
