---
name: skill-quality-audit
description: "Use when a skill needs a readiness audit before publishing. Runs read-only structural checks, separates confirmed findings from signals, and hands semantic judgment to the agent."
version: 0.1.0
author: Hermes Skills contributors
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [skill-audit, quality, readiness, privacy, frontmatter, documentation, tests, read-only]
    category: software-development
---

# Skill Quality Audit

Audit a skill's quality and publishing readiness with **traceable findings and
declared limits**. The script runs deterministic structural checks over the
target in read-only mode; the agent judges the semantic items from the evidence
the script collected.

**Core principle:** every statement in the report is either executed, merely
reported, or explicitly unverifiable. Absence of evidence is never rendered as
approval.

## When to Use

- Before publishing a new skill or a new version of one.
- Before opening a PR that adds or rewrites `SKILL.md`, `README.md`, references
  or scripts of a skill.
- When the user asks whether a skill is ready, what it lacks, or whether it leaks
  personal data or credentials.
- As the gate that decides what to hand to `independent-code-review`.

Don't use for:

- Fixing the target. This skill reports; the author decides.
- Executing the target's scripts or tests. That requires explicit authorization
  and is out of scope in this version.
- Scoring skills on a numeric scale or issuing a safety seal.

## Prerequisites

- Python 3.11+ with `pyyaml` available for full frontmatter reading. Without it
  the script falls back to a restricted parser and reports SQA-027 for constructs
  it cannot decide. `--backend auto` is what falls back; asking for
  `--backend pyyaml` when PyYAML is not importable is a usage error (exit 3),
  never a frontmatter defect of the target, and nothing is installed to hide it.
- The target as a local directory. No network access is used.

## Quick Reference

```bash
# full audit, human-readable report
python scripts/audit_skill.py audit --target <skill-dir>

# machine-readable report, written outside the target
python scripts/audit_skill.py audit --target <skill-dir> --format json --out report.json

# list the catalogue
python scripts/audit_skill.py checks

# content manifest, for proving the target did not change
python scripts/audit_skill.py manifest --target <skill-dir> --out before.json
python scripts/audit_skill.py audit --target <skill-dir> \
  --check-unchanged before.json --fail-on never

# regenerate the published catalogue after editing checks.py
python scripts/render_checks.py
python scripts/render_checks.py --check
```

## Exit Codes

| code | meaning |
| --- | --- |
| 0 | audit finished with no finding at the requested `--fail-on` severity |
| 1 | at least one finding at that severity |
| 2 | target missing or not a directory |
| 3 | command line misuse |
| 4 | could not write the requested output |
| 5 | the target changed while it was being audited (a difference observed between the before and after manifests of this run) |
| 6 | the auditor itself failed unexpectedly; there is no trustworthy report |

`--fail-on` defaults to `high` and only chooses which findings produce exit 1.
`never` keeps every finding in the report and stops findings from producing
exit 1; it changes nothing else:

- a reference divergence stays visible as `matches_reference: false` and as the
  critical SQA-026 finding, even when the exit code is 0;
- a change proven during the run still exits 5, `never` or not, because exit 5
  reports an integrity failure, not a finding;
- an inconclusive comparison stays inconclusive: `matches_reference` is `null`
  (or the comparison is partial, with `holes_*` naming what was not observed),
  never "equal" and never "changed".

## Procedure

### Step 1: freeze the state you are auditing

```bash
python scripts/audit_skill.py manifest --target <skill-dir> --out before.json
```

Keep the manifest. It answers one question only: did the target move while the
audit ran? Pass it back with `--check-unchanged`, which compares against it and
reports the answer separately in `matches_reference`. A target can stay stable
during the run and still differ from an older reference; that is a reference
divergence (critical SQA-026, exit 1), not a change observed during the audit
(exit 5). When a file cannot be read, both comparisons stay inconclusive
(`null`, with `manifest_unreadable` naming it) instead of claiming "unchanged".

The manifest is a **private technical artifact**: it is deliberately not masked,
so two manifests compare exactly. Do not attach it to a PR, an issue or a chat;
`publication.manifest_private` in the report states this.

Both comparisons use the same rule, and it is per side: an absence proves an
addition or a removal only when that side was actually observed. A divergence
that was read (different content, or an entry only one side has outside the
unreadable area) still reports `false` even if another subtree could not be
listed. An entry missing because its subtree could not be listed goes to
`unconfirmed_added` or `unconfirmed_removed`, never to `added`/`removed`, and
makes an otherwise clean comparison inconclusive. `holes_before` and
`holes_after` name the subtrees that were not observed on each side, including
`.` when the root itself failed.
`reference_compared` says whether the comparison happened at all: without
`--check-unchanged` it is `false` and `matches_reference` is `null`, meaning "not
compared", never "equal".

### Step 2: run the structural audit

```bash
python scripts/audit_skill.py audit --target <skill-dir> \
  --format json --out report.json --check-unchanged before.json
```

Read `checks_run` before reading `findings`. A check that did not run is not a
pass: it appears with status `not_run` and a reason, and it must be reported as
a coverage gap.

