# Report schema (`skill-execution-report/v1`)

Source of truth: the JSON printed by `python scripts/report.py --json`.
The human report (`render_text`) is derived from the same payload, so both
outputs always describe the same analysis.

`schema_version` is `1` and `schema` is the string `skill-execution-report/v1`.
Any incompatible change bumps `schema_version` and the string together.

## Top level

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | `skill-execution-report/v1` |
| `schema_version` | int | `1` |
| `generated_at` | string | ISO-8601 UTC timestamp of the run, read from the clock named in `clock_source` (`SER_NOW` when set, otherwise the system clock) |
| `clock_source` | string | `system`, or `SER_NOW` when the clock was pinned |
| `note` | string | fixed read-only notice (PT-BR) |
| `analysis` | object | what was analysed and how |
| `counts` | object | per-state counters computed from `findings` only |
| `findings` | array | one entry per skill per scope |
| `divergences` | array | fields where `.usage.json` and `state.db` disagree |
| `available_skills` | array of strings | known skill names (capped by `--max-skills`) |
| `sessions_analysed` | array | main session plus analysed children |
| `sources` | object | per-source facts and availability |
| `options` | object | effective options of this run |
| `limitations` | array of codes | analysis-wide caveats |
| `unknowns` | array of codes | what could not be known |
| `evidence_events` | int | total evidence events collected |

## `analysis`

| Field | Meaning |
|---|---|
| `session_id`, `session_source`, `title` | resolved session identity |
| `scope` | list of scopes analysed: `main`, plus one `child:<id>` per analysed child scope |
| `include_children` | whether child sessions were analysed |
| `children_analysed` | number of child scopes analysed |
| `counters_are_global` | always `true` for `.usage.json` counters |
| `period` | `started_at`, `ended_at`, `duration_seconds`, `source` |
| `window` | `since`, `until`, `filtered`, `scope` |

## `counts`

`skills_available`, `skills_discovered`, `skills_loaded`, `skills_invoked`,
`skills_execution_proven`, `skills_unverifiable`, `skills_aggregate_only`,
`findings`.

`skills_available` comes from the skills index; every other counter is derived
from `findings`. Nothing here is the sum of two sources.

## `findings[]`

| Field | Meaning |
|---|---|
| `skill` | skill name |
| `scope` | `main`, `child:<id>`, or `aggregate` |
| `session_id` | session the evidence came from |
| `state` | `discovered`, `loaded`, `invoked`, `execution_proven`, `unverifiable`; `available` is documentary vocabulary only and is never emitted as a finding |
| `state_rank` | `0`..`4`, used for ordering (strongest first) |
| `state_reason` | machine-readable code explaining the state (see `references/evidence-rules.md`) |
| `evidence` | array of evidence events backing the finding |
| `result` | `{state, basis, detail}` with `state` in `succeeded`, `failed`, `unknown` |
| `verification` | `{state, basis, detail}` with `state` in `verified`, `unverifiable` |
| `counts` | per-finding event counters |
| `in_skill_index` | whether the name exists in the skills index |
| `first_seen_at`, `last_seen_at` | ISO timestamps of the first and last evidence event |
| `limitations` | per-finding limitation codes, including the limitations observed on the individual evidence events (an orphan `skill_view` result contributes `out_of_transcript_load`) |

Findings are sorted by `-state_rank`, then `skill`, then `scope`, so the output
is deterministic for the same input.

## `findings[].evidence[]`

| Field | Meaning |
|---|---|
| `kind` | `skill_dir`, `mention`, `skill_view`, `skill_manage`, `skill_script`, `artifact_file`, `artifact_commit`, `usage_counter` |
| `source` | source id: `state_db`, `usage_json`, `skills_index`, `git` |
| `at` | ISO timestamp, when the source carries one |
| `message_uid` | message handle inside `state.db` (never a session id) |
| `tool` | tool name observed in the transcript, when applicable |
| `ok` | `true`, `false` or `null` for the observed call result |
| `error` | short redacted error text, when the call failed |
| `detail` | human-readable description of the event |
| `basis` | for verified artifacts: `artifact_file` or `artifact_commit` |
| `artifact` | artifact description (path or commit) when verified |
| `excerpt` | redacted and truncated excerpt (max 160 chars) |
| `orphan_result` | `true` when the event was recovered from a result whose call was never observed, `null` otherwise |

## `divergences[]`

| Field | Meaning |
|---|---|
| `skill`, `field` | the skill and counter field that disagree |
| `usage_json`, `state_db` | the two values, reported side by side |
| `note` | reminder that the values come from different sources and must not be summed |

## `sources`

| Source | Fields |
|---|---|
| `state_db` | `name`, `present`, `bytes`, `modified_at`, `schema` (`compatible`, `problems`), `sessions_total` |
| `usage_json` | `name`, `present`, `bytes`, `modified_at`, `counters_are_global`, `malformed_entries` (structurally invalid entries found in the file, listed rather than ignored) |
| `skills_index` | `roots_count`, `skills_known` |
| `git` | `available`, `reason` (when unavailable), plus repository facts when available |

## `options`

`artifact_check`, `max_skills`, `skills_roots_count`, `sources_enabled`.
`sources_enabled` lists every source actually consulted: `state_db`, `usage_json`
(only when the file is present), `skills_index`, and `git` (only when a
repository was found).
The raw arguments that shape the analysis window live under `analysis.window`
and `analysis.include_children`.

## Determinism

- The clock is `SER_NOW` when that variable is set, and the system clock
  otherwise; `clock_source` records which one was used and `generated_at` is
  read from it.
- Same sources plus the same `SER_NOW` value produce byte-identical JSON,
  `generated_at` included: with `SER_NOW` set the timestamp is pinned as well.
- Only when `SER_NOW` is unset does `generated_at` depend on the wall clock.
- `--max-skills` only truncates `available_skills`; it never changes `findings`.

## Truncation

Message bodies and tool payloads are read from a bounded head only: the parser
never loads a whole oversized row, so a signal that sits past that head is simply
not seen. When a read is cut, that is marked instead of being silently accepted:

- `_truncated` on a salvaged tool payload — the payload itself was cut, so it is
  never read as the full result.
- `result_truncated` on the evidence event whose result came from such a payload,
  and `truncated` on an event whose message row went past the content cap.
- the per-scope read facts `truncated_messages`, `truncated_tool_call_messages`
  and `truncated_events`, which count how many rows were cut. These are internal
  read diagnostics: they qualify how much of the input was read and do not appear
  as JSON fields.
