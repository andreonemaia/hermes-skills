---
name: task-checkpoint-recovery
description: "Use when a long task may be interrupted or resumed. Saves task state and revalidates it against the repo, so decisions survive and side effects are not repeated."
version: 0.1.0
author: Hermes Skills contributors
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [checkpoint, resume, recovery, long-running, state, ledger, git, dedupe]
    category: software-development
---

# Task Checkpoint & Recovery

Structured, **revalidated** checkpoints for long-running agent tasks.

## Core principle

A checkpoint is a **claim about the past**, not a fact. Every claim is revalidated
against the real state of the project before it is used to decide the next step.
When revalidation is not possible, the claim is reported as unknown, never as true.

Two consequences drive the whole design:

- `record-op` only records a *claim* that a side effect happened. `verify` re-checks
  that claim against git and the remote; a claim alone never marks an operation done.
- `check-op` decides whether an effect-carrying operation may run *now*, so an agent
  resuming a task does not push, merge or open a pull request twice.

## What this skill is not

It does not reimplement, and must not be confused with:

| Native mechanism | What it preserves |
|---|---|
| Session persistence, `--resume`, `/resume`, `/branch` | the conversation |
| File checkpoints, `/rollback`, `hermes checkpoints` | file content (git-shadow repo) |
| Context compaction | a model-written summary of the conversation |
| `/goal`, `/subgoal` | objective and loop policy (turn budget, gates) |
| Kanban | coordination across tasks, profiles and workers |

None of them records *what was already done, what was decided and why, what is left,
and which side effects already happened in the outside world*. That gap is what this
skill fills, as a complementary layer on top of them.

There is no pre-compaction hook and no iteration-budget-exhaustion event in Hermes,
so this skill never promises to *anticipate* an interruption. It uses **preventive
checkpoints**: save often enough that any interruption is cheap.

## When to use

- A task will span many steps, files, commits or tool calls.
- A task may be interrupted: context compaction, a new session, a hand-off, a stop.
- The task has **side effects that must not repeat**: commits, pushes, PRs, merges,
  downloads, deploys.
- Several writers (parallel agents, a human and an agent) may touch the same task.

## When not to use

- Short single-shot tasks: the overhead is not worth it.
- Tasks where the only state is file content: use native file checkpoints.
- Multi-task coordination: use Kanban.

## Prerequisites

- Python 3.10+ (stdlib only, no third-party packages).
- Optional: `git` on `PATH` for repo revalidation. Without it, repo facts are
  reported as unknown (`git_unavailable`) instead of failing the run.

## Quick reference

All commands run from the skill directory; `--project` defaults to the current
directory. Add `--now <ISO-8601>` for deterministic runs.

```bash
# start / update the checkpoint
python scripts/checkpoint.py --project <root> save --task <slug> --title "<title>" \
  --objective "<objective>" --step s1="<title>" --step-status s1=done \
  --step-evidence s1="<command and output>" --file path/inside/repo

# revalidate against reality (exit 1 when there are findings)
python scripts/checkpoint.py --project <root> verify --task <slug> --json

# human report for resuming
python scripts/checkpoint.py --project <root> resume-report --task <slug>

# record a side effect (a claim) and ask whether it may run again
python scripts/checkpoint.py --project <root> record-op --task <slug> \
  --kind push --target origin/main --state verified --verification "git ls-remote"
python scripts/checkpoint.py --project <root> check-op --task <slug> \
  --kind push --target origin/main

# inspect and maintain
python scripts/checkpoint.py --project <root> show --task <slug> --json
python scripts/checkpoint.py --project <root> list --json
python scripts/checkpoint.py --project <root> prune --task <slug> --keep 20 --dry-run
```

## Procedure

### 1. Open the checkpoint before doing work

```bash
python scripts/checkpoint.py --project . save --task my-task --title "Ship feature X" \
  --objective "feature X merged to main with tests green" \
  --step s1="write implementation" --step s2="run suite" --step s3="open PR"
```

`save` is idempotent and additive: repeating it with `--step-status` / `--decision` /
`--note` updates only what you pass, increments `revision`, and appends a snapshot to
`history/`. It refuses to write secrets (exit 4).

### 2. Update as you go (preventive checkpoints)

After each meaningful step, and **before** any effect-carrying operation:

```bash
python scripts/checkpoint.py --project . save --task my-task \
  --step-status s1=done --step-evidence s1="pytest -q -> 41 passed" \
  --decision d1="use stdlib only|no third-party dependency allowed" \
  --next n1="open the PR" --file scripts/checkpoint.py
```

Fields: `--status`, `--step`, `--step-status`, `--step-evidence`, `--decision`,
`--blocker ID=WHAT[|NEEDS]`, `--next ID=ACTION`, `--next-open ID` (the action does not
need approval), `--check "COMMAND|EXIT"`, `--note`, `--file RELATIVE_PATH`.