### Step 3: triage findings by confidence, not by file path

- `confidence: confirmed` was observed directly in the target.
- `confidence: signal` is a heuristic hit that needs a decision from you.
  `needs_agent_confirmation` is `true`.

For every `signal`, decide and record the outcome:

1. read the location named in the finding;
2. decide whether the value is synthetic (a documented placeholder, a fixture
   built at runtime, a value the tests generate) or real;
3. keep the finding, or discard it with the justification written down.

A path under `tests/` or `fixtures/`, or a `test_*` filename, lowers **nothing
but the confidence**. Severity still describes the impact if the value is real:
a live credential in a test file keeps the impact of a live credential. Never
close a privacy finding because "it is only a test".

### Step 4: judge the semantic checklist

`semantic_checklist` lists SQA-101 to SQA-107 with the question each one asks.
The script does not answer them. Answer each with the evidence you read, and use
`validation: reported` or `unverifiable` when the evidence does not decide it.
Do not turn a checklist item into a pass because you did not look.

### Step 5: report coverage and limits

State, in this order:

- which checks ran and which did not, with the reason;
- which files were scanned (the `coverage.text_files` list) and which entries
  were skipped;
- what the manifest comparison proved (no change during the audit window) and
  what it does not prove (no effects outside the audited directory);
- what remains for human judgment.

### Step 6: hand off what is technical

When findings concern code, tests or scripts, forward them to
`independent-code-review` with the diff and the audit report. That skill runs a
separate reviewer process with its own model. Treat its findings like any other
signal: validate, then fix inside scope.

## What the Checks Actually Observe

The catalogue in `references/checks.md` is generated from `scripts/checks.py`
and is the single source of truth for codes, methods, default severities and
limits. Read it before claiming a check covers something.

Structural checks look at form: frontmatter presence and fields, name against
directory, description trigger, README existence and language signal, relative
links and paths, presence of tests when scripts exist, script references in
documentation, platform evidence, version consistency, unfinished-work markers,
capability claims, manifest integrity.

They do **not** understand meaning. A well-formed `description` can still
describe the wrong skill; a present README can still be false. Those are the
semantic items, and they stay with you.

## Privacy Findings

SQA-019 to SQA-022 report a location, a class and a length. The value is never
reproduced. The report is masked on export, but masking is **format-limited**:
only recognised classes are replaced, so free-form text, private names without a
format and target-relative paths survive. `publication.report_safe_to_attach` is
`false`: review the report before attaching it to a PR, an issue or a chat.

Detection is by fixed format: credential assignments, AWS keys, GitHub tokens,
`sk-` style keys, Slack tokens, Google keys, JWTs, `Bearer` values and private
key blocks; Windows and POSIX user paths, e-mail addresses and CPF-shaped
numbers; Hermes session identifiers; `.corp`/`.internal`/`.intranet`/`.lan`
domains, internal URLs and private IPv4 ranges.

What it cannot determine: whether a value is real or synthetic, whether a
credential is active, whether an address belongs to a real person, and whether a
private repository or service name is sensitive when it has no recognisable
format. Those stay with the agent (SQA-103, SQA-105) or are declared as limits.

## Regenerating the Catalogue

`references/checks.md` is generated, never hand-edited:

```bash
python scripts/render_checks.py          # rewrite the file
python scripts/render_checks.py --check  # exit 1 when the committed file is stale
```

The output depends only on `checks.CATALOGUE` and the templates in the script:
no clock, no working-directory contents, no scratch area. `tests/` asserts the
committed file is byte-identical to a fresh render, so the catalogue, the
documentation and the published command cannot drift apart silently.

## Limitations

- Read-only over the target, but the audit window is only as good as the
  manifest comparison: it proves no change inside the target directory, not the
  absence of external effects.
- Frontmatter is read by `pyyaml` when available and by a restricted parser
  otherwise. The restricted parser reports what it cannot decide (SQA-027)
  instead of guessing. A missing PyYAML only degrades `auto` to the restricted
  parser; `--backend pyyaml` without the dependency exits 3 as usage.
- Language and capability-claim checks are heuristics and always `signal`.
- The target's tests are never executed here; SQA-016 only observes that `tests/`
  exists, follows the `test_*.py` naming pattern and mentions `unittest` or
  `pytest`. It proves neither discovery nor execution, and a passing suite is not
  evidence of test quality.
- No scoring, no seal, no automatic publishing decision.

## Example: Auditing a Draft Skill

A request such as "audite a skill `minha-skill` antes de eu abrir o PR" leads to:

```bash
python scripts/audit_skill.py manifest --target ../minha-skill --out before.json
python scripts/audit_skill.py audit --target ../minha-skill \
  --format json --out report.json --check-unchanged before.json
```

A sanitized excerpt of the resulting report is in
`references/example-report.md`: one confirmed platform-claim finding (SQA-018),
one privacy signal from a test file that keeps `critical` severity, one check
that did not run, and the coverage block. No value from the audited target
appears in it.
