# independent-code-review

Get a code review from a **separate reviewer process** that has no tools and no
access to the author's conversation, and that is configured to run a different
model than the one that wrote the code. Whether that model really differed is
stated in the report: when the configured reviewer runs the same model as the
implementer, the skill reports a same-model second opinion instead of claiming
model independence.

## The problem it solves

An agent reviewing its own change in its own context is a weak reviewer: it
already "knows" what it intended, so it re-reads the diff the same way and
misses the same mistakes. Loading a second opinion inside the same session does
not fix this, because the author's context (its assumptions, its plan, its
earlier mistakes) is still there and anchors the reviewer.

This skill fixes it by making the reviewer a genuinely separate run:

- a **separate `hermes chat` process**,
- a **separate model** (the one configured at `auxiliary.review`; if that key is
  not configured, the skill stops and asks rather than pretending to have a
  reviewer),
- a **clean context** (`--ignore-rules`, no shared conversation),
- **zero tools** (`-t none`), so it can only reason about the diff you hand it,
- a **finite, one-shot run** (`--oneshot --source oneshot`), hidden from the
  Desktop/TUI session pickers.

The result is a review that finds what the author could not see, and that
cannot touch the repository.

## Workflow

```
        ┌──────────────────────┐
        │  main agent (author) │
        └──────────┬───────────┘
                   │ 1. read auxiliary.review  -> provider + model
                   │ 2. acquire diff (read-only)
                   │    gh pr diff / git diff
                   ▼
        ┌──────────────────────────────┐
        │ separate hermes chat process │   3. brief file (--query-file)
        │  -t none  --ignore-rules     │      diff embedded, no tools
        │  --oneshot --source oneshot  │      no shared context
        └──────────┬───────────────────┘
                   │ 4. stream-json: init event -> actual model
                   │    review text + graded findings
                   ▼
        ┌──────────────────────┐
        │  main agent validates│  5. confirmed / rejected / inconclusive
        │  and reports         │     (does not auto-apply fixes)
        └──────────────────────┘
```

### Why the implementer's context is not shared

The whole value is in the isolation. Three barriers:

1. **Process isolation** - the reviewer is a new `hermes chat`, not a subagent
   inside the author's session.
2. **Context isolation** - `--ignore-rules` stops memory, `AGENTS.md`/`SOUL.md`
   and preloaded skills from being injected, so the reviewer sees only the brief
   and the diff.
3. **Capability isolation** - `-t none` loads no tools at all, so the reviewer
   cannot read the rest of the repo, cannot run commands, and cannot write
   anything. It analyses the diff delivered inside the brief, nothing else. This
   is an absence of capability in the reviewer process, not a kernel-level
   sandbox: the guarantee rests on the CLI honouring `-t none` (re-validate it on
   a CLI upgrade).

Anything the reviewer needs beyond the diff (requirements, acceptance criteria,
architecture) is added to the brief on purpose, by the author, explicitly.

### Acquiring the diff

| Source | Commands |
|--------|----------|
| GitHub PR | `gh pr view <N> --json number,title,state,baseRefName,headRefName,url,mergeable,mergedAt,changedFiles,additions,deletions` then `gh pr diff <N>` |
| Branch vs base | `git diff --stat <base>...HEAD` then `git diff <base>...HEAD` |
| Staged | `git diff --cached` |
| Unstaged | `git diff` |
| Range | `git diff <A>..<B>` |

Acquisition is strictly read-only: no `add`, `commit`, `checkout`, `stash` or
`reset` happens while collecting the diff.

### Read-only mode

The reviewer is read-only by construction: `-t none` leaves it with no tool that
could write anywhere. The run is additionally scoped with `--in <scratch-dir>`
and `--ignore-rules` as **defense in depth, not a sandbox**. After the run, the
author verifies that `git status --short` is unchanged.

### Confirming the model that actually ran

The reviewer may fall back to a different model. The skill parses the
`system/init` event of the `--format stream-json` output:

```json
{"type":"system","subtype":"init","model":"<actual-model>"}
```

If `<actual-model>` differs from the configured model, that is reported as a
finding and the author does **not** claim the configured reviewer was used. The
event exposes the model only; the provider is taken from the configuration.

### Validating findings

Reviewer output is a claim, not a fact. The main agent classifies every finding
as **confirmed**, **rejected**, or **inconclusive**, reproducing the important
ones when it can. Fixes are not applied automatically unless the task calls for
it.

## Installation

See [`../../docs/installation.md`](../../docs/installation.md). Short version:
copy this directory into the `software-development/` category folder of your
Hermes skills directory.

```
<hermes-data-dir>/skills/software-development/independent-code-review/
```

Then confirm with `hermes skills list`.

The skill needs a reviewer configured at `auxiliary.review` (provider, model,
and optionally `reasoning_effort`). Without it, the skill reports the absence
instead of pretending to be independent.

## Usage examples

### 1. Review a GitHub pull request

```
Review PR 42 of this repository independently.
```

The agent:
1. reads `auxiliary.review` for provider/model;
2. `gh pr view 42 --json ...` and `gh pr diff 42`;
3. runs the isolated reviewer on the diff;
4. confirms the model from the init event;
5. validates the findings and reports them, without commenting on GitHub.

### 2. Review staged changes

```
Run an independent review on what I have staged.
```

Diff source: `git diff --cached`. Nothing is committed.

### 3. Review unstaged changes

```
Independent review of my uncommitted changes.
```

Diff source: `git diff`.

### 4. Review a branch against its base

```
Independently review the diff of this branch against main.
```

Diff source: `git diff --stat main...HEAD` plus `git diff main...HEAD`.

## Limitations

- **Windows-tested only.** This version declares `platforms: [windows]`. Do not
  assume Linux/macOS behaviour that was not tested.
- **Version-dependent behaviour.** The `-t none` and `--source oneshot` findings
  were validated against one Hermes version. Re-validate after upgrading the
  CLI.
- **Picker-hidden is not storage-hidden.** One-shot runs stay out of the Desktop
  session pickers, but `hermes sessions list` still shows them, and
  `hermes -c` can continue the last one. The skill never deletes sessions to
  work around this.
- **Read-only means read-only.** The reviewer cannot fetch extra context itself;
  everything it needs must be in the brief.
- **No GitHub-side action.** The skill does not post review comments or approve
  PRs. Use a dedicated GitHub review skill for that, with explicit
  authorization.
- **Quality is bounded by the brief.** A vague brief produces a vague review.

## License

MIT, like the rest of this repository.
