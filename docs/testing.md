# Testing and validation

What to check before a skill is published, and how this repository validates a
change.

## Pre-publication checklist

Run this on every skill you add or change.

### 1. Frontmatter

Parse the YAML and confirm the required keys exist and are well-formed:

```bash
python - <<'PY'
import re, yaml, pathlib, sys
for p in pathlib.Path("skills").rglob("SKILL.md"):
    c = p.read_text(encoding="utf-8")
    assert c.startswith("---\n"), f"{p}: missing frontmatter"
    m = re.search(r"\n---\s*\n", c[3:])
    fm = yaml.safe_load(c[3:m.start() + 3])
    for key in ("name", "description", "version", "platforms"):
        assert key in fm, f"{p}: missing {key}"
    assert fm["name"] == p.parent.name, f"{p}: name != directory"
    hermes = (fm.get("metadata") or {}).get("hermes") or {}
    assert hermes.get("tags"), f"{p}: metadata.hermes.tags missing"
    assert hermes.get("category"), f"{p}: metadata.hermes.category missing"
    print(f"OK {p.parent.name} v{fm['version']} platforms={fm['platforms']}")
PY
```

Checks performed: `name`, `description`, `version`, `platforms`,
`metadata.hermes.tags`, `metadata.hermes.category`, and `name == directory`.

### 2. Markdown

- Headings are ordered and not skipped.
- Code fences are balanced and language-tagged.
- Relative link targets resolve to a file that exists in the repository.
- Tables render (consistent pipe counts per row).

### 3. Secrets and personal data

Grep the whole skill for anything that must not be published:

```bash
# Personal paths and usernames (adapt the patterns to your environment)
rg -n -i 'C:\\\\Users\\\\|/home/|/Users/|<your-username>|@gmail\.com' skills/
# Credential-shaped strings
rg -n -i 'api[_-]?key|token|secret|password|BEGIN [A-Z ]*PRIVATE KEY|ghp_|gho_|sk-' skills/
# Session identifiers and private infra
rg -n -i 'session[_-]?id|internal\.|\.corp|10\.|192\.168\.' skills/
```

Every hit is either removed or converted into a clearly generic example. A
secret is never "probably fine".

### 4. Platform honesty

For every platform in `platforms`, confirm the skill was actually run there.
Remove any platform that was not tested. Also scan the body and the changelog
for sentences that imply untested compatibility.

### 5. Genericity

Read the skill as if you were a different Hermes user on a clean machine. Any
step that only works because of your local setup must become a documented
prerequisite.

### 6. Consistency between `README.md` and `SKILL.md`

The skill README explains the same behaviour as `SKILL.md`, without duplicating
it wholesale. If the version or the commands differ between the two files, the
skill is wrong.

### 7. Automated tests

A skill that ships scripts should ship tests for them, in the skill's own
`tests/` directory, using the standard library (`unittest`) so that running them
needs no dependency install:

```bash
cd skills/<skill-name>
python -m unittest discover -s tests -t tests
```

Requirements:

- **No network and no live service.** Anything that would reach out is stubbed
  or skipped. A test that needs a running server is not a test, it is a manual
  step, and it belongs in the skill's verification checklist instead.
- **Hermetic against the host.** Tests must not read the machine's real
  configuration, home directory, or installed applications. If the code under
  test discovers paths, pin that discovery to a temporary directory in the test
  setup. A suite that passes only because of the author's machine is not
  evidence.
- **Deterministic.** No sleeps, no clock dependence, no ordering assumptions.
- **The suite runs green before the change is proposed.** Report the exact
  command and the observed result in the pull request.

### 8. Code-to-documentation consistency

When the code emits identifiers that must be documented — finding IDs, exit
codes, states, event types — add a test that asserts every emitted identifier is
documented, with the same severity or meaning. The writing rule is in
`docs/development.md`.

- **Derive the identifiers from the code**, not from a second hand-written list,
  so emitting one without documenting it fails the suite.
- **Assert the scan found a non-empty set**, so a documentation reformat cannot
  turn the test into a no-op that passes forever.
- **Parse the structure you must read, not whole lines.** A line-shaped regex
  both missed identifiers grouped into one table cell and matched unrelated
  tables, failing on correct documentation. Read the table's cells instead.

Reference implementation: the consistency test in the `task-checkpoint-recovery`
skill's suite, which scans `scripts/` for emitted finding IDs and requires each
one, with its real severity, in `references/schema.md` and `README.md`.

## Independent review

A skill change must pass an independent review by a different model/context
before merge. This repository's own `independent-code-review` skill is designed
for exactly this: it runs a reviewer in a separate, isolated process, with no
tools and no shared context, and produces graded findings.

The review must cover: security; leakage of private information; genericity;
technical correctness; documentation; potentially dangerous instructions;
declared compatibility; false sense of read-only; version dependencies;
overengineering; and readiness for open-source publication.

## Validating findings

Reviewer output is not truth. For each finding, the author must:

- analyse it against the actual code and requirements;
- reproduce it when possible (running tests is fine; editing during review is not);
- classify it as **confirmed**, **rejected**, or **inconclusive**.

Confirmed `critical`, `high`, or `medium` findings are fixed, then the change is
re-validated and reviewed again. Low or informational findings may be accepted
deliberately when documented.

## Manual verification loop

```bash
# 1. install the changed skill into a scratch profile or a test Hermes home
# 2. run the procedure the skill describes, end to end
# 3. confirm the verification checklist in SKILL.md actually passes
# 4. confirm nothing outside the intended scope was changed
git status --short
```

If the skill touches Git or GitHub, confirm afterwards that no branch, commit,
file, or remote changed unexpectedly.
