---
name: comfyui-model-manager
description: "Manage ComfyUI model files and validate workflows before running them."
version: 0.1.0
author: Hermes Skills contributors
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [comfyui, models, safetensors, workflow, validation, disk, integrity]
    category: creative
---

# ComfyUI Model Manager

Discover a ComfyUI installation, inventory the model files on disk, verify a
manifest against them, and fetch missing models **only after verifying the
SHA-256 digest**. Also statically validate a workflow against a running server
before spending GPU time on a prompt that cannot load.

This skill complements the official `comfyui` skill, which covers *running*
workflows. This one covers the two things that break a run before it starts:
models that are missing, truncated, or duplicated, and graphs that reference
nodes or files that do not exist.

**Core principle: an unverifiable download is indistinguishable from a corrupt
one.** Nothing is moved into a model folder unless its digest matches, and no
file is overwritten without an explicit flag.

## When to use this skill

- A workflow fails with a missing-model or `value not in list` error.
- You need to know what is already installed before downloading tens of GB.
- You want to check free space and total size before a large transfer.
- You want to validate a workflow's graph and asset references without running it.
- You suspect duplicate checkpoints are wasting disk.

## Prerequisites

- Python 3.8+ from the ComfyUI environment. **Standard library only**; no
  `pip install` is required and none of the scripts import ComfyUI itself.
- For validation: a **running** ComfyUI server whose HTTP API answers
  `/object_info` and `/models`. `comfy_env.py` and `comfy_models.py` work
  offline; `comfy_validate.py` does not.
- Scripts live in `scripts/` inside this skill directory. Run them with the
  Python that belongs to the ComfyUI install, not a system Python you happen to
  have on `PATH`.

Set a shell variable once per session instead of repeating the path:

```bash
export CMM="<skill-dir>/scripts"          # bash
# $CMM = "<skill-dir>\scripts"            # PowerShell
```

## Step 1: discover the installation (read-only)

```bash
python "$CMM/comfy_env.py" --json
```

Reports the API reachability and version, the detected installation kind, the
model roots, and every folder under them. Add `--root DIR` (repeatable) to scan
extra locations, and `--base-url URL` to point at a remote instance.

Exit codes: `0` discovered something (installation, config or model roots),
`3` an API was requested but unreachable **and** nothing at all was discovered.
A reachable-API warning never changes the exit code on its own; read the
`warnings` list in the JSON payload to tell the two cases apart.

Read the output before trusting any path. Two situations are reported as
warnings rather than errors and both change what the later steps mean:

- A config referenced with `--extra-model-paths-config` that no longer exists
  on disk (common after moving a portable install).
- No config found at all, so the model directory was *guessed* from the install
  layout.

## Step 2: inventory what is already on disk

```bash
python "$CMM/comfy_models.py" inventory --models-dir "<models-dir>" --json
python "$CMM/comfy_models.py" inventory --models-dir "<models-dir>" --duplicates
python "$CMM/comfy_models.py" inventory --models-dir "<models-dir>" --folder checkpoints --hash
```

`--hash` reads every byte of every file: on a multi-hundred-GB model folder it
takes minutes. Use it when you need to prove a file's identity, not for a quick
listing. `--duplicates` groups files by size and, only with `--hash`, by digest.

## Step 3: write and verify a manifest

A manifest is JSON: either a bare array or an object with a `models` array.
Every entry requires `url`, `folder`, `filename` and `sha256`; `size` is
optional but strongly recommended, because it lets a partial download be
resumed instead of restarted.

```json
{
  "models": [
    {
      "url": "https://example.invalid/models/model.safetensors",
      "folder": "checkpoints",
      "filename": "model.safetensors",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "size": 12345678
    }
  ]
}
```

`sha256` must be a 64-character hex digest. A missing or malformed digest is a
hard error, not a warning: without it the download cannot be verified at all.
`folder` and `filename` are untrusted input and are proven to stay inside the
model directory; `..` or an absolute path is rejected before any file is
touched.

```bash
python "$CMM/comfy_models.py" verify --manifest manifest.json --models-dir "<models-dir>"
```

Per-entry status is one of `ok`, `missing`, `partial` (a resumable `.part`
sidecar exists) or `conflict` (a file is present but its size or digest
differs). Exit codes: `0` all clean, `4` bad manifest, `5` at least one entry
needs attention.

## Step 4: install (dry run first, always)

