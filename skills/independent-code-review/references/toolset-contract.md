# Toolset contract of `hermes chat` (measured)

Scope of the measurement: Hermes Agent **v0.22.1+104.gecac27c (2026.9.24)**, upstream
`ecac27c0`, Windows, the **CLI launch path**, run from a disposable working
directory:

```bash
hermes chat -Q -q "Responda apenas: ok" -m deepseek-v4.1-flash \
  --provider verboo-deepseek-v4-1 --format stream-json <pick>
```

`--provider` wants the provider's real name (the id under `providers:` in
`config.yaml`). Passing the config key instead is refused at launch with
`provider 'custom' resolved without credentials (no endpoint or API key
configured). If this is a named custom provider, use its real name`.

The probe command above ran on the default CLI source (no `--source` flag).

Do not generalise these numbers to another build, another provider, another model,
another configuration (the plugin set above all) or another launch surface
(Desktop/TUI/ACP, `session.create`) without re-measuring. The plugin list is part
of the measurement, not a constant: another profile can load different plugins, so
the resolved sets below do not carry over to it.

## What the runtime does with `-t/--toolsets`

The pick is validated at session launch in
`gateway/session_policy.py::_resolve_toolsets` (source order: the resolver runs
before the model is invoked). Measured result per pick (toolset count from the
resolver; run outcome from the CLI):

| Pick | Resolved toolset set | Observed run |
| --- | --- | --- |
| omitted | 16 platform toolsets (`browser`, `clarify`, `code_execution`, `computer_use`, `cronjob`, `delegation`, `file`, `image_gen`, `memory`, `session_search`, `skills`, `terminal`, `todo`, `tts`, `vision`, `web`) + plugin toolsets (`OpenPencil`, `web-search-plus`) | exit 0, 17,041 input tokens |
| `-t ""` | plugin toolsets only: `OpenPencil`, `web-search-plus` | exit 0, 6,139 input tokens |
| `-t web` | `web` + plugin toolsets | exit 0, 6,603 input tokens |
| `-t none` | refused: `RuntimeStoreError('invalid_params')` | exit 1, no model response (0 tokens) |
| `-t <unknown>` | refused: `invalid_params` | exit 1, no model response (0 tokens) |
| `-t web,none` | refused: `invalid_params` | exit 1, no model response (0 tokens) |
| `-t desktop_ui` | refused on the CLI path (only `source=gui` may pick it) | exit 1 |
| `--safe-mode` | 16 platform toolsets + `OpenPencil` (the plugin `web-search-plus` is subtracted) | exit 1, no model response (0 tokens): the run died before the toolset mattered |
| `-t "" --safe-mode` | `OpenPencil` only | exit 1, no model response (0 tokens) |

Reading:

1. `none` is not a registered toolset name, so it is a **hard validation error**,
   not a warning. The cosmetic `Warning: Unknown toolsets: none` belongs to the
   *config* path (`agent.disabled_toolsets` / `platform_toolsets` in
   `config.yaml`, which drop unknown names). The CLI flag path does not use it.
2. An empty pick is **not** "no pick": it stores `platform_toolsets.cli: []`, so
   the platform toolsets are gone, but the plugin toolsets are folded in
   regardless. `-t ""` therefore loads *fewer* tools than the default, not more.
3. **No tested combination left zero toolsets on this build.** The smallest usable
   surface a run can be given here is `-t ""` (two plugin toolsets); adding
   `--safe-mode` drops it to `OpenPencil` alone, which is still not zero. Scope:
   this build, this configuration, this plugin set and this launch path - the
   statement is a measurement, not a property of `hermes chat` in general.
4. `--safe-mode` could not be used at all on this path: every probe exited 1
   about two seconds in, after `init` (session created, model resolved) and
   before any model response, with zero tokens and the generic lost-turn text
   `Execution outcome is unknown. Resume this session to inspect the lost turn
   and /discard it; do not resend the input.` The cause was not isolated and the
   error is not toolset-specific. The safe worker also ignores user config, so
   the failure may be credential/endpoint resolution rather than the toolset
   subtraction. Do not read it as "safe-mode breaks toolsets", and do not present
   it as a zero-tool mode.
5. `OpenPencil` survives `--safe-mode`'s plugin subtraction, so it is not in the
   plugin-key list that worker subtracts.

## Reproduction

Deterministic resolution (no model response, no network): call the same function the
gateway calls at launch.

