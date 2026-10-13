# Checkpoint schema (`task-checkpoint/v1`)

Source of truth: `<project>/.hermes/checkpoints/<slug>/checkpoint.json`.
`checkpoint.md` is a human rendering of the same data. Both are written atomically
(`.tmp` + `os.replace` + `fsync`).

## Example

```json
{
  "schema": "task-checkpoint/v1",
  "revision": 3,
  "task": {
    "id": "my-task",
    "title": "Ship feature X",
    "objective": "feature X merged with tests green",
    "status": "in_progress",
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T02:00:00+00:00"
  },
  "repo": {"available": true, "root": ".", "branch": "feat/x", "head": "abc1234",
           "remote_head": "abc1234", "dirty": false, "ahead": 0, "behind": 0},
  "steps": [{"id": "s1", "title": "implement", "status": "done",
             "evidence": "pytest -q -> 41 passed", "completed_at": "..."}],
  "decisions": [{"id": "d1", "decision": "stdlib only", "rationale": "...", "at": "..."}],
  "blockers": [{"id": "b1", "what": "no credentials for the registry", "needs": "user"}],
  "next_actions": [{"id": "n1", "action": "open the PR", "requires_approval": true}],
  "files": [{"path": "scripts/checkpoint.py", "sha256": "...", "state": "modified"}],
  "artifacts": [{"kind": "push", "id": "origin/feat/x", "at": "..."}],
  "operations_summary": {"planned": 0, "started": 0, "verified": 1, "failed": 0, "unknown": 0},
  "checks": [{"command": "pytest -q", "exit": 0, "at": "..."}],
  "notes": ["..."],
  "limitations": ["..."]
}
```

## Top-level fields

| Field | Type | Notes |
|---|---|---|
| `schema` | string | must be exactly `task-checkpoint/v1` |
| `revision` | int | positive; incremented by every successful `save` |
| `task` | object | `id`, `title`, `objective`, `created_at`, `updated_at` are required non-empty strings |
| `repo` | object | `available` (bool) required; other keys filled when git is usable |
| `steps` | array | `id`, `title` non-empty; `status` in `done`/`pending`/`blocked` |
| `decisions` | array | `decision` non-empty; `rationale` optional but recommended |
| `blockers` | array | `what` and `needs` required (what it waits on) |
| `next_actions` | array | `action` non-empty; `requires_approval` bool required |
| `files` | array | `path` project-relative (absolute paths rejected); `state` in `created`/`modified`/`deleted`; optional `sha256` |
| `artifacts` | array | `kind` in `commit`/`push`/`pr`/`merge`/`download`/`other` |
| `operations_summary` | object | counts per ledger state |
| `checks` | array | `command` non-empty, `exit` int |
| `notes`, `limitations` | array of strings | free text |

`task.status` is one of `in_progress`, `blocked`, `awaiting_approval`, `done`.
Unknown keys at any level are rejected by the validator.

## Operations ledger (`operations.jsonl`)

Append-only, one JSON object per line. A torn line is skipped and never invalidates
earlier evidence.

```json
{"op_id": "push:origin/main", "kind": "push", "state": "verified", "target": "origin/main",
 "detail": "pushed 3 commits", "verification": "git ls-remote origin main", "at": "..."}
```

- `kind` in `commit`/`push`/`pr`/`merge`/`download`/`other`.
- `state` in `planned`/`started`/`verified`/`failed`/`unknown`.
- `op_id` defaults to `<kind>:<target>`; `current_ops()` collapses the log to the
  latest state per `op_id`, and a `verified` entry is cleared by a later
  `started`/`failed`/`planned` one.

A ledger entry is a **claim**. Only `verify` (against git) and `check-op` (as a gate)
turn it into a decision.

## History and recovery

`history/` holds immutable snapshots (`<NNNN>-<ts>.json` plus the markdown rendering).
`load_checkpoint` behaviour:

1. read `checkpoint.json`; if it parses and validates, use it;
2. otherwise fall back to the newest valid snapshot and, only then, quarantine the
   damaged file;
3. if nothing valid exists, raise `CorruptCheckpoint` (exit 2) without overwriting the
   corrupt file.

`prune --keep N` trims `history/` to the newest N snapshots (the current state is never
pruned); `--dry-run` reports what would be removed.

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | success |
| 1 | `EXIT_DRIFT` | findings, or "do not repeat" from `check-op` |
| 2 | `EXIT_CORRUPT` | corrupt/unreadable checkpoint, unknown state |
| 3 | `EXIT_MISSING` | missing target, revision conflict, unresolved failure |
| 4 | `EXIT_USAGE` | invalid usage, refused secret, unsafe path |

## Finding ids

| id | Severity | Trigger |
|---|---|---|
| `history_rewritten` | critical | recorded HEAD is no longer an ancestor of the current HEAD |
| `op_stale_claim` | critical | a `verified` ledger claim contradicts reality |
| `unsafe_path` | critical | a recorded path escapes the project |
| `head_diverged` | warning | local and remote HEAD diverged |
| `op_unresolved` | warning | ledger records a failure that needs a decision |
| `op_planned_landed` | warning | recorded as `planned`, but reality confirms the effect is already present |
| `op_unknown` | warning | ledger state cannot be checked |
| `branch_mismatch` | warning | the checkpoint branch differs from the current branch |
| `dirty_tree` | warning | uncommitted changes present |
| `file_missing` / `file_drift` | warning | a recorded file is gone / its digest changed |
| `op_planned` / `op_completed_unrecorded` | info | pending claim / effect present but never recorded |
| `git_unavailable` | info | git missing, so repo facts are unknown |
| `head_advanced` | info | HEAD moved forward (expected after a commit) |
| `stale_checkpoint` | info | older than `--stale-after` (default 86400s) |

## Determinism

`--now <ISO-8601>` overrides the clock for `save`, `verify`, `resume-report`,
`record-op`, `check-op` and `prune`. The environment variables `TCR_NOW` / `TCR_EPOCH`
provide the same override for tests.
