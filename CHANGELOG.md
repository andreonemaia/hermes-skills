# Changelog

All notable changes to the skills in this repository are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the skills follow [Semantic Versioning](https://semver.org/). The version of
each skill is the `version` field in its own `SKILL.md`.

## [Unreleased]

### Added

- `comfyui-model-manager` skill (0.1.0): discover a ComfyUI installation,
  inventory the model files on disk, verify a SHA-256 manifest against them,
  and fetch only the missing or corrupted entries (dry run by default). Also
  statically validates a workflow, in API or UI format, against a running
  server's `/object_info`. Standard-library only; ships a `unittest` suite.
  Complements the official `comfyui` skill. Tested on Windows.

### Changed

- Repository docs: `docs/development.md` now requires the identifiers a skill's
  code emits to stay in sync with its documentation, and `docs/testing.md`
  documents how to test that consistency.

## [1.0.0] - 2026-10-05

### Added

- Repository infrastructure: `README.md`, `LICENSE` (MIT), `CONTRIBUTING.md`,
  `CHANGELOG.md`, `.gitignore`, and the `docs/` guide set.
- `independent-code-review` skill: independent, read-only code review executed
  by a separate reviewer model in an isolated, finite (one-shot) process.
  Covers GitHub PRs, branch-vs-base diffs, staged changes, and unstaged
  changes. Tested on Windows.
