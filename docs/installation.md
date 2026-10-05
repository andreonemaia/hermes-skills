# Installation

This guide explains where Hermes Agent looks for skills and how to install a
skill from this repository.

## Where skills live

Hermes loads skills from the `skills/` directory inside its data directory.
The location depends on the platform:

| Platform | Skills directory |
|----------|------------------|
| Windows | `%LOCALAPPDATA%\hermes\skills` (typically `C:\Users\<you>\AppData\Local\hermes\skills`) |
| Linux / macOS | `${HERMES_HOME:-$HOME/.hermes}/skills` |

Inside that directory, skills are grouped by category, e.g.
`skills/software-development/`, `skills/github/`. The category folder is a
convention for organisation; what makes a directory a skill is the presence of
a `SKILL.md` file in it.

```
<skills-dir>/
└── software-development/
    └── independent-code-review/
        ├── SKILL.md
        └── README.md
```

If a non-default profile is in use, its data directory is separate, e.g.
`<hermes-data-dir>/profiles/<profile>/skills`. Run `hermes config path` to see
the active data directory for your installation.

## Installing a skill manually

1. Clone this repository (or download a single skill directory):

   ```bash
   git clone https://github.com/andreonemaia/hermes-skills.git
   ```

2. Copy the skill into the category folder of your skills directory:

   ```bash
   # Linux / macOS
   mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/software-development"
   cp -r hermes-skills/skills/independent-code-review \
         "${HERMES_HOME:-$HOME/.hermes}/skills/software-development/"
   ```

   ```powershell
   # Windows (PowerShell)
   $dest = Join-Path $env:LOCALAPPDATA 'hermes\skills\software-development'
   New-Item -ItemType Directory -Force -Path $dest | Out-Null
   Copy-Item -Recurse hermes-skills\skills\independent-code-review $dest
   ```

3. Verify that Hermes sees the skill:

   ```bash
   hermes skills list
   ```

   The skill should appear with its category and an `enabled` status.

## Updating a skill

Replace the skill directory with the newer version from this repository and
re-run `hermes skills list`. Keep the directory name identical to the skill
`name` in the frontmatter.

## Uninstalling a skill

Delete the skill's directory from your skills tree:

```bash
rm -rf "${HERMES_HOME:-$HOME/.hermes}/skills/software-development/independent-code-review"
```

```powershell
Remove-Item -Recurse "$env:LOCALAPPDATA\hermes\skills\software-development\independent-code-review"
```

## Notes

- Skills are scanned at session start. If you install a skill while a session
  is open, start a new session (or reload) before expecting its slash command
  to be registered.
- No skill in this repository requires global configuration changes. Anything a
  skill needs from your installation is declared in its own prerequisites
  section.
