# Changelog

All notable changes to the skills in this repository are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the skills follow [Semantic Versioning](https://semver.org/). The version of
each skill is the `version` field in its own `SKILL.md`.

## [Unreleased]

## [1.0.0] - 2026-10-05

### Added

- Repository infrastructure: `README.md`, `LICENSE` (MIT), `CONTRIBUTING.md`,
  `CHANGELOG.md`, `.gitignore`, and the `docs/` guide set.
- `independent-code-review` skill: independent, read-only code review executed
  by a separate reviewer model in an isolated, finite (one-shot) process.
  Covers GitHub PRs, branch-vs-base diffs, staged changes, and unstaged
  changes. Tested on Windows.
