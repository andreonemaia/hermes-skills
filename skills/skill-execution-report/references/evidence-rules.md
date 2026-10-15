# Evidence and classification rules

Rules the v0.1 implementation actually applies. They exist to keep the report
honest: no state is granted on a signal that does not support it.

## Attribution first

A signal only counts for a skill when **both** conditions hold:

1. the signal carries the `skills/<name>/...` path form, and
2. `<name>` exists in the scanned skills index (`--skills-root`, default
   `HERMES_HOME/skills`).

Consequences:

- Naming a skill in prose (`"usei a skill X"`) is **not** evidence: without the
  path form, no event is produced at all.
- A message written as `skills/independent-code-review/` produces a `mention`,
  and a mention alone stops at `discovered`.
- Tool arguments are scanned the same way, so a shell command such as
  `python skills/foo/scripts/run.py` is attributable, while `python run.py`
  inside an unrelated directory is not.
- System and context rows are excluded from mention scanning, because they embed
  the whole skills index and would otherwise mark every skill as mentioned.
- When two skills roots contain the same skill name, the **first** root wins and
  the later copy is ignored.

## Decision order (`classify_skill`)

Evaluated top to bottom; the first match wins. Weaker signals stay in
`evidence` and `state_reason` keeps the reason.

| # | Condition | State | `state_reason` |
|---|---|---|---|
| 1 | a `skill_script` event with `ok is True` **and** a verified artifact | `execution_proven` | `skill_script_ok_verified_artifact` |
| 2 | a `skill_script` event with `ok is True` | `invoked` | `skill_script_ok` |
| 3 | a `skill_script` event with `ok is False` | `invoked` | `skill_script_failed` |
| 4 | a `skill_script` event with no observed result | `invoked` | `skill_script_result_unknown` |
| 5 | a `skill_view` event with `ok is True` | `loaded` | `skill_view_ok` |
| 6 | a `skill_view` event with `ok is False` | `discovered` | `skill_view_failed` |
| 7 | only `skill_manage` events | `discovered` | `skill_manage_only` |
| 8 | only `mention` events | `discovered` | `mention_only` |
| 9 | no events at all in scope | `discovered` | `empty_transcript` |

Reading notes:

- A failed invocation is still an invocation: the skill was exercised and it
  failed. That is different from a failed load, which never got the content.
- Editing a skill through `skill_manage` without loading it is `discovered`, not
  `loaded`. The reason is `skill_manage_only`; `edit_without_load` is the
  *limitation* that comes with it, not a `state_reason`.
- `execution_proven` requires the artifact to be verified at report time, so it
  disappears when `--no-artifact-check` is used. The state is a claim about
  evidence you can point at, not about intent.
- An invocation whose result was never observed keeps the state `invoked` with
  the reason `skill_script_result_unknown`. An unknown outcome is never read as
  an implicit success; only an observed success **plus** a verified artifact
  reaches `execution_proven`.
- A `skill_view` whose result was never observed grants no `loaded` state: the
  event stays in `evidence` with `ok: null`, the finding stops at `discovered`
  and `result_not_observable` is added to `limitations`. The signal is kept, but
  it is never a full confirmation.

## What counts as observable success

| Signal | Success basis | Rule |
|---|---|---|
| `skill_view`, `skill_manage` | `success_flag` | `result.success` must be a real boolean |
| any call in a failed turn | `failed_turn` | `display_kind == "failed_turn"` |
| any call with an error field | `error_field` | forces `ok = False` |
| script execution | `exit_code` | `ok = exit_code == 0` |

No `exit_code`, no `success` flag and no failed turn means the outcome is
**unknown**: `result.state` is `unknown`, and `_result_state` refuses to guess
from prose. `result.detail` reports "n of m executions with observed success".

## `skill_view` seen out of transcript

A `skill_view` can be split by the analysed window in two ways, and neither is
promoted to a plain in-window observation:

