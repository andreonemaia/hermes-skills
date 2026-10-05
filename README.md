# Hermes Skills

A public, community-oriented collection of skills for **Hermes Agent** (by Nous
Research). Each skill is a self-contained, reusable procedure that Hermes can
load on demand to perform a specific task the same way every time.

This repository is also an experiment in the open: skills are developed here,
reviewed independently, versioned with Git, and published through pull requests.

## What is a Hermes Agent skill?

A skill is a directory containing a `SKILL.md` file with YAML frontmatter and a
Markdown body. The frontmatter declares the skill's `name`, `description`
(which tells the agent *when* to load it), `version`, supported `platforms`, and
optional metadata such as tags and category. The body contains the actual
procedure: prerequisites, exact commands, pitfalls, and a verification checklist.

Skills are progressive disclosure: the agent reads only the `name` and
`description` at startup, and loads the full body only when the skill is
relevant to the current task. That means a good `description` matters as much as
a good body.

## Repository structure

```
hermes-skills/
├── README.md              # this file
├── LICENSE                # MIT
├── CONTRIBUTING.md        # how to contribute a skill
├── CHANGELOG.md           # Keep a Changelog style
├── .gitignore
├── docs/
│   ├── installation.md    # how to install skills into Hermes
│   ├── development.md     # how to write and version a skill
│   └── testing.md         # how to validate a skill before publishing
└── skills/
    └── <skill-name>/
        ├── SKILL.md       # required: the skill itself
        └── README.md      # optional: human-facing explanation
```

## Available skills

| Skill | Description | Platforms | Version |
|-------|-------------|-----------|---------|
| [`independent-code-review`](skills/independent-code-review/) | Run a code review with a separate reviewer model, in an isolated process, strictly read-only. Covers GitHub PRs, branch diffs, staged and unstaged changes. | windows | 1.0.0 |

## Installing a skill manually

Skills live in a `skills/` directory inside the Hermes profile directory.

1. Copy the skill directory into your skills tree, keeping the category folder:

   ```bash
   # Linux / macOS
   cp -r skills/independent-code-review ~/.hermes/skills/software-development/
   ```

   ```powershell
   # Windows (PowerShell)
   Copy-Item -Recurse skills\independent-code-review "$env:LOCALAPPDATA\hermes\skills\software-development\"
   ```

2. Verify that Hermes sees it:

   ```bash
   hermes skills list
   ```

3. The skill is now available. See `docs/installation.md` for details, including
   the exact profile paths on each platform.

## Using a skill

Skills load automatically when their `description` matches the task, or can be
invoked explicitly as a slash command using the skill name, for example:

```
/independent-code-review
```

Read the skill's own `README.md` for a human-oriented explanation of what it
does and worked examples.

## Compatibility and versioning

- Each skill declares the `platforms` it was actually tested on. **No skill in
  this repository claims support for a platform it was not tested on**, and an
  untested platform is deliberately omitted rather than guessed.
- Each skill is versioned independently with [Semantic Versioning](https://semver.org/).
  The `version` field in `SKILL.md` is the source of truth.
- Changes to a skill are recorded in the repository `CHANGELOG.md`.
- Some skills document behavior of a **specific Hermes CLI version**. Where that
  is the case, the skill states the version tested and instructs re-validation
  when the CLI changes. Behavior validated against one version is never
  presented as a permanent API guarantee.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.
In short: one skill per directory, a mandatory `SKILL.md` with valid
frontmatter, no secrets and no personal data, only tested platforms declared,
and changes proposed through a branch and a pull request that passes an
independent review before merge.

## License

[MIT](LICENSE).
