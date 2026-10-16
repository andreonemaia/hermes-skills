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

Do not generalise these numbers to another build, another provider, another model
or another launch surface (Desktop/TUI/ACP, `session.create`) without
re-measuring.

## What the runtime does with `-t/--toolsets`

The pick is validated at session launch in
`gateway/session_policy.py::_resolve_toolsets`, before any model call. Measured
result per pick (toolset count from the resolver; run outcome from the CLI):

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
3. **No measured flag combination leaves zero toolsets.** The smallest surface a
   run can be given here is `-t ""` (two plugin toolsets); adding `--safe-mode`
   drops it to `OpenPencil` alone, which is still not zero.
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
`tool_use` events.

Empirical run (one API call per case; the input-token count is a proxy for the
size of the tool surface):

```bash
hermes chat -Q -q "Responda apenas: ok" -m <model> --provider <provider> \
  --format stream-json <pick> > out.jsonl
grep -o '"input": [0-9]*' out.jsonl
```

## What this does NOT prove

- A count of zero `tool_use` events in a transcript is an observation about that
  run, not a capability statement: the toolsets in effect were still loaded.
- A separate scratch directory (`--in`) is scoping, not isolation.
- `HERMES_DUMP_REQUESTS=1` produced no new request dumps on this path (64 dumps
  before the probe batch, 64 after), so the toolset sets above come from the
  resolver plus the input-token deltas, not from an inspected wire payload.
- The numbers come from one build, one provider, one model and the CLI path.

## Conservative procedure

1. Pass `-t ""` (smallest measured surface).
2. Keep the brief's explicit prohibition ("Do NOT use tools. Output the review
   text only.").
3. After the run, count `tool_use` events in the stream, require `0`, and record
   the count.
4. Record which toolsets were in effect (rerun the probe above) and report them;
   never claim a tool-free process.
5. Re-run the probe after a CLI upgrade.
