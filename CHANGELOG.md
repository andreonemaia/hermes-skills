# Changelog

All notable changes to the skills in this repository are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the skills follow [Semantic Versioning](https://semver.org/). The version of
each skill is the `version` field in its own `SKILL.md`.

## [Unreleased]

### Fixed

- `task-checkpoint-recovery` documentation aligned with the implementation: a stale
  `--expect-revision` exits 1 (`EXIT_DRIFT`), not 3; `verify` fails the run on every
  non-benign finding (not only with `--strict`); the resume prompt names the skill
  directory the commands must run from; the `README.md` file list names all four test
  modules.

### Added

- `task-checkpoint-recovery` skill (0.1.0): persist the logical/operational state
  of a long-running task (objective, steps, decisions, blockers, next actions,
  and a ledger of effect-carrying operations) to
  `<project>/.hermes/checkpoints/<task-slug>/`, and revalidate it against reality
  before resuming (`verify`, `resume-report`), so an interrupted task can be
  resumed without losing decisions or repeating work already done. Includes a
  compare-and-swap revision guard, a lock with timeout, atomic writes, quarantine
  of corrupted checkpoints, and a `--now` override for deterministic tests.
  Standard-library only; ships a `unittest` suite. Complements native sessions,
  file checkpoints and context compaction. Tested on Windows.
- `comfyui-model-manager` skill (0.1.0): discover a ComfyUI installation,
  inventory the model files on disk, verify a SHA-256 manifest against them,
  and fetch only the missing or corrupted entries (dry run by default). Also
  statically validates a workflow, in API or UI format, against a running
  server's `/object_info`. Standard-library only; ships a `unittest` suite.
  Complements the official `comfyui` skill. Tested on Windows.

## [1.0.0] - 2026-10-05

### Added

- Repository infrastructure: `README.md`, `LICENSE` (MIT), `CONTRIBUTING.md`,
  `CHANGELOG.md`, `.gitignore`, and the `docs/` guide set.
- `independent-code-review` skill: independent, read-only code review executed
  by a separate reviewer model in an isolated, finite (one-shot) process.
  Covers GitHub PRs, branch-vs-base diffs, staged changes, and unstaged
  changes. Tested on Windows.
