# task-checkpoint-recovery

Structured, revalidated checkpoints for long-running agent tasks.

A long agent task gets interrupted: context compaction, a new session, a hand-off, a
stopped loop. What usually gets lost is not the files (git has them) or the
conversation (the session has it), but the *operational state*: which steps are done,
which decisions were made and why, what is blocked, and which side effects already
happened in the outside world. The cost of that loss is redoing work and, worse,
repeating an effect such as a push, a merge or a download.

This skill stores that state as a small JSON artifact inside the project and, crucially,
**revalidates every claim against the project before acting on it**.

## Design in one paragraph

A checkpoint is a claim about the past. `verify` revalidates it against git and the
filesystem and reports drift instead of trusting the file. The operations ledger is
append-only and records *claims* about side effects; `check-op` is the gate that decides
whether an effect-carrying operation may run, so a resumed agent does not push twice.
Nothing is written outside `<project>/.hermes/checkpoints/<slug>/`, no secrets are
persisted, and a corrupt checkpoint is quarantined only after a valid history snapshot
is available.

## Installation

Copy or symlink `skills/task-checkpoint-recovery/` into your Hermes profile `skills/`
directory, or keep it in this repository and run it by path. Requirements: Python 3.10+
(stdlib only) and, optionally, `git` on `PATH`.

## Quick start

```bash
cd skills/task-checkpoint-recovery

# 1. open the checkpoint
python scripts/checkpoint.py --project /path/to/repo save --task my-task \
  --title "Ship feature X" --objective "feature X merged with tests green" \
  --step s1="implement" --step s2="test" --step s3="open PR"

# 2. work, then record progress
python scripts/checkpoint.py --project /path/to/repo save --task my-task \
  --step-status s1=done --step-evidence s1="pytest -q -> 41 passed"

# 3. revalidate and report before resuming
python scripts/checkpoint.py --project /path/to/repo verify --task my-task --json
python scripts/checkpoint.py --project /path/to/repo resume-report --task my-task

# 4. guard a side effect
python scripts/checkpoint.py --project /path/to/repo check-op --task my-task --kind push --target origin/main
```

## Commands

| Command | Purpose |
|---|---|
| `save` | write or update the checkpoint (additive, revision-guarded) |
| `verify` | revalidate against the project; reports findings |
| `resume-report` | human-readable report for resuming the task |
| `record-op` | append a side-effect claim to the ledger |
| `check-op` | decide whether an effect-carrying operation may run |
| `show` / `list` | print one checkpoint / list all in the project |
| `prune` | trim `history/` (`--dry-run` available) |

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | success; for `verify`, no actionable finding (benign ones may still be reported) |
| 1 | `EXIT_DRIFT` | findings (drift, staleness), any finding under `--strict`, a stale `--expect-revision`, or "do not repeat" from `check-op` |
| 2 | `EXIT_CORRUPT` | corrupt/unreadable checkpoint, or a state that cannot be confirmed |
| 3 | `EXIT_MISSING` | missing target, or an unresolved failure needing a decision |
| 4 | `EXIT_USAGE` | invalid usage: unknown kind/state, refused secret, unsafe path |

## Findings

`verify` reports findings by id and severity (`critical`, `warning`, `info`):
`history_rewritten`, `op_stale_claim`, `unsafe_path` (critical); `head_diverged`,
`op_unresolved`, `op_planned_landed`, `op_unknown`, `branch_mismatch`, `dirty_tree`,
`file_missing`, `file_drift` (warning); `op_planned`, `op_completed_unrecorded`, `git_unavailable`,
`head_advanced`, `stale_checkpoint` (info).

## Files

```
SKILL.md                       agent-facing instructions
README.md                      this file
references/schema.md           checkpoint schema, fields, exit codes, findings
references/hermes-integration.md  how to wire it into a Hermes workflow
scripts/checkpoint.py          CLI
scripts/_common.py             storage, validation, locking, secret scanning
scripts/validate.py            read-only validator for CI and pre-commit
tests/test_task_checkpoint_recovery.py
tests/test_hardening.py
tests/test_now_flag.py
tests/test_review_regressions.py
```

## Tests

```bash
cd skills/task-checkpoint-recovery
python -m unittest discover -s tests -t tests -v
python scripts/validate.py --project /path/to/repo --digests
```

## Limitations

Single-task scope; git-only repo revalidation; effects with no local trace stay claims;
no pre-compaction or iteration-budget event exists in Hermes, so interruption is handled
by preventive checkpoints rather than detection.

## License

MIT.
