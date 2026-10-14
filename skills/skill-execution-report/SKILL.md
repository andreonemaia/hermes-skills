---
name: skill-execution-report
description: "Use when you need to know which skills a Hermes session actually used. Classifies availability, loading, invocation and proven execution from read-only evidence, never from mentions or counters alone."
version: 0.1.0
author: Hermes Skills contributors
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [skills, usage, audit, evidence, session, state-db, read-only, telemetry]
    category: software-development
---

# Skill Execution Report

Read-only evidence for the question "which skills did this session really use?".

## Core principle

Availability, mention, loading, invocation and proven execution are **different
things** and the tool never collapses them. A skill that exists in the index is
available; a name that appears in prose is a `mention`; a successful `skill_view`
proves loading; an invocation needs a call attributable to the skill; proven
execution needs an observable result (a verified artifact or commit). Anything
that cannot be attributed to a skill is reported as `unverifiable` and is never
counted as use.

Second principle: **unknown is a valid answer**. Cost, duration and result are
reported as unknown whenever the sources do not carry them; they are never
estimated from context, and two sources are never summed into one number.

## What this skill is not

- Not a telemetry uploader: no network call is made, nothing leaves the machine.
- Not a state mutator: `state.db` is opened with `mode=ro` plus `PRAGMA
  query_only`; `.usage.json` is only read; native counters are never changed.
- Not an executor: commands found in a transcript are never run.
- Not a replacement for the native usage counters: it is slower on purpose,
  because it explains *why* a skill counts as used.

## When to use

- Audit a session before sharing a transcript or writing a post-mortem.
- Check whether a skill was actually exercised, or only loaded.
- Verify a claim such as "I used skill X" without trusting the claim.
- Find skills that are loaded but never executed, or edited but never loaded.
- Cross-check `.usage.json` aggregates against what a single session shows.

## When not to use

- Live monitoring of a running session (the report is a point-in-time read).
- You only need counts (use the native counters).
- You need evidence from a machine or a `state.db` you cannot read.

## Prerequisites

- Python 3.10+ (standard library only, no third-party packages).
- Read access to `HERMES_HOME` (default `%LOCALAPPDATA%\hermes` on Windows).
- Optional: a local git working tree for commit verification.

## Quick reference

```bash
cd <skills-root>/skill-execution-report

python scripts/report.py --help
python scripts/report.py --session latest
python scripts/report.py --session latest --json
python scripts/report.py --session 20261012_080613_51acc2 --include-children --repo C:/path/to/repo
python scripts/report.py --session latest --json --out report.json
python scripts/report.py --session latest --no-usage --no-artifact-check
```

| Flag | Effect | Default |
|---|---|---|
| `--session REF` | full id, unique id prefix, or `latest` | `latest` |
| `--since ISO` / `--until ISO` | analysis window (ISO-8601 or epoch seconds) | whole session |
| `--include-children` | analyse linked child sessions in a separate scope | off |
| `--db PATH` | `state.db` location | `HERMES_HOME/state.db` |
| `--usage PATH` / `--no-usage` | `skills/.usage.json` location / skip it | `HERMES_HOME/skills/.usage.json` |
| `--skills-root PATH` | extra skills root for the known-name index (repeatable) | `HERMES_HOME/skills` |
| `--repo PATH` | local git repo used to verify commits | `git_repo_root` of the session |
| `--no-artifact-check` | do not stat produced files or verify commits | artifact check on |
| `--json` | structured JSON output | human text (PT-BR) |
| `--out PATH` | also write the report to a file | stdout only |
| `--max-skills N` | cap the `available_skills` list in JSON | `200` |

## Procedure

1. **Locate the skill directory** and run the CLI from there, so `scripts/` is on
   `sys.path`.
2. **Pick the scope.** `--session latest` is the most recent visible session; use
   an explicit id when the audit must be reproducible. Add `--include-children`
   only when child sessions are in scope; their evidence is reported under
   `child:<id>` scopes and never merged into the parent.
3. **Run the report read-only.** Add `--no-artifact-check` for a fast pass with no
   filesystem stats; keep it on when a claim of execution must be substantiated.
4. **Read the exit code first**, then the findings.
5. **Check the counters and the divergence rows** before quoting any number:
   `divergences` lists every field where `.usage.json` and `state.db` disagree,
   and the two are deliberately never added together.
6. **Quote the state, not the mention.** Report a skill as `loaded` or
   `invoked` only when a finding says so, and always with the
   `limitations`/`unknowns` that come with it.
