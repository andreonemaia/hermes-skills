---
name: independent-code-review
description: "Independent read-only code review by a separate reviewer process."
version: 1.0.0
author: Hermes Skills contributors
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [code-review, git, github, multi-model, reviewer, quality]
    category: software-development
---

# Independent Code Review

Run a code review with a **separate reviewer process** in an isolated `hermes
chat` run, strictly read-only. The reviewer is normally the model configured at
`auxiliary.review` in the Hermes config; the run's `system/init` event is checked
so a silent fallback to another model is reported instead of assumed away. This
skill covers GitHub PRs, local branch diffs, staged changes, and uncommitted
changes. It never publishes to GitHub and never edits code on its own.

**Core principle:** an agent must not review its own work in its own context. A
fresh process, a separate model, and zero tools find what the implementer's
context hides.

## When to Use

- The user asks for an independent review, a second-model opinion, or a review
  by a different model on a diff or a pull request.
- Before approving or merging a PR, or before shipping a change.
- After implementation, as a step distinct from the author's own checks.

Don't use for:

- Self-review by the implementer. That is the author's own check, not an
  independent review; keep the two steps separate.
- Posting review comments to GitHub, approving or merging. This skill never
  writes to GitHub; do that only with explicit user authorization and a
  dedicated tool.
- Fixing the findings. This skill reports; the caller decides.

## Prerequisites

- Hermes CLI on PATH (`hermes`).
- `git`; `gh` (authenticated) only when the diff is a GitHub PR.
- A reviewer configured under `auxiliary.review` (see Step 1).

## Quick Reference

```bash
# 1. Read the configured reviewer
hermes config path
hermes config get auxiliary.review

# 2. Acquire the diff (read-only)
gh pr diff <N>                 # GitHub PR
git diff <base>...HEAD         # branch vs base
git diff --cached              # staged
git diff                       # unstaged
git diff <A>..<B>              # explicit range

# 3. Run the isolated reviewer (one-shot, no toolsets: -t none, never -t "")
#    pass --reasoning only if auxiliary.review.reasoning_effort is configured
mkdir -p "<scratch-dir>"
hermes chat --query-file <brief.md> \
  -m <model> --provider <provider> --reasoning <level> \
  -t none --ignore-rules -Q --max-turns 2 \
  --in <scratch-dir> --format stream-json --source oneshot \
  > "<scratch-dir>/review.jsonl"
```