- **Result without its call.** A `tool` row whose `tool_call_id` matches no call
  in the window is recovered only when the row's `tool_name` is `skill_view` and
  the parsed result carries a non-empty `name`. The recovered event is emitted
  with `orphan_result: true` and the limitation `out_of_transcript_load`, because
  the call itself was never seen: the load is real but cannot be confirmed from
  this transcript alone. The limitation is repeated on the finding that consumes
  the event, so an orphan load never reads as a clean confirmation. Any other
  orphan result is dropped and counted in the `unattributed_tool_results` fact.
- **Call without its result.** The invocation is observed but its outcome is not,
  so no `loaded` state is granted from the call alone: without an observed
  success flag the load stays unproven, and a script keeps the reason
  `skill_script_result_unknown`.

In both cases the load is reported as recovered and conditional, never as an
in-window observation, and the missing half is left as unknown instead of being
filled in from prose.

## Artifact verification

An artifact is what separates `execution_proven` from `invoked`, so the rules are
spelled out precisely. Everything below is a statement about **evidence at report
time**, never about what the run did.

How a candidate is found and accepted:

- **File artifacts.** Paths are matched in the tool output text of that same call
  and accepted only when the path resolves inside an allowed base (the session
  `cwd`, the repository root, or the `--repo` path) **and exists as a file right
  now**. A missing path, a directory, or a path outside the bases stays
  unverified. Only the first accepted path is quoted in `verification.detail`.
- **Commit artifacts.** Hex tokens sitting near a commit-ish word in the same
  output are accepted only when `git cat-file`/`rev-parse` confirms the commit
  exists in the local repository. No repo found, no commit verification.
- **Correlation is per event.** The artifact must come from the output of a
  `skill_script` event whose `ok is True`. An artifact seen in another call never
  proves this skill ran.

`verification.state` is `verified` with `basis` `artifact_file` or
`artifact_commit`, otherwise `unverifiable` with a reason. A path that cannot be
pointed at is never reported as a produced artifact.

### What the evidence does not prove

- `artifact_file` proves **existence only**: a file the invocation referenced was
  on disk when the report ran. It does **not** prove that the invocation created,
  modified or authored it. A successful script that merely printed or read an
  existing path yields the same signal as one that wrote it, so the report never
  claims authorship.
- `execution_proven` means "an observed successful invocation **and** an artifact
  correlated to that invocation and verified". It is not a claim about creation,
  modification or authorship of the artifact, and it says nothing about the
  content being what the skill was supposed to produce.
- Verification is a **point-in-time stat**, not a check at execution time. A file
  created during the run and removed afterwards is unverifiable; a file that
  exists now but was written later is accepted.

Separating those claims is proposed in `docs/backlog.md` and deliberately not
implemented in v0.1.

## Missing sources are not missing executions

An unavailable source degrades the evidence, never the history: the run is still
reported, at the strongest state the remaining evidence supports.

- `--no-artifact-check` makes every artifact `unverifiable` (`detail`
  `verificação de artefatos desativada`) and adds the `artifact_check_disabled`
  limitation, so `execution_proven` disappears even for a run that did produce
  artifacts. The invocation itself is still observed, and the report says so
  instead of looking like a full run.
- No repository found: commit candidates cannot be confirmed by that mechanism,
  but a file artifact under an allowed base (the session `cwd`, for instance) is
  still verifiable and can sustain `execution_proven`. A script stays `invoked`
  with the limitation `no_correlated_artifact` only when no other correlated
  artifact is verifiable.
- Missing `.usage.json`: the usage source is dropped and the aggregate findings
  disappear; session evidence is unaffected.

In every case the report keeps the invocation and records the reason. A gap in
the evidence is never read as a fact about the run: absence of a verified
artifact is never presented as absence of execution.

## Two sources, never one sum

- `.usage.json` counters are **global** across sessions and histories.
- `state.db` evidence is **scoped** to the analysed session.
- Aggregate counters therefore produce findings with `scope: "aggregate"` and
  `state: "unverifiable"`, reason `usage_counter_only`. They are counted inside
  `counts.skills_unverifiable` and additionally exposed as
  `counts.skills_aggregate_only`, so they are visible without being mistaken for
  session evidence. Aggregate and session values are never added together and no
  field mixes the two sources.
- When the same skill shows different values in the two sources, each field is
  listed in `divergences` with both values and a note. The report never picks a
  winner and never computes a total across them.

## Scopes

