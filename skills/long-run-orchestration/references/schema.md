# Objective schema (version 1)

`lro_spec.validate` rejects unknown keys. The normalized object is hashed after
defaults and derived dependencies are applied, so equivalent files share a hash.

## Top level

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | integer | yes | must be `1` |
| `objective_id` | string | yes | 3-64 chars, `[a-z0-9._-]`, starts alphanumeric |
| `title` | string | yes | non-empty |
| `goal` | string | yes | non-empty; rendered into every card body |
| `executor` | object | yes | `profile` required; `model`, `provider` optional; provider requires model |
| `workspace` | object | yes | `kind`: `worktree`, `dir`, `scratch`; `path` required for worktree/dir; `branch` only for worktree |
| `limits` | object | no | see defaults below |
| `review` | object | no | `enabled` (default false); when enabled, `model` is required and must differ from `executor.model` |
| `artifacts_dir` | string | no | default `artifacts` |
| `steps` | array | yes | non-empty; exactly one `spec`, one `consolidate`, one `approval`; one `review` iff review enabled |

### `limits` defaults

| Field | Default | Constraint |
|---|---|---|
| `max_runtime` | `7200` seconds | duration `300`, `"90s"`, `"30m"`, `"2h"`, `"1d"` |
| `max_retries` | `2` | integer >= 1 |
| `max_turns` | `12` | integer >= 1 (goal mode) |
| `stall_ticks` | `2` | integer >= 1 |
| `stall_alert_ticks` | `3` | integer >= 2 and greater than `stall_ticks` |
| `heartbeat_ttl_seconds` | `900` | integer >= 60 |

## Steps

Every step accepts:

| Field | Type | Notes |
|---|---|---|
| `id` | string | same format as `objective_id`; unique |
| `kind` | string | `spec`, `work`, `review`, `consolidate`, `approval` |
| `title` | string | non-empty |
| `body` | string | required for `work`, `review`, `consolidate` |
| `body_file` | string | alternative to `body`, relative to the spec file; file must exist |
| `depends_on` | array | step ids; derived edges are validated and completed |
| `acceptance` | array | `{id, criterion, evidence}`; required for work/review/consolidate; `evidence` is `proof`, `artifact` or `command` |
| `evidence_required` | array | field names from the native completion contract; defaults by kind |
| `max_runtime` | duration | overrides `limits.max_runtime` |
| `max_retries` | integer | overrides `limits.max_retries` |
| `goal` | boolean | work only; enables goal mode |
| `max_turns` | integer | work only, requires `goal: true` |
| `completion_contract` | string | `local-only` (default), `OWNER/REPO`, or a GitHub PR URL |
| `assignee` | string | ignored for spec/approval; approval is forced to the non-profile sentinel `lro-human-approval`; others default to executor or review profile |
| `skills` | array | review always gets `independent-code-review` |
| `model`, `provider` | string | review model must differ from executor model |

`spec` and `approval` cannot declare `evidence_required` and are always created
with `initial_status: blocked`. `spec` has no assignee; `approval` carries the
non-profile sentinel `lro-human-approval`, which the dispatcher skips and the
watchdog verifies, so neither card can be claimed by an agent.

### Default evidence by kind

| Kind | Fields |
|---|---|
| `work` | `changed_files`, `tests_run`, `artifacts` |
| `review` | `artifacts`, `notes` |
| `consolidate` | `artifacts`, `tests_run`, `pr_url` |

The native contract stores the PR as `published_pr`; the objective accepts both
`pr_url` and `published_pr`. A `tests_run` value is a non-empty list of objects,
each with a non-empty `cmd` (or `command`) string and `"exit": 0`; an empty list,
a free-form string, or a non-zero/missing exit code is a contract violation.
`artifacts` entries are checked for existence relative to the spec directory or
the objective's `artifacts_dir`; the check does not restrict paths to that
directory.

## Derived dependency edges

- the first `work` step depends on `spec`;
- later `work` steps form a chain in declaration order unless `depends_on` says
  otherwise;
- `review` depends on every `work` step;
- `consolidate` depends on `review` when present, otherwise on every work step;
- `approval` depends on `consolidate`.

A cycle, an unknown dependency, a self-dependency, or a dependency set that
differs from the derived set above is a contract error. Work steps may omit
`depends_on`; the derived chain is then filled in. The spec step cannot depend on
anything.

## Body marker

Every rendered body ends with:

```html
<!-- lro:v1 objective=<objective_id> step=<step_id> sha256=<spec_sha256> -->
```

`status` and `verify` use the marker to distinguish cards from this objective
from cards that merely share a title. A missing marker or a different hash is
reported as drift, never silently accepted.

## Exit codes

### `lro_objective.py` (`status`, `verify`)

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | success |
| 1 | `EXIT_CRITERIA` | acceptance or evidence failure |
| 2 | `EXIT_PENDING_APPROVAL` | objective complete except the human gate |
| 3 | `EXIT_CONTRACT` | invalid spec or contract violation |
| 4 | `EXIT_ADAPTER` | native CLI/adapter failure |

### `lro_watchdog.py`

| Code | Meaning |
|---|---|
| 0 | tick completed without drift or gateway outage |
| 3 | drift (missing marker, hash mismatch, broken parent link, missing card, unreadable ledger) |
| 4 | adapter failure before a tick could be produced |
| 5 | gateway unavailable |

The watchdog's verdict is in the JSON `state`; the code only separates the
operational categories. Code `2` belongs to the objective tool's approval gate
and is never emitted by the watchdog.