Shells: the blocks in this skill are written for a POSIX shell (bash, git-bash
on Windows). PowerShell is **not** drop-in compatible with them: it does not
continue lines with `\` (it uses a backtick), it has no `grep`/`wc` (use
`Select-String` and `Measure-Object`), and its redirection re-encodes output. Run
these commands through bash/git-bash, or translate each block before using it in
PowerShell. Windows is this skill's only tested platform, and git-bash is the
tested shell.

## Auxiliary runs are internal implementation

Every auxiliary `hermes chat` run (reviewer, probes, smoke tests, model or
toolset checks) is **finite, non-interactive and internal**. It must never show
up in the user's normal conversation list.

1. **Always one-shot:** `-Q` (or `--oneshot`). Never launch an interactive
   auxiliary `hermes chat`.
2. **Always an explicit hidden source:** `--source oneshot`. An explicit
   `--source` always wins over the transport default, so a run started from
   inside a Desktop/TUI session is still tagged as a one-shot run, not as that
   conversation. Do **not** substitute `--source tool`: it is documented as
   hidden, but on the validated version it still leaked into the Desktop Project
   tree, while `oneshot` did not (see **Visibility evidence**). `oneshot` is both
   the accurate tag for first-party auxiliary runs and the one observed to be
   filtered.
3. **No interactive auxiliary sessions** for probes, reviewer tests, model
   confirmation or toolset validation. All of them use the same one-shot form.
4. **Never delete sessions.** Prevention via the hidden source is the mechanism;
   automatic deletion of the user's history is out of bounds for this skill.

### Visibility evidence (version-dependent)

Validated against **Hermes Agent v0.21.5** (build `7024.g7653424`, 2026.9.24).
Re-validate when the CLI changes; this is observed behaviour of one version,
not a permanent API guarantee.

- `oneshot` covers finite non-interactive runs (`hermes chat --oneshot -q`,
  `-Q`, `hermes -z`, non-TTY `-q`) and is described as hidden from the TUI,
  Desktop and dashboard session pickers, like `kanban` and `tool`.
- `tool` is the documented tag for third-party integrations and "should not
  appear in user session lists". That help text was **not sufficient** in the
  tested version: the Desktop **Project tree** excluded only
  `["cron", "kanban", "oneshot"]` (see `tui_gateway/methods_projects.py` in the
  Hermes source), so a `--source tool` run still appeared in the Project session
  list. `oneshot` was the tag the Project view actually filtered out.
- The Desktop sidebar recents sent
  `exclude_sources=acp,cron,kanban,oneshot,subagent,tool,...` in the same
  version.

**Known limitation (document it, never auto-delete):** picker-hidden is not
storage-hidden. `hermes sessions list` still shows one-shot sessions, and
`hermes -c` / `--resume latest` can continue the last one-shot. If a future
Hermes version cannot hide an auxiliary run, record that limitation here instead
of deleting the user's history.

**Sensitive diffs are persisted twice.** The brief carries the diff, so a review
of sensitive code writes that diff into two places: the Hermes session store (see
above) and `<scratch-dir>/review.jsonl`. Treat both as sensitive artifacts, clean
the scratch directory after the review, and let the user decide explicitly
whether to delete the session. Never delete sessions automatically.

## Procedure

### Step 1 - Discover the reviewer

1. `hermes config get auxiliary.review` (or read the file at the path printed by
   `hermes config path`).
2. Extract `provider`, `model`, and `reasoning_effort` (when present).
3. Completion criterion: you hold an explicit provider + model string, OR you
   have established that `auxiliary.review` is **not** configured.
4. If it is **not** configured: do NOT fake an independent reviewer. State the
   absence plainly and ask whether to use another model explicitly (the flow
   stops there until the user answers).
5. Compare the configured reviewer `model` with the model **you** are currently
   running on. A separate process and a clean context still hold when the two
   models are the same, but the "second opinion" is weaker: record the outcome
   as `distinct from implementer model: yes/no` in the report (Step 9) instead
   of implying model independence you did not have.

Never silently reuse the main agent as if it were an independent reviewer.

### Step 2 - Acquire the diff (read-only)

Detect the source and collect only what the reviewer needs.

- **GitHub PR:** `gh pr view <N> --json number,title,state,baseRefName,headRefName,url,mergeable,mergedAt,changedFiles,additions,deletions`, then `gh pr diff <N>`. Add `gh pr checks <N>` if CI status matters. Prefer `gh pr diff`.
- **Local branch vs base:** `git diff --stat <base>...HEAD` + `git diff <base>...HEAD`.
- **Staged:** `git diff --cached`.
- **Unstaged:** `git diff`.
- **Explicit commit/range:** `git diff <range>`.

Never modify Git state while collecting (no add, commit, checkout, stash or
reset). If the diff is very large, pass `--stat` plus per-file diffs and note
the truncation instead of reviewing a partial diff silently.

### Step 3 - Run the isolated reviewer

```bash
mkdir -p "<scratch-dir>"
hermes chat --query-file <brief> \
  -m <model> --provider <provider> --reasoning <level> \
  -t none --ignore-rules -Q --max-turns 2 \
  --in <scratch-dir> --format stream-json --source oneshot \
  > "<scratch-dir>/review.jsonl"