- `main`: the resolved session.
- `child:<id>`: only with `--include-children`; evidence stays in its own scope
  and the `child_scope` limitation is added. Child findings never merge into the
  parent.
- Without the flag, `unknowns` carries `children_excluded`, so the report says
  what it did not look at.

## Unknowns and limitations

`unknowns` (codes): `cost_unavailable`, `duration_unavailable`,
`session_in_progress`, `usage_counters_are_global`, `children_excluded`,
`unattributed_tool_results`, `skill_events_without_call`,
`compaction_hides_detail`.

`limitations` (codes): `no_transcript_evidence`, `no_correlated_artifact`,
`artifact_check_disabled`, `out_of_transcript_load`, `edit_without_load`,
`compacted_messages_present`, `window_filtered`, `child_scope`,
`result_not_observable`.

`compacted_messages_present` is set when compacted messages are found; their
content is still scanned, but summarisation can remove evidence, so a missing
signal is not proof of absence.

## Degrading safely

| Situation | Behaviour |
|---|---|
| `state.db` missing | `sources.state_db.present = false`, message on stderr, exit `3`, no report |
| session not found | stderr `fonte ausente: session not found: <ref>`, exit `3`, no report |
| ambiguous session prefix | exit `3`, no guessing (shares the missing-source code) |
| missing `sessions`/`messages` columns | source reported incompatible, stderr message, exit `2`, no report |
| corrupt/partial SQLite | `fonte ilegível: cannot inspect table sessions: file is not a database`, exit `2`, no report |
| `usage.json` missing | usage source disabled (`sources.usage_json.present = false`, `options.sources_enabled` drops it), report still produced, exit `0` |
| structurally invalid `.usage.json` entries | the offending entries are listed in `sources.usage_json.malformed_entries`, the report is still produced, exit `0` |
| truncated tool payload | salvaged where possible: `_truncated` on the salvaged payload, `result_truncated` (payload cut) / `truncated` (message row past the content cap) on the evidence event, and the `truncated_messages`, `truncated_tool_call_messages` and `truncated_events` facts per scope. Only a bounded head of each payload is parsed, so a signal past that head is not seen |
| no findings at all | report produced, exit `1` (a valid, empty answer) |
| invalid window / bad args | stderr `erro: --since é posterior a --until`, exit `4`, no report |
| analysis ok, `--out` not writable | report on stdout, stderr message, exit `5` |
| unexpected internal failure | stderr message, exit `2`, no traceback |

Exit codes: `0` findings, `1` no findings, `2` corrupt (or otherwise unreadable)
source, `3` missing source, `4` usage error (including an invalid `SER_NOW`),
`5` analysis ok but `--out` could not be written.

Precedence when more than one condition applies:

1. `4` (usage) — arguments are validated before any source is read.
2. `3` / `2` (source) — a missing or corrupt source stops the analysis before a
   report exists, so stdout stays empty.
3. `5` (output) — the analysis and the stdout report succeeded, but `--out`
   could not be written.
4. `0` / `1` — a full report is on stdout.

Codes `2`, `3` and `4` are errors: the report is **not** produced, so an empty
stdout is expected. Codes `0` and `1` both carry a full report. An unexpected
internal failure also exits `2`, always with a message on stderr and never a
traceback.

`options.sources_enabled` lists every source actually consulted: `state_db`,
`usage_json` (only when the file is present), `skills_index`, and `git` (only
when a repository was found).

## Privacy

- Session ids are never printed in excerpts; messages are referenced by
  `message_uid`.
- Excerpts are redacted and truncated (`MAX_EXCERPT`, 160 chars); error text is
  capped at 120 chars.
- Absolute paths under the user's home are collapsed to a relative form in
  `display_path`.
- No network call is made; no file outside `--out` is written.

## Determinism and performance

- Timestamps come from `SER_NOW` when set, otherwise the system clock;
  `clock_source` records which one.
- Ordering is fixed: findings by `-state_rank`, `skill`, `scope`; evidence by
  timestamp then insertion.
- Messages are streamed with a `LIMIT`-bounded cursor per session, and only a
  bounded head of each payload is parsed, so the whole history is never loaded
  into memory.
