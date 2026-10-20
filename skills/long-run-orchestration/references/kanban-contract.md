# Native Kanban contract observed

This skill relies on behavior of the Hermes CLI at `hermes kanban`, not on a
private database. The points below were read directly from the installed
implementation (`hermes_cli/kanban_db.py`, `kanban_db_graph.py`,
`kanban_parser.py`) and are the basis for the safety tests.

## 1. `create` accepts parents and links atomically

`hermes kanban create` exposes a repeatable `--parent` argument
(`kanban_parser.py`, `_arg("--parent", action="append")`). `create_task` resolves
the initial state, inserts the task, and inserts every parent link **inside one
`write_txn`** (`kanban_db.py`, `create_task`):

```python
with write_txn(conn, allow_nested=True):
    task_status, tenant = initial_task_state(...)
    conn.execute("INSERT INTO tasks ...")
    for pid in parents:
        _link(conn, pid, task_id)
```

The comment on `allow_nested` states the intent explicitly: graph builders
compose `create_task` under one outer commit "so the dispatcher never sees a
half-built graph". A crash cannot leave a created child without its links: both
the row and the links commit together.

**Consequence:** the supported CLI cannot produce a child with parents that were
silently dropped. `honor_create_parents=False` is not a supported CLI mode; it
simulates an external write (legacy or out-of-band) that bypasses `create_task`.
It is still a possible *persisted state*, so the orchestrator must treat a
created child with missing links as unsafe and keep it inert.

## 2. Initial status

`initial_task_state` returns:

| Condition | Status |
|---|---|
| `initial_status="blocked"` | `blocked` |
| `triage=True` | `triage` |
| any parent not `done` | `todo` |
| otherwise | `ready` |

The orchestrator creates `spec` and `approval` with `initial_status="blocked"`.
`spec` has no assignee. `approval` is assigned to the sentinel
`lro-human-approval`, which is not a Hermes profile: the dispatcher buckets it
as `skipped_nonspawnable` instead of spawning an agent, and the non-empty value
also stops `kanban.default_assignee` from routing the gate to a worker. Work,
review, and consolidate cards are created with the spec card as a parent, and
the spec card is `blocked`, so they are born `todo`.

The sentinel is a routing convention, not an authorization boundary. The
installed `kanban complete` command records no caller identity, so any process
that can run the CLI can complete the approval card. Keep board access limited
to trusted operators and treat the approval card as an audit marker, not as an
access control.

## 3. Linking a ready child demotes it

`link_tasks` refuses to link a child that is already `running` unless the caller
supplies the trusted `expected_child_run_id`. For a `ready` child whose new
parent is not terminal, it sets the child to `todo` and records a
`dependency_wait` event. So even if a child was somehow created `ready`, adding
its link before releasing the graph demotes it.

## 4. Eligibility is enforced at claim time, not only at promotion

`recompute_ready` promotes `todo`/`blocked` cards only when all parents are
`done` or `archived` (and skips sticky blocks). More importantly, `claim_task`
re-checks parents in the same transaction as `ready -> running`:

```python
if not _parents_satisfied(conn, task_id):
    conn.execute("UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'ready'", ...)
    _append_event(conn, task_id, "claim_rejected", {"reason": "parents_not_done"})
    return None
```

An operator `promote` also refuses while a parent is unfinished, because a
forced `ready` would only be reverted by the next claim.

**Consequence:** a card with a persisted, unfinished parent cannot execute even
if its status was manually set to `ready`. The failure mode that matters is a
child with **no** parent link at all; that child is genuinely eligible.

## 5. What the orchestrator must guarantee

1. Create each child with `--parent` in the same native call; never create then
   link as separate steps. This gives atomicity for **one card and its links**,
   not for the whole objective.
2. Keep `spec` blocked until every child exists and every expected link is
   present. Preparing an objective spans several independent CLI calls; the
   guarantee is that no child is dispatchable until the spec is released.
3. On resume, verify each expected link with `show`; re-create a missing link
   only while the spec is still blocked and the persisted graph matches the spec.
4. If a child exists with a missing or extra link, a divergent marker, or is
   already `ready`/`running` while a parent is unfinished, stop with a
   diagnostic. Do not complete the spec and do not attempt automatic repair.
5. Release the spec only after preparation succeeds. Releasing is idempotent:
   completing an already-done spec is a no-op for the orchestrator.

The adapter cannot prevent an execution that an external actor released after
the graph was prepared. Native `claim_task` re-checks parents, so a manually
promoted `ready` card is demoted at claim time, but a card already `running` is
outside the adapter's control. The watchdog reports that condition as drift and
never kills the process or mutates the card.

`honor_create_parents=False` in the test fake is not a CLI mode. It simulates a
legacy or out-of-band write that bypassed `create_task`; the orchestrator must
detect the resulting missing link and keep the graph inert.

## Evidence map

| Claim | Source |
|---|---|
| `--parent` is repeatable | `hermes_cli/kanban_parser.py`, create subcommand |
| create + links share one transaction | `hermes_cli/kanban_db.py`, `create_task` |
| initial status rules | `hermes_cli/kanban_db_graph.py`, `initial_task_state` |
| link demotes ready child | `hermes_cli/kanban_db.py`, `link_tasks` |
| claim re-checks parents | `hermes_cli/kanban_db.py`, `claim_task` |
| promote refuses unfinished parents | `hermes_cli/kanban_db.py`, `promote_task` |
| sticky blocks need explicit unblock | `hermes_cli/kanban_db.py`, `recompute_ready` |
