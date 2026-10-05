# Contributing

Thanks for considering a contribution. These rules are intentionally short.
They exist so that every published skill is safe to load, reproducible on a
clean machine, and honest about what it supports.

## Repository rules

1. **One skill per directory.** A skill lives at
   `skills/<skill-name>/SKILL.md`. Do not bundle unrelated procedures into one
   skill.
2. **`SKILL.md` is mandatory.** It must have valid YAML frontmatter with
   `name`, `description`, `version` and `platforms`, and a Markdown body that
   describes the procedure.
3. **The `description` is a trigger.** Its first 57 characters should stand
   alone as `Use when <trigger>. <one-line behavior>.`, because that is what the
   agent reads when deciding whether to load the skill.
4. **Documentation must be clear.** State prerequisites, exact commands, the
   completion criterion, known pitfalls, and a verification checklist.
5. **No secrets.** No API keys, tokens, passwords, private keys, cookies, or
   `.env` contents. Ever.
6. **No personal data.** No personal file paths, no usernames, no machine
   names, no session IDs, no email addresses.
7. **No private references.** No private repository names, internal hostnames,
   internal URLs, or references to non-public infrastructure. Generic examples
   are fine; make the generality explicit.
8. **Declare only tested platforms.** List a platform in `platforms` only if the
   skill was actually run there. Do not claim compatibility you did not test.
9. **Changes go through a branch and a pull request.** Never push directly to
   `main`.
10. **Independent review before merge.** A pull request that adds or changes a
    skill must pass an independent review (a different model/context from the
    author's) before merge.
11. **Do not claim untested compatibility.** The same principle as rule 8,
    applied to statements in the body and in the changelog.

## Adding a skill

```bash
git checkout -b feat/<skill-name>
mkdir -p skills/<skill-name>
# add SKILL.md (and README.md if the skill needs a human-facing explanation)
git add skills/<skill-name>
git commit -m "feat: add <skill-name> skill"
git push -u origin HEAD
gh pr create --title "Add <skill-name> skill" --fill
```

The pull request description should cover: motivation, how it works, tested
platform, security considerations, limitations, validations performed, and a
publication checklist.

## Review checklist

Before opening the pull request, confirm:

- [ ] frontmatter parses and `name`, `description`, `version`, `platforms` are present
- [ ] `metadata.hermes.tags` and `metadata.hermes.category` are set
- [ ] no secrets, no personal paths, no usernames, no session IDs
- [ ] no references to private repositories or infrastructure
- [ ] the body is generic enough for a different Hermes user to run as-is
- [ ] `README.md` (skill-level, if present) is consistent with `SKILL.md`
- [ ] declared platforms were actually tested

## License of contributions

By contributing you agree that your work is licensed under the MIT license of
this repository.
