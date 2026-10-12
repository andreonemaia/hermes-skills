# comfyui-model-manager

Find a ComfyUI installation, inventory the model files it already has, verify a
manifest against them, and fetch the missing ones **only after checking the
SHA-256 digest**. Also validate a workflow against a running server before
spending GPU time on a prompt that cannot load.

This skill complements the official `comfyui` skill, which covers *running*
workflows. This one covers the two failures that happen before a run starts:
models that are missing, truncated or duplicated, and graphs that reference
nodes or files that do not exist.

## The problem it solves

A ComfyUI model folder is a few hundred gigabytes of files downloaded from
various places over months, and a workflow is a JSON graph that silently
assumes all of them are present and correct. Both fail late and expensively:

- A download that was interrupted leaves a file with the right name and the
  wrong bytes. ComfyUI loads it far enough to fail with a confusing tensor
  error, or worse, produces garbage.
- A workflow is submitted, queued, and only then rejected because one node
  class was renamed or one checkpoint is in the wrong folder.
- Duplicate checkpoints accumulate because nothing ever compared digests.

The scripts here answer three questions without touching the GPU: *what is on
disk*, *is it what it claims to be*, and *will this graph load*.

## What it ships

| Script | Purpose | Needs a server |
|--------|---------|----------------|
| `scripts/comfy_env.py` | Discover the installation, the API version, and every model root and folder. | Optional (`--base-url` for a remote instance) |
| `scripts/comfy_models.py` | `inventory`, `verify`, `install` against a JSON manifest. | No |
| `scripts/comfy_validate.py` | Statically validate a workflow (API or UI format) against `/object_info`. | Yes |
| `scripts/_common.py` | Shared helpers: path safety, hashing, config parsing, HTTP. | - |

Standard library only. No `pip install`, and no import of ComfyUI itself.

## Design decisions

**A missing digest is a hard error.** An unverifiable download cannot be
distinguished from a corrupted one, so a manifest entry without a valid
64-character `sha256` is rejected rather than downloaded on trust.

**Downloads land in a `.part` sidecar and are renamed only after the digest
matches.** An interrupted transfer therefore never leaves a truncated file
behind a valid filename. A digest mismatch deletes the sidecar; a size mismatch
keeps it so the next run resumes.

**Destructive actions require an explicit flag.** Without `--yes`, `install` is
a dry run that prints the plan and transfers nothing. Replacing a file whose
digest differs additionally requires `--allow-overwrite`. Free space is checked
against the bytes still needed before the transfer starts.

**The manifest is untrusted input.** `folder` and `filename` are proven to stay
inside the model directory before anything touches the filesystem, so a
manifest containing `..` or an absolute path is rejected.

**Validation is static and says so.** A clean run means the graph is loadable
and the files it names exist. It does not mean the inference result is correct,
and nothing here executes a prompt.

## Workflow

```
comfy_env.py  --json          # where is ComfyUI, what folders exist
        │
        ▼
comfy_models.py inventory     # what is already on disk (--duplicates, --hash)
        │
        ▼
comfy_models.py verify        # manifest vs disk -> ok / missing / partial / conflict
        │
        ▼
comfy_models.py install       # dry run by default; --yes to transfer
        │
        ▼
comfy_validate.py workflow.json   # will this graph load?
```

## Usage examples

### 1. Find out what is installed

```
Use comfyui-model-manager to tell me what models are already installed and
which checkpoints are duplicated.
```

The agent runs `comfy_env.py --json`, then `comfy_models.py inventory
--duplicates`, and reports the folders, the total size, and the duplicate
groups.

### 2. Check a folder before a large download

```
Verify my manifest.json against the models directory and tell me how much
still has to be downloaded.
```

`verify` prints a per-entry status and the bytes needed; exit code `5` means at
least one entry needs attention.

### 3. Fetch only what is missing

```
Install the missing models from manifest.json.
```

The agent runs the dry run first and shows the plan. The transfer only happens
after the `--yes` form is run deliberately.

### 4. Validate a workflow before queueing it

```
Validate workflow.json against my running ComfyUI before I run it.
```

Reports unknown node classes, dangling links, missing required inputs, type
mismatches, and model filenames the graph names that are not in any configured
folder.

## Installation

Copy this directory into the `creative/` category folder of your Hermes skills
directory, so it sits next to the official `comfyui` skill:

```bash
# Linux / macOS
cp -r skills/comfyui-model-manager ~/.hermes/skills/creative/
```

```powershell
# Windows (PowerShell)
Copy-Item -Recurse skills\comfyui-model-manager "$env:LOCALAPPDATA\hermes\skills\creative\"
```

Then confirm with `hermes skills list`. See
[`../../docs/installation.md`](../../docs/installation.md).

## Tests

```bash
cd skills/comfyui-model-manager
python -m unittest discover -s tests -t tests
```

Standard-library `unittest`. No network, no live ComfyUI server, and path
discovery is pinned to a temporary directory so the suite never reads the host
machine's real configuration.

## Limitations

- **Windows-tested only.** `platforms: [windows]`. The scripts use
  `os.path` and `pathlib` and should work elsewhere, but that has not been run,
  so it is not claimed.
- **`--hash` reads every byte.** On a large model folder it takes minutes. It
  is for proving identity, not for a quick listing.
- **Validation needs a running server.** `comfy_env.py` and `comfy_models.py`
  work offline; `comfy_validate.py` cannot, because the node definitions come
  from `/object_info`.
- **Model availability is a warning, not an error.** A workflow naming a
  checkpoint that is not installed still loads; the skill reports it and lets
  you decide.
- **No download resumption across a digest change.** If the upstream file
  changes, the old `.part` is discarded on the next size check.
- **No ComfyUI Manager integration.** Custom-node installation is out of scope;
  this skill manages model files and validates graphs.

## License

MIT, like the rest of this repository.