7. **Never paste raw excerpts from a private session into a shared report.**
   Excerpts are redacted and truncated by the tool, but the safest practice is to
   share the JSON with `--max-skills` and the counts only.

## Classification ladder

| State | Rank | Meaning | Minimum evidence |
|---|---|---|---|
| `available` | 0 | exists in the skills index, no usage record in scope | reserved in v0.1; reported through `available_skills` and `counts.skills_available` |
| `discovered` | 1 | the name appears in the analysed records | `mention`, a failed `skill_view`, a `skill_manage` edit, or an unattributable signal |
| `loaded` | 2 | the skill content was retrieved | a `skill_view` call with an observed success |
| `invoked` | 3 | a call attributable to the skill happened | `skill_script` or `skill_manage` observed in the session |
| `execution_proven` | 4 | execution with an observable result | successful script run **plus** a verified `artifact_file` or `artifact_commit` |
| `unverifiable` | 0 | a signal exists but cannot be attributed | aggregate counters with no link to the analysed session |

A finding is always reported at the strongest state its own evidence supports;
weaker signals are kept in the `evidence` list and in `state_reason` instead of
being promoted. See `references/evidence-rules.md` for the exact decision order.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | report produced and at least one finding exists |
| `1` | report produced, no finding at all (no skill activity in scope) |
| `2` | source unreadable, corrupt, or an output file could not be written |
| `3` | source missing (no session in `state.db`, unknown or ambiguous session id) |
| `4` | usage error (`--since` after `--until`, bad timestamp, bad arguments) |

Codes `2`, `3` and `4` are errors: the message goes to stderr and **no report is
produced** (empty stdout is expected). Codes `0` and `1` both carry a full
report, so distinguish "nothing happened" from "I could not look".

## JSON contract

`schema: "skill-execution-report/v1"`. Full field reference in
`references/schema.md`. Top level: `generated_at`, `clock_source`, `analysis`,
`counts`, `findings`, `divergences`, `available_skills`, `sources`, `options`,
`limitations`, `unknowns`, `evidence_events`, `note`.

`counts.skills_unverifiable` includes aggregate-counter findings (global
`.usage.json` rows); `counts.skills_aggregate_only` is the subset of those that
exist **only** as aggregate counters, so the two are never confused with session
evidence.

## Pitfalls

- **A mention is not a load.** Text that names a skill (including this report
  itself) produces a `discovered` finding at most.
- **A counter is not this session.** `.usage.json` is global; aggregate findings
  carry `scope: "aggregate"` and `state: "unverifiable"`, and are never added to
  session findings.
- **A successful load is not an execution.** `skill_view` stops at `loaded`.
- **Compaction hides detail.** When a session contains compacted messages, the
  report adds the `compacted_messages_present` limitation; missing evidence may
  simply be summarised away.
- **A child session is not the parent.** Child evidence stays in its own scope and
  adds the `child_scope` limitation.
- **Do not re-run with `--include-children` to "fix" low counts**; it changes the
  scope, not the truth of the parent session.
- **Artifact verification only stats paths under the session `cwd`, the repo root
  or the provided `--repo`.** Paths outside those bases stay unverified.
- **`--out` writes a file.** Everything else is read-only; a plain report run
  never touches `state.db`.

## Verification checklist

- [ ] Exit code understood before reading the numbers.
- [ ] `sources.state_db.compatible` is `true` and no unexpected problem is listed.
- [ ] Every quoted state comes from a `findings` entry, with its `state_reason`.
- [ ] `divergences` reviewed; no number was produced by adding two sources.
- [ ] `unknowns` and `limitations` were carried into whatever the report feeds.
- [ ] No absolute home path, session id, secret or private excerpt left the
      machine in a shared artifact.

## Tests

Hermetic suite, no network, no real session data:

```bash
cd <skills-root>/skill-execution-report
python -m unittest discover -s tests -v
```

The suite builds a synthetic SQLite `state.db` and synthetic skills roots in
temporary directories, and pins the clock with `SER_NOW` for deterministic
timestamps.

## Limitations

- Windows-first: paths and defaults are documented for Windows; the POSIX
  defaults exist but are untested here.
- Depends on the Hermes `state.db` schema (tables `sessions`, `messages` and the
  child-session link). An incompatible schema is reported as a source problem
  instead of being guessed at; behaviour may change with a different Hermes
  version.
- `available` is part of the ladder vocabulary but v0.1 does not emit per-skill
  `available` findings.
- Cost and duration are reported as unknown: the sources do not carry per-skill
  cost.