```

- `<model>` and `<provider>` come from Step 1. Provider identifiers (`custom`,
  `openai`, `anthropic`, ...) and full display names are both accepted.
- `--reasoning <level>` comes from `auxiliary.review.reasoning_effort` when that
  key is present. If it is absent, omit `--reasoning` entirely and let the CLI
  default apply; never invent a level.
- `-t none` yields **zero tools** (verified with `-v`: "No tools loaded"). Do
  NOT use `-t ""`: an empty value falls back to the default toolsets and the
  reviewer runs with full tools. `-t none` prints a cosmetic
  `Warning: Unknown toolsets: none`; that warning is expected. Version-dependent:
  re-validate on a CLI change.
- `--ignore-rules` skips injection of memory, `AGENTS.md`/`SOUL.md` and preloaded
  skills, so the reviewer sees only the brief.
- `--in <scratch-dir>` scopes the reviewer's working directory. This is **defense
  in depth**, not a sandbox: the real write barrier is `-t none`, which leaves the
  process with no tool able to write anywhere.
- `--max-turns 2` keeps the run finite even if tools were ever re-enabled; with
  zero tools it is inert.
- `-Q` (or `--oneshot`) makes the run finite and non-interactive; combined with
  `--source oneshot` it is tagged as an internal auxiliary run, hidden from the
  Desktop/TUI/dashboard pickers (see **Auxiliary runs are internal
  implementation**).
- Redirect the stream to an absolute path inside the scratch directory, as
  shown: Step 4 parses that file, and writing it outside the user's repositories
  keeps the review artifacts out of their working tree. The invoking shell does
  the redirect, not `--in`, so a bare `review.jsonl` would land in the agent's
  current directory, usually the repository under review.
- Pass the diff inside the brief; never share the implementer's conversation.

### Step 4 - Confirm the model that actually ran

Parse the stream for the init event:
`{"type":"system","subtype":"init","model":"<actual>"}`.

- `<actual>` == configured model -> independent run confirmed.
- Differs (fallback) -> treat it as an important finding: report the fallback and
  do NOT claim the configured reviewer was used.
- The init event exposes the **model only**, not the provider. State the provider
  from config, never from the stream.
- `<actual>` == your own model -> process/context isolation still holds, but
  report `distinct from implementer model: no` (Step 1.5). Never present a
  same-model run as model-independent.
- Sanity check: `grep -cE '"type": ?"tool_use"' "<scratch-dir>/review.jsonl"` must
  be `0`. The pattern requires an unescaped `"type"` key, so the brief echoed
  back into the stream, where the same text appears as `\"type\"`, cannot cause a
  false positive. Note that `grep -c` exits `1` when the count is `0`, which is
  the expected result here; read the number, not the exit status (use `|| true`
  in scripts under `set -e`).

### Step 5 - Read-only enforcement

The reviewer is strictly read-only: no editing, creating, moving or deleting
files; no commit, push or merge; no branch or remote changes; no published
comments; no dependency installs. Supply any extra context yourself in the
brief. After the run, confirm nothing changed (`git status --short` still
matches) and that no write operation ran.

### Step 6 - Reviewer briefing

Brief = only useful context: objective, known requirements/acceptance criteria,
files changed, the full diff, available test results, necessary architecture
context. Do not dump the whole conversation.

The reviewer runs with `--ignore-rules` in a clean process, so it cannot infer
anything you do not write down: fill `<language>` in the template with the
language the final report should be written in, and spell out every requirement
the diff is supposed to satisfy.

Ask the reviewer to look for: logic bugs; regressions; requirement violations;
typing problems; security issues (when applicable); concurrency/state issues
(when applicable); edge cases; error handling; missing or insufficient tests;
implementation-vs-docs divergence; unnecessary complexity; overengineering;
out-of-scope changes.

Brief template (the outer fence is four backticks so the inner one survives):

````markdown
You are an independent, rigorous code reviewer. Respond in: <language>.
Do NOT modify files. Do NOT use tools. Output the review text only.

## Context
- Purpose of the change:
- Acceptance criteria / requirements:
- Files changed:
- Test results known:
- Architecture context:

## Look for
bugs; regressions; requirement violations; typing; security; concurrency/state;
edge cases; error handling; missing tests; doc divergence; needless complexity;
overengineering; out-of-scope edits.

