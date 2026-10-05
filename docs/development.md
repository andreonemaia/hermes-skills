# Development

How to write, structure, and version a skill that fits this repository.

## Skill anatomy

```
skills/<skill-name>/
├── SKILL.md      # required
└── README.md     # optional, human-facing
```

### `SKILL.md` frontmatter

```yaml
---
name: <skill-name>                 # must match the directory name
description: "Use when <trigger>. <one-line behavior>."
version: 1.0.0                     # semantic versioning, per skill
author: <author or project>
license: MIT
platforms: [windows]               # only platforms actually tested
metadata:
  hermes:
    tags: [tag-a, tag-b]
    category: <category>
    related_skills: [other-skill]
---
```

Rules that matter:

- **`name`** must equal the directory name. A mismatch breaks lookup.
- **`description`** is the only thing the agent reads before deciding to load
  the skill. Keep the first 57 characters self-contained: `Use when <trigger>.`
  followed by a short behavioural phrase.
- **`platforms`** lists what you tested. Omit anything untested. It is better to
  under-claim than to claim support you cannot reproduce.
- **`version`** is independent per skill. Bump it whenever behaviour changes.

### Body structure

A body that works well in practice has these sections, in this order:

1. **One-paragraph summary** of what the skill does and its core principle.
2. **When to Use** / **When NOT to use**.
3. **Prerequisites**.
4. **Quick Reference** — the handful of commands someone actually runs.
5. **Procedure** — numbered steps, each ending in a completion criterion.
6. **Pitfalls** — mistakes that were actually observed, stated as a rule plus
   the reason.
7. **Verification** — a checklist to confirm the skill did its job.

## Writing rules

- **Write lessons, not logs.** State the rule and the reason. Do not narrate a
  debugging session or reference issue numbers and dates in the body.
- **Prefer exact commands over descriptions.** A command that was run and
  worked beats a paragraph explaining the general idea.
- **State the completion criterion per step.** If a step cannot fail, it is not
  a step.
- **Document version-dependent behavior as version-dependent.** If a behaviour
  was validated against a specific Hermes CLI version, say so and instruct
  re-validation when the CLI changes. Never present a validated behaviour as a
  permanent API guarantee.
- **Do not claim untested compatibility.** In the body, in the changelog, and
  in `platforms`.
- **No personal or private data.** No personal paths, usernames, machine names,
  session IDs, tokens, or private repository names. Convert anything specific
  into a clearly generic example.
- **Keep it generic.** Another Hermes user must be able to run the skill as-is.
  If a step depends on your environment, make that a prerequisite, not an
  assumption.

## Windows support

A skill may be declared `windows`-only. If a skill works on multiple platforms,
say so only after running it on each. When a procedure differs per shell,
show both a POSIX form and a PowerShell form rather than assuming Bash.

## Versioning and changelog

- Use [Semantic Versioning](https://semver.org/): `MAJOR` for a breaking change
  to the procedure or its interface, `MINOR` for new capability, `PATCH` for
  corrections that do not change behaviour.
- Add an entry to the repository `CHANGELOG.md` under `Unreleased`, then move it
  to a released version when the change is merged.
- One skill, one directory, one version. Do not version a group of skills
  together.

## Branch and pull request

```bash
git checkout -b feat/<skill-name>
# edit skills/<skill-name>/...
git add skills/<skill-name>
git commit -m "feat: add <skill-name> skill"
git push -u origin HEAD
gh pr create --title "Add <skill-name> skill" --fill
```

Never push directly to `main`. Every skill change is proposed through a pull
request and reviewed independently before merge (see `docs/testing.md`).
