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
- Internal links (`[text](relative/path.md)`) resolve to a file that exists.
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
