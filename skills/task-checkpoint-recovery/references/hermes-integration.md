# Integrating with a Hermes workflow

This skill is a **layer on top of** native Hermes mechanisms. It does not replace
sessions, file checkpoints, `/goal`, Kanban or compaction, and it deliberately does not
try to detect an interruption: Hermes exposes no pre-compaction hook and no
iteration-budget-exhaustion event, so the strategy is **preventive checkpoints** during
long tasks (save after each meaningful step, and before every effect-carrying operation).

## Where it fits

| Native mechanism | What it keeps | What it still misses |
|---|---|---|
| `--resume` / `/resume` / `/branch` | the conversation | structured "done / decided / left / already done in the world" |
| `/rollback`, `hermes checkpoints` | file content | intent and side effects |
| compaction | a model-written summary | revalidation against the project |
| `/goal` | objective, loop policy, gates | per-step evidence and the operations ledger |
| Kanban | multi-task coordination | single-task recovery with revalidation |

Use `/goal` to keep the objective across compaction, Kanban when several tasks or
profiles are involved, and this skill for the operational state of one long task.

## Recommended loop

1. `save` to open the task (title, objective, steps).
2. After each meaningful step: `save --step-status ... --step-evidence ...`, plus
   `--decision` when a choice is made and `--blocker` when something is stuck.
3. Before a push / PR / merge / download / deploy: `check-op` for that `--kind` and
   `--target`, and obey the decision (0 proceed, 1 do not repeat, 2/3 stop and ask).
4. After the operation: `record-op` with `--state verified` and `--verification`
   (the command that proves it), so the next run can skip it.
5. At the start of any new session: `verify` then `resume-report` and continue from the
   reported next actions.
6. At the end: `save --status done`, then `prune`.

## Resume prompt

> Resume task `<slug>` in `<project root>`. Run `verify --json` and `resume-report`
> first, then continue from the reported next actions. Before any push/PR/merge, run
> `check-op` and respect its decision. Update the checkpoint with `save` after each step.

## Optional automation with shell hooks

Hermes shell hooks are declared in `config.yaml` and cover events such as
`pre_tool_call` (exit code 2 blocks the tool call), `post_tool_call`, `pre_llm_call`,
`pre_verify`, `on_session_start`, `on_session_end`, `agent_loop_stopped` and
`subagent_start`/`subagent_stop`. See the Hermes docs
(`website/docs/user-guide/features/hooks.md`) for the exact schema and the
first-use consent prompt per (event, command).

Two safe patterns, both optional:

- `post_tool_call`: after a write/edit, run `save --task <slug>` so progress is
  checkpointed without the model asking.
- `on_session_start`: run `resume-report` and inject it as context, so a resumed session
  starts from the report instead of re-deriving state.

Because hook payloads may contain file contents and command output, keep the hook
commands free of secrets and prefer `--file`/`--note` over dumping raw output.

## Deliberate non-goals

- No promise of anticipating an interruption (no hook exists for it).
- No secrets, ever: values that look like credentials, tokens or env dumps are refused
  with exit 4 rather than stored or silently redacted.
- No writes outside `<project>/.hermes/checkpoints/`.
- No automatic merge or push: `check-op` gates those, it never performs them.

## Fail-closed policy

When a claim cannot be checked (no git, `--offline`, unknown state), `check-op` returns
"cannot confirm" (exit 2) rather than "proceed". An agent that obeys the exit code
therefore errs on the side of asking the user instead of repeating an effect.