```bash
# dry run: prints the plan, downloads nothing
python "$CMM/comfy_models.py" install --manifest manifest.json --models-dir "<models-dir>"

# the same command with --yes actually transfers
python "$CMM/comfy_models.py" install --manifest manifest.json --models-dir "<models-dir>" --yes
```

Without `--yes` the command is a dry run even if you forgot the flag; the
download path requires explicit consent. `--allow-overwrite` is required to
replace a file whose digest differs, and is never implied.

Safety behaviour worth knowing before you run it:

- Free space is checked against the bytes still needed, and the transfer aborts
  rather than filling the volume. Only entries that declare a `size` can be
  counted, so the plan also prints how many entries were left out of that
  total (`bytes_unknown`) instead of presenting it as complete.
- The body streams into `<file>.part` and is renamed only after the digest
  matches, so an interrupted download never leaves a truncated file behind a
  valid name.
- A digest mismatch deletes the `.part` file; a size mismatch keeps it, so the
  next run resumes from where it stopped. If the server ignores the `Range`
  header and answers with the whole body, the transfer restarts from zero
  rather than appending to the wrong offset.
- A per-entry failure is reported in the result and does not abort the batch.

Exit codes: `0` nothing left to do (including every dry run), `4` bad manifest,
`5` at least one entry failed, `6` at least one entry was refused (an existing
file with a different digest and no `--allow-overwrite`). A run that both fails
and refuses exits `5`: the failure is the more severe of the two. A dry run
always exits `0`, since it changed nothing.

## Step 5: validate a workflow (needs a running server)

```bash
python "$CMM/comfy_validate.py" workflow.json --json
python "$CMM/comfy_validate.py" workflow.json --fail-on-warning
```

Both the API format and the UI (graph) format are accepted and detected
automatically.

Errors (the graph cannot load): unknown node class, link to a node or output
slot that does not exist, a required input with no value, a widget given a link
or a value of the wrong Python type.

Warnings (the graph loads, but something is off): a model filename the workflow
names that is not present in any configured folder, or an input the node does
not declare.

Exit codes: `0` clean, `1` the workflow file is missing, is not JSON, or is
neither format, `2` errors (or warnings with `--fail-on-warning`), `3` the
server was unreachable.

**A clean validation does not mean the inference result is correct.** It means
the graph is loadable and the files it names exist. Nothing here executes a
prompt, and no check reads image content.

## Pitfalls

- Running the scripts with a system Python instead of the ComfyUI one: harmless
  here (stdlib only), but it hides the fact that a different interpreter is
  being used for the server. Prefer the install's own Python.
- Trusting a discovered path without reading the warnings. A guessed model
  directory looks identical to a detected one in the JSON unless you check the
  warning list.
- Using `--hash` on a large folder casually. It is a full read of the volume.
- Assuming `verify` exits non-zero only on missing files. A `conflict` (wrong
  digest) is the more dangerous case and carries the same exit code `5`.
- Treating `--yes` as implied by a previous dry run. Every invocation needs it.
- Reading a dry run's exit `0` as proof the download would succeed: it opens no
  socket, so a dead URL only shows up on the real run.
- Missing the exit `6` case. A refused overwrite changes nothing on disk but
  still fails the run, so a batch that "looked fine" in the log did not install
  everything the manifest asked for.
- Expecting `comfy_validate.py` to work offline. It needs `/object_info`.

## Verification checklist

1. `python "$CMM/comfy_env.py" --json` exits `0` and the reported model root is
   the one you expect (not a guess).
2. `inventory --json` count and total size match what a file manager shows.
3. `verify --manifest <file>` reports `ok` for a file you know is complete, and
   `conflict` after you truncate a copy of it.
4. `install` without `--yes` downloads nothing: `git status`-style check that
   no new file and no `.part` sidecar appeared.
5. `install --yes` on a small manifest produces the file with the expected
   digest, and leaves no `.part` behind.
6. `comfy_validate.py` on a valid workflow exits `0`; on a workflow with a
   renamed node class exits `2` and names the class.
7. Re-run the test suite: `python -m unittest discover -s tests -t tests`.

## Tests

```bash
cd <skill-dir>
python -m unittest discover -s tests -t tests
```

The suite is standard-library `unittest`, uses no network and no live server,
and pins path discovery to a temporary directory so it never reads the host
machine's real ComfyUI configuration.