## Finding format
Severity (critical|high|medium|low|informational); file:line; description;
evidence; impact; recommendation. Avoid false positives; do not invent
requirements. Anything outside the known contract is a suggestion, not a bug.

## Diff
```diff
<paste diff here>
```
````

### Step 7 - Findings

Each finding: severity (`critical`, `high`, `medium`, `low`, `informational`),
`file:line` when applicable, objective description, evidence, impact, and
recommendation. The reviewer must avoid false positives and must not invent
requirements; anything outside the known contract is a suggestion/observation,
not a confirmed bug.

### Step 8 - Validate the findings (main agent)

Reviewer findings are not truth. For each one: analyse it; reproduce or verify
the important ones when possible (running tests is fine, editing is not);
classify it as `confirmed`, `rejected`, or `inconclusive`. Do NOT implement
fixes automatically unless the user asked for an implement/review/fix cycle or
the current task explicitly authorizes fixes.

### Step 9 - Report

Emit the report in the user's language, using this structure:

```markdown
### Reviewer
- provider:
- model:
- reasoning:
- independent run confirmed: yes/no
- fallback detected: yes/no
- distinct from implementer model: yes/no

### Reviewed scope
- diff source:
- files:
- approximate diff size:
- known tests:

### Findings
(table or list by severity)

### Validation
- confirmed:
- rejected:
- inconclusive:

### Verdict
approved | approved with observations | changes recommended | changes required before approval

### Safety
- no file was modified: yes/no
- no Git/GitHub write operation was performed: yes/no
```

## Pitfalls

- **Faking independence**: running the main agent (same session/model) and
  calling it a reviewer. Never do this; if no reviewer is configured, say so.
- **`-t ""` does not disable tools** - it silently re-enables the default
  toolsets. Use `-t none`, which really loads zero tools. Confirm with `-v`
  ("No tools loaded") or with zero `tool_use` events in the stream.
- **Provider is not in the stream.** Only the model appears in the init event;
  report the provider from config, not from the stream.
- **`hermes config get` warns on unknown dotted keys.** Read the printed value;
  treat the warning as informational.
- **`hermes chat` may not inherit the shell's cwd.** Pass `--in <dir>`
  explicitly.
- **Huge diffs**: pass `--stat` plus per-file diffs and note the truncation
  rather than silently reviewing a subset.
- **Interactive auxiliary runs.** Launching `hermes chat` without `-Q`/`--oneshot`
  creates a visible interactive session. Probes, reviewer smoke tests, model
  confirmation and toolset validation are one-shot too, never interactive.
- **Deleting sessions to tidy up.** Never. `--source oneshot` keeps the row out of
  the user's pickers; deleting history is not this skill's job even if a run
  somehow shows up.
- **Choosing the right shell**: this skill declares Windows support only, and its
  command blocks are POSIX-shell syntax (bash / git-bash). PowerShell is not
  drop-in compatible; translate line continuations, `grep`/`wc` and redirection
  before using a block there, or run it through git-bash.
- **Treating version-validated behaviour as a guarantee.** The `-t none` and
  `--source oneshot` findings were validated on one Hermes version. Re-validate
  after a CLI upgrade instead of assuming.
- **Claiming untested platforms.** This skill is Windows-tested; do not extend
  `platforms` without actually running it elsewhere.

## Verification

- [ ] Reviewer provider/model read from `auxiliary.review` (or absence reported).
- [ ] Diff acquired read-only; source stated.
- [ ] Reviewer ran in a separate `hermes chat` process with explicit provider/model.
- [ ] `-t none` used; tool-use count in the stream is `0`.
- [ ] Auxiliary run was one-shot (`-Q`/`--oneshot`) with `--source oneshot`; no interactive auxiliary session and no session deleted.
- [ ] Init event model matches the configured model; fallback checked and reported.
- [ ] Report states whether the reviewer model differs from the implementer's.
- [ ] No file/Git/GitHub write occurred; `git status` unchanged after the review.
- [ ] Findings validated and classified; report emitted in the user's language.