```python
import copy, sys
sys.path.insert(0, "<hermes-install-dir>")
from gateway.session_policy import _resolve_toolsets
from hermes_cli.config import load_config

config = load_config()
for label, params, safe_mode in [
    ("omitted", {}, False),
    ('-t ""', {"toolsets": []}, False),
    ("-t none", {"toolsets": ["none"]}, False),
    ("-t web", {"toolsets": ["web"]}, False),
    ("--safe-mode", {}, True),
    ('-t "" --safe-mode', {"toolsets": []}, True),
]:
    try:
        resolved = _resolve_toolsets(dict(params), copy.deepcopy(config), "cli", safe_mode)
        print(label, len(resolved), sorted(resolved))
    except Exception as exc:
        print(label, type(exc).__name__, exc)
```

`_resolve_toolsets(params, config, source, safe_mode)` stores an explicit pick
under `platform_toolsets['cli']` for every `source` except `acp`, so `source=cli`
and `source=oneshot` resolve identically for every pick above (measured); only the
omitted pick differs under `source=tui`, which folds in `project`. The probes ran
on the default CLI source (no `--source` flag); the reviewer command line adds
`--source oneshot` and `--ignore-rules`, and that full flag set measured 5,466
input tokens (model `glm-5.3-flash`, prompt a short review brief) with 0
`tool_use` events in the exported transcript (absence observed with declared
coverage; see **Classifying a transcript**).

Empirical run (one API call per case; the input-token count is a proxy for the
size of the tool surface):

```bash
hermes chat -Q -q "Responda apenas: ok" -m <model> --provider <provider> \
  --format stream-json <pick> > out.jsonl
grep -o '"input": [0-9]*' out.jsonl
```

## What this does NOT prove

- A count of zero `tool_use` events in a transcript is an observation about that
  run, not a capability statement: the toolsets in effect were still loaded. It is
  also only as good as its coverage (see **Classifying a transcript**): a missing,
  empty, truncated, unparseable or unframed transcript is *inconclusive*, never
  "zero tools used".
- An unchanged `git status` covers the observed working-tree state only. It says
  nothing about external effects, such as a call to a remote service or a write
  performed on the plugin side.
- `tool_names` is the schema of the tools *available* to the run, not the tools
  that were called. In the `--format jsonl` export it is the full schema payload
  (`{"version": ..., "tools": [...]}`); in `state.db` the same column is an opaque
  64-hex identifier of that tool set, carrying no names. Do not read either as
  call evidence.
- A separate scratch directory (`--in`) is scoping, not isolation.
- `HERMES_DUMP_REQUESTS=1` produced no new request dumps on this path (64 dumps
  before the probe batch, 64 after; the newest dump under `<hermes home>/sessions`
  predates the batch), so the toolset sets above come from the resolver plus the
  input-token deltas, not from an inspected wire payload. Recheck that directory
  count before relying on this again.
- The numbers come from one build, one provider, one model and the CLI path.

## Classifying a transcript

Three outcomes, never two:

| Outcome | What it takes |
| --- | --- |
| **calls detected** | at least one `tool_use` / `tool_result` event, or a `tool` row / non-zero `tool_call_count` in the session store |
| **absence observed with declared coverage** | every line parsed as JSON, the framing events (`system` + `init`, and the final `result`) were present, and no call event appeared |
| **inconclusive** | the record is missing, empty, truncated, unparseable, unframed, or carries an event type the parser was not validated against |

Precedence when several conditions apply at once: a missing, empty or unparseable
record is *inconclusive* first; then a detected call wins over an unvalidated event
type (a visible `tool_use` is decisive even if another event type is unknown);
then an unvalidated event type is *inconclusive*; only then does framing decide
between absence observed and *inconclusive*. A JSON object with no `type` key is
reported as `<missing type>`, not treated as a crash.

Two records can show calls on this build:

1. **The `--format stream-json` transcript of the run.** A call appears as a
   top-level event `{"type": "tool_use", "name": ..., "tool_call_id": ...,
   "input": {...}}` followed by `{"type": "tool_result", "name": ...,
   "tool_call_id": ..., "output": ...}` (measured shape). `--format text` prints
   the final response only and cannot show calls.