Use `--expect-revision N` when another writer may be active: the save fails with exit 1
(`EXIT_DRIFT`, the same code a drifted checkpoint produces) instead of silently clobbering
a newer revision.

### 3. Before repeating an effect-carrying operation, ask

```bash
python scripts/checkpoint.py --project . check-op --task my-task --kind push --target origin/main
```

| Decision | Exit | Meaning |
|---|---|---|
| `proceed` | 0 | nothing recorded, or a recorded claim is not backed by reality: the operation may run |
| already done, do not repeat | 1 | the effect is verified as present in reality: skip it |
| stop and ask the user | 3 | the ledger records a failure for this operation: a human decides |
| cannot confirm, ask the user | 2 | state unknown (offline, or a claim that cannot be checked): fail closed |

`check-op --offline` skips remote verification, which turns remote claims into
`unknown` instead of guessing.

### 4. Revalidate, then resume

```bash
python scripts/checkpoint.py --project . verify --task my-task --json
python scripts/checkpoint.py --project . resume-report --task my-task
```

`verify` compares the checkpoint against the project and prints findings. Findings are
`critical` / `warning` / `info`. Every finding except the benign ones (`git_unavailable`,
`op_planned`, `op_completed_unrecorded`) fails the run with exit 1; `--strict` also fails on
those three. `--stale-after`
(default 24h) controls when a checkpoint counts as `stale_checkpoint`.

`resume-report` is the entry point for a fresh session: it reports the state, the
drift, what is already done, what is blocked, and what to do next, so the work
continues instead of restarting.

### 5. Close the task

```bash
python scripts/checkpoint.py --project . save --task my-task --status done --note "merged in abc1234"
python scripts/checkpoint.py --project . prune --task my-task --keep 20
```

## Resuming a task in a new session

Paste this into the new session (or reference the skill and the task slug):

> Resume task `<slug>` in `<project root>`. From the skill directory (`cd <skill dir>`), run
> `python scripts/checkpoint.py --project <root> verify --task <slug> --json` and
> `python scripts/checkpoint.py --project <root> resume-report --task <slug>`, then continue
> from the reported next actions. Before any push/PR/merge/download, run `check-op` for that
> operation and respect its decision. Update the checkpoint with `save` after each step.

## Storage layout

```
<project>/.hermes/checkpoints/<task-slug>/
  checkpoint.json        # current state (atomic write: .tmp + os.replace + fsync)
  checkpoint.md          # readable rendering of the same state
  operations.jsonl       # append-only ledger of side-effect claims
  history/               # immutable snapshots, newest kept by prune
  .lock                  # cross-process lock guarding writers
```

`.hermes/` is already git-ignored in this repo; nothing is written outside the project,
no absolute paths, no secrets. A corrupt `checkpoint.json` is never silently
overwritten: it is quarantined only once a valid history snapshot exists to fall back on.

## Verification checklist

- [ ] `save` exits 0 and `show --task <slug>` shows the expected revision and steps.
- [ ] `verify --task <slug> --json` exits 0 on a clean project; exit 1 with findings when
      the tree is dirty or the checkpoint is stale.
- [ ] A second `save` with `--expect-revision <old>` exits 1 (`EXIT_DRIFT`), not 0: no silent clobber.
- [ ] `check-op` returns `proceed` (0) with no ledger entry, and "do not repeat" (1)
      after a verified `record-op`.
- [ ] `resume-report` on a fresh clone of the state names the next action, not the whole task.
- [ ] `cd <skill dir> && python -m unittest discover -s tests -t tests` passes. Run it from
      the skill directory: from the repository root there is no `tests/` and discovery
      aborts with `ImportError: Start directory is not importable`.
- [ ] `python scripts/validate.py --project <root>` exits 0 for every checkpoint.

## Pitfalls

- **Treating a ledger claim as truth.** `record-op` records what the agent *believes*;
  only `verify`/`check-op` compare it with reality.
- **Writing secrets into evidence.** Commands, outputs and notes are scanned; a hit is
  refused with exit 4 rather than redacted silently.
- **Absolute or escaping paths.** `--file` and `--check` paths must stay inside the
  project; symlinks that leave it are refused.
- **`--offline` as a default.** It converts remote truth into `unknown`, so `check-op`
  fails closed and blocks a resumable push. Use it only when there is no network.
- **Expecting interruption detection.** There is no pre-compaction hook; save early and
  often instead of waiting for a signal.

## Tests

```bash
cd <skill dir> && python -m unittest discover -s tests -t tests -v
```

## Limitations

- Single-task scope: it does not coordinate multiple tasks (use Kanban for that).
- Repo revalidation is limited to git facts (branch, HEAD, ancestry, dirty tree, remote ref).
- It cannot verify effects that leave no local trace (a deploy on a third-party system,
  a message sent by hand); those stay claims until something observable confirms them.
- The markdown rendering is for humans; `checkpoint.json` is the source of truth.
