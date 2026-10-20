---
name: long-run-orchestration
description: "Use when a task is too long for one agent turn. Compiles a versioned objective into a gated Kanban graph, applies it idempotently, watches progress, and verifies evidence and human approval."
version: 0.1.0
author: Hermes Skills contributors
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [long-running, orchestration, kanban, dag, watchdog, evidence, approval, resume, idempotent]
    category: software-development
---

# Long-Run Orchestration

Turn one long objective into a **versioned contract** plus a **gated Kanban graph**:
spec gate, work steps, independent review, consolidation, and a human approval gate.
Every stage is resumable, every completion is checked against declared evidence, and
nothing is dispatched before the graph is fully prepared.

## Core principle

The objective file is the source of truth. Cards are derived state. A card is
never trusted because it exists; it is trusted because its body carries the
objective marker and its `spec_sha256` matches the current normalized spec.

Three invariants drive the design:

- **No executable child before the graph is complete.** Children are created with
  their parent links in the same native `create` call, inside one write
  transaction. The spec card is completed only after every child exists and every
  link is present.
- **The spec and the human approval gate are born blocked.** They are not
  dispatchable. The spec gate is released by the orchestrator after successful
  preparation. The approval card is promoted by the board's native
  ready-recompute once consolidation completes; its sentinel assignee
  (`lro-human-approval`) is not a Hermes profile, so the dispatcher skips it.
  Only a human completes the card.
- **Apply is idempotent and resumable, but not a blind repair tool.** Cards are
  found by idempotency key (`lro:v1:<objective>:<step>`), reused, and missing
  links are re-created only while the graph is still consistent with the spec.
  A divergent marker, an unexpected parent, or a card already `ready`/`running`
  before its parents finish stops apply with a diagnostic; nothing is
  auto-repaired or killed.

Atomicity is scoped: `create` persists one card and its parent links in one
native transaction. Preparing the whole objective spans several independent
CLI calls, so the guarantee comes from keeping the spec gate blocked until all
calls succeed, not from one transaction around the whole graph.

## Files

| Path | Purpose |
|---|---|
| `scripts/lro_contract.py` | Exit codes, kinds, evidence fields, watchdog states |
| `scripts/lro_spec.py` | Schema validation, DAG derivation, hash, body rendering |
| `scripts/lro_adapter.py` | Native `hermes kanban` adapter (create/link/show/complete) |
| `scripts/lro_objective.py` | `apply`, `status`, `verify` |
| `scripts/lro_watchdog.py` | One read-only watchdog tick |
| `references/schema.md` | Full objective schema and defaults |
| `references/kanban-contract.md` | Observed native behavior the design relies on |
| `references/operations.md` | State files, watchdog signals, drift handling, limits |
| `examples/objective.example.json` | Illustrative six-step objective with placeholders |

## Workflow

1. **Write the objective** (`objective.json`) from `examples/objective.example.json`.
   The executor model and the review model must differ when review is enabled.
2. **Validate and preview**:

   ```bash
   python scripts/lro_objective.py apply --spec objective.json --dry-run
   ```

3. **Apply** (safe to rerun):

   ```bash
   python scripts/lro_objective.py apply --spec objective.json --json
   ```

   This creates or reuses the cards, verifies every link, closes the spec gate,
   and saves `.lro/objective.json` next to the spec. On a partial failure the state
   file records the cards already created, so the next run resumes instead of
   duplicating. On a divergent persisted graph it exits with a diagnostic and
   leaves the graph untouched.

4. **Watch** (one tick per call; schedule it externally if desired):

   ```bash
   python scripts/lro_watchdog.py --spec objective.json --json
   ```

   Exit codes: `0` when the tick completes without drift or gateway outage;
   `3` for drift; `5` for gateway unavailable; `4` for an adapter failure before
   a tick could be produced. The JSON `state` carries the verdict (`running`,
   `stalled`, `runtime_exhausted`, `blocked`, `drift`, `gateway_unavailable`,
   `done`, `unknown`). The watchdog is read-only: it does not create, complete,
   or mutate cards.

5. **Verify evidence**:

   ```bash
   python scripts/lro_objective.py verify --spec objective.json --json
   ```

   Exit codes: `0` all criteria met, `1` criteria/evidence failure, `2` waiting on
   human approval, `3` contract error, `4` adapter/native error.

6. **Human approval.** A person reviews the PR and completes the `approval` card
   manually. No agent should complete it.

## Completion metadata

Workers complete a card with `metadata` containing:

- every field listed in `evidence_required` (non-empty; paths must exist);
- `acceptance`: a list of `{id, met, proof}` covering every criterion;
- for review cards: `verdict`, `reviewer_model`, `reviewer_provider`, `findings`.

`verify` fails closed: a missing card, missing marker, hash mismatch, empty
evidence, unmet criterion, or reviewer model equal to the executor model is a
failure, never a warning.

## Safety rules

- Do not hand-edit cards to skip a gate. Fix the spec and re-apply.
- Do not complete the approval card programmatically.
- Do not merge, release, or install as part of this workflow.
- Keep `.lro/objective.json` with the objective; it is the resume ledger.
- The active Hermes profile is not modified by this skill: it only invokes the
  native `hermes kanban` CLI against the selected board.

## References

- `references/schema.md` for every field, default, and validation rule.
- `references/kanban-contract.md` for the native behaviors (atomic create,
  initial status, sticky blocks, eligibility) and the evidence behind them.