2. **The session store, after the run**: `sessions.tool_call_count` and the
   per-message `role='tool'` rows. Read it with
   `hermes sessions export --session-id <id> --format jsonl <out.jsonl>` (the
   session selector is `--session-id`; a bare positional argument is the *output*
   path, so `hermes sessions export <id>` exports the whole store into a file
   named `<id>`), or with a read-only sqlite query. Measured on the same build: a
   reviewer run with `-t ""` stored `tool_call_count=0` with no `tool` row, while a
   probe that called `tool_search` twice stored `tool_call_count=2` with two `tool`
   rows. In the export record, `tool_names` is the schema payload of the tools
   available to the run (3 entries, `tool_search`/`tool_describe`/`tool_call`, on
   the `-t ""` run; 20 on the default pick); in `state.db` the same column is an
   opaque 64-hex identifier of that tool set, carrying no names: every `-t ""`
   session measured shares one value and both default-pick sessions share a
   different one. Neither column is a record of calls.

Do **not** grep the raw transcript for a keyword. The final `result` event carries
the review text, and that text can mention `tool_use` in prose: in the reviewer run
measured here, `grep -c tool_use review.jsonl` returned `1` while the structured
count of `tool_use` events was `0`. The narrower
`grep -cE '"type": ?"tool_use"'` pattern happens to return `0` on that file and `2`
on a stream that did call tools, but it is still a text pattern: it cannot see
truncation, cannot see a call recorded in another shape, and cannot distinguish a
complete run from a partial one. Use it as a quick pre-check only, and parse the
JSONL for the verdict.

Example verifier (a parser, not a runner: it never launches anything). Validated
against the fixtures listed below:

```python
import json

KNOWN = {"system", "text", "tool_use", "tool_result", "result"}  # event types measured on this build


def classify(path):
    """Return (verdict, detail) for one --format stream-json transcript."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return "inconclusive", f"unreadable: {exc}"
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return "inconclusive", "empty transcript"
    events, bad = [], 0
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
        else:
            bad += 1
    if bad:
        return "inconclusive", f"{bad} unparseable line(s) of {len(lines)}"
    types = [e.get("type") for e in events]
    uses, results = types.count("tool_use"), types.count("tool_result")
    if uses or results:
        return "calls detected", f"{uses} tool_use / {results} tool_result"
    unknown = sorted(
        {"<missing type>" if t is None else str(t) for t in types if t not in KNOWN}
    )
    if unknown:
        return "inconclusive", f"event type(s) not validated against this build: {unknown}"
    framed = "system" in types and "result" in types and any(
        e.get("subtype") == "init" for e in events if e.get("type") == "system"
    )
    if not framed:
        return "inconclusive", "framing events missing (need system/init and result)"
    return "absence observed with declared coverage", f"{len(events)} events, framed, 0 call events"
```

Fixtures it was validated with (synthetic, in a scratch directory):

| Fixture | Expected verdict |
| --- | --- |
| clean run: `system`+`init`, `text`, `result` | absence observed with declared coverage |
| run that called a tool: adds `tool_use` + `tool_result` | calls detected |
| recognized call next to an unknown event type, every line valid JSON | calls detected |
| review prose that only *mentions* `tool_use`, inside the `result` text | absence observed with declared coverage |
| truncated: valid lines, no `result` event | inconclusive |
| invalid: unparseable lines | inconclusive |
| empty file | inconclusive |
| unknown event type | inconclusive |
| dict line with no `type` key, next to an unknown event type | inconclusive |
| missing path | inconclusive |

Applied to the real artifacts of this measurement: the reviewer transcript
(`review.jsonl`, 1,618 events, `system`/`init` + `result` framing) classified as
absence observed with declared coverage, and a probe transcript that called
`tool_search` twice (`probes/matrix/T_empty.jsonl`) classified as calls detected.

## Conservative procedure

1. Pass `-t ""` (smallest usable surface measured on this build).
2. Keep the brief's explicit prohibition ("Do NOT use tools. Output the review
   text only."). It is a requirement on the reviewer, not a claim that the process
   cannot call tools.
3. After the run, classify the transcript with the verifier above (or an
   equivalent parser) and record the verdict **and** its coverage. A transcript
   that is missing, empty, truncated, unparseable or unframed is inconclusive:
   report it that way instead of "zero tools used".
4. Cross-check the session store (`tool_call_count`, `tool` rows) for the same run
   when it matters.
5. Record which toolsets were in effect (rerun the probe above) and report them;
   never claim a tool-free process.
6. Re-run the probe after a CLI upgrade and re-validate the parser whenever the
   build's event types change. Re-run it as well when `config.yaml` or the relevant
   plugins change, and when working in another profile: the plugin list is not
   assumed to be the same there, so the resolved sets must be re-measured before
   the conclusions are reused.
