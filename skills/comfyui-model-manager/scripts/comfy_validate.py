#!/usr/bin/env python3
"""Statically validate a ComfyUI workflow against the running server.

Two different things get checked, and they are reported separately because
they carry different weight:

* **integrity of the graph** -- node classes that the API does not know about,
  links pointing at nodes or output slots that do not exist, required inputs
  with no value, widget values of the wrong Python type. These are errors.
* **availability of assets** -- model filenames the workflow names but that
  are not present in any configured folder. These are warnings, because a
  workflow can legitimately be validated before its models are installed.

Passing both checks means the graph is *loadable*. It does not mean inference
produces anything useful: only an actual execution can show that, and this
tool never runs one.

Usage:
    python comfy_validate.py WORKFLOW.json [--base-url URL] [--json]
                           [--fail-on-warning]

Exit codes: 0 clean, 1 the workflow file is missing, is not JSON, or is of an
unknown format, 2 validation errors (or warnings with --fail-on-warning), 3 the
API could not be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import ApiError, http_get_json, resolve_base_url  # noqa: E402

# Input types that must be fed by a link rather than a widget value.
LINK_TYPES = frozenset(
    {
        "MODEL",
        "CLIP",
        "VAE",
        "CONDITIONING",
        "LATENT",
        "IMAGE",
        "MASK",
        "AUDIO",
        "CONTROL_NET",
        "SIGMAS",
        "SAMPLER",
        "GUIDER",
        "NOISE",
        "STYLE_MODEL",
        "CLIP_VISION",
        "CLIP_VISION_OUTPUT",
        "UPSCALE_MODEL",
        "GLIGEN",
        "WEBCAM",
        "HOOKS",
        "PHOTOMAKER",
        "TRANSFORM",
        "DICT",
        "LAYOUT_IMAGE",
        "COMFY_MATRIX",
        "TIMESTEP_KEYFRAME",
    }
)

# Nodes the frontend handles without a backend class of the same name.
VIRTUAL_TYPES = frozenset({"Note", "MarkdownNote", "Reroute", "RerouteNode", "PrimitiveNode"})

# widget input name -> the model families it may resolve to.
MODEL_INPUT_FAMILIES = {
    "ckpt_name": ("checkpoints",),
    "checkpoint": ("checkpoints",),
    "vae_name": ("vae",),
    "lora_name": ("loras",),
    "unet_name": ("diffusion_models", "unet"),
    "clip_name": ("text_encoders", "clip"),
    "clip_name1": ("text_encoders", "clip"),
    "clip_name2": ("text_encoders", "clip"),
    "clip_name3": ("text_encoders", "clip"),
    "control_net_name": ("controlnet",),
    "style_model_name": ("style_models",),
    "clip_vision_name": ("clip_vision",),
    "ipadapter_file": ("ipadapter",),
}

SUPPORTED_SCALARS = {
    "INT": (int,),
    "FLOAT": (int, float),
    "STRING": (str,),
    "BOOLEAN": (bool,),
}


class Report:
    """Collect errors and warnings tagged with the node they came from."""

    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, message):
        self.errors.append({"where": where, "message": message})

    def warn(self, where, message):
        self.warnings.append({"where": where, "message": message})

    def as_dict(self):
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


def load_workflow(path):
    """Read a workflow JSON file and classify its serialisation format."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        raise SystemExit(f"workflow not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}")
    except OSError as exc:
        # A directory, an unreadable file or a broken link all land here. Left
        # uncaught they escaped ``main`` as a traceback; the exit code was the
        # documented 1 either way, but the message was not.
        raise SystemExit(f"{path}: cannot read workflow: {exc}")
    if not isinstance(document, dict):
        raise SystemExit(f"{path}: expected a JSON object at the top level")
    if isinstance(document.get("nodes"), list):
        return document, "ui"
    if document and all(isinstance(value, dict) and "class_type" in value for value in document.values()):
        return document, "api"
    raise SystemExit(
        f"{path}: unrecognised workflow format (neither API prompt nor UI graph)"
    )


def input_spec(object_info, class_type, name):
    """Return the spec dict for an input, or ``None`` when it is not declared."""
    definition = object_info.get(class_type) or {}
    inputs = definition.get("input") or {}
    for section in ("required", "optional"):
        spec = (inputs.get(section) or {}).get(name)
        if spec is not None:
            return spec
    return None


def is_link_spec(spec):
    """True when an input spec expects a link rather than a widget value."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    kind = spec[0]
    if isinstance(kind, str):
        return kind in LINK_TYPES
    if isinstance(kind, list):
        return False  # combo of literal choices
    if isinstance(kind, dict):
        inner = kind.get("type")
        if isinstance(inner, str):
            return inner in LINK_TYPES
        if isinstance(inner, list):
            return False
    return False


def combo_choices(spec):
    """Return the permitted values of a combo input, or ``None`` if not a combo."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    kind = spec[0]
    if isinstance(kind, list):
        return [str(item) for item in kind]
    if isinstance(kind, dict):
        inner = kind.get("type")
        if isinstance(inner, list):
            return [str(item) for item in inner]
    return None


def scalar_kind(spec):
    """Return the scalar type name an input accepts, or ``None``."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    kind = spec[0]
    if isinstance(kind, str) and kind in SUPPORTED_SCALARS:
        return kind
    if isinstance(kind, str) and kind == "COMBO":
        return "COMBO"
    return None


def widget_inputs(object_info, class_type):
    """Names of the required inputs that take a widget value, in API order."""
    definition = object_info.get(class_type) or {}
    required = (definition.get("input") or {}).get("required") or {}
    names = [name for name in required if not is_link_spec(required[name])]
    forced = (definition.get("input") or {}).get("required_order")
    if isinstance(forced, list):
        names = [name for name in forced if name in names]
    return names


def validate_api_workflow(workflow, object_info, model_index=None, report=None):
    """Validate a prompt-serialised graph. Returns the populated report."""
    report = report or Report()
    ids = set(workflow)

    for node_id, node in workflow.items():
        class_type = node.get("class_type")
        where = f"node {node_id}"
        if not isinstance(class_type, str):
            report.error(where, "missing 'class_type'")
            continue
        if class_type in VIRTUAL_TYPES:
            continue
        definition = object_info.get(class_type)
        if definition is None:
            report.error(where, f"unknown node class '{class_type}' (not exposed by /object_info)")
            continue

        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            report.error(where, f"'{class_type}' has no 'inputs' object")
            continue

        required = (definition.get("input") or {}).get("required") or {}

        for name, spec in required.items():
            if name not in inputs:
                report.error(where, f"'{class_type}' is missing required input '{name}'")

        for name, value in inputs.items():
            spec = input_spec(object_info, class_type, name)
            if spec is None:
                report.warn(where, f"'{class_type}' has input '{name}' which the server does not declare")
                continue
            if is_link_spec(spec):
                _check_link(value, ids, workflow, object_info, where, name, report)
                continue
            _check_widget(value, spec, where, name, class_type, model_index, report)

    return report


def _check_link(value, ids, workflow, object_info, where, name, report):
    """A link value must be ``[source_id, slot]`` naming a node in this graph."""
    if not isinstance(value, list) or len(value) != 2:
        report.error(where, f"input '{name}' expects a link [node_id, slot], got {value!r}")
        return
    source_id, slot = value
    source_key = str(source_id)
    if source_key not in ids:
        report.error(where, f"input '{name}' references node '{source_id}', which is not in the workflow")
        return
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        report.error(where, f"input '{name}' has an invalid output slot {slot!r}")
        return
    source = workflow[source_key]
    source_class = source.get("class_type")
    outputs = (object_info.get(source_class) or {}).get("output") or []
    if not outputs:
        # A node that produces nothing cannot feed an input. The virtual node
        # types are exempt: they are frontend-only and legitimately absent
        # from /object_info.
        if source_class not in VIRTUAL_TYPES:
            report.error(
                where,
                f"input '{name}' is fed by '{source_class}', which produces no outputs",
            )
        return
    if slot >= len(outputs):
        report.error(
            where,
            f"input '{name}' takes slot {slot} but '{source_class}' has "
            f"{len(outputs)} output(s)",
        )


def _check_widget(value, spec, where, name, class_type, model_index, report):
    """Widget values must match the declared scalar type, and named models must exist."""
    if isinstance(value, list):
        report.error(where, f"input '{name}' is a widget but was given a link {value!r}")
        return
    choices = combo_choices(spec)
    if choices is not None:
        if not isinstance(value, str):
            report.error(where, f"input '{name}' expects one of {choices[:5]}..., got {value!r}")
            return
        families = MODEL_INPUT_FAMILIES.get(name)
        if families and model_index is not None:
            known = set()
            for family in families:
                known.update(model_index.get(family) or ())
            if known and value not in known and value not in choices:
                report.warn(
                    where,
                    f"input '{name}' names '{value}', which is not present in "
                    f"{'/'.join(families)}",
                )
        return
    kind = scalar_kind(spec)
    if kind is None or kind == "COMBO":
        return
    allowed = SUPPORTED_SCALARS[kind]
    if isinstance(value, bool) and bool not in allowed:
        report.error(where, f"input '{name}' expects {kind}, got a boolean")
        return
    if not isinstance(value, allowed):
        report.error(where, f"input '{name}' expects {kind}, got {type(value).__name__} ({value!r})")


def validate_ui_workflow(workflow, object_info, report=None):
    """Validate a GUI-serialised graph: node classes, link endpoints and slots.

    Widget values are positional in this format, so this only warns when their
    count disagrees with the number of widget inputs the server declares.
    """
    report = report or Report()
    nodes = {}
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if node_id is None:
            report.error("graph", "a node has no 'id'")
            continue
        nodes[str(node_id)] = node
        class_type = node.get("type")
        if not isinstance(class_type, str):
            report.error(f"node {node_id}", "missing 'type'")
            continue
        if class_type in VIRTUAL_TYPES:
            continue
        if class_type not in object_info:
            report.error(f"node {node_id}", f"unknown node class '{class_type}' (not exposed by /object_info)")
            continue
        expected = widget_inputs(object_info, class_type)
        values = node.get("widgets_values")
        if isinstance(values, list) and expected:
            if len(values) != len(expected):
                report.warn(
                    f"node {node_id}",
                    f"'{class_type}' has {len(values)} widget value(s) but the server declares "
                    f"{len(expected)} ({', '.join(expected)})",
                )
        elif isinstance(values, dict) and expected:
            missing = [name for name in expected if name not in values]
            if missing:
                report.warn(
                    f"node {node_id}",
                    f"'{class_type}' did not provide widget value(s): {', '.join(missing)}",
                )

    for link in workflow.get("links") or []:
        if not isinstance(link, list) or len(link) < 6:
            report.warn("graph", f"malformed link entry: {link!r}")
            continue
        _link_id, origin_id, origin_slot, target_id, _target_slot, _link_type = link[:6]
        origin_key = str(origin_id)
        if origin_key not in nodes:
            report.error("graph", f"link into node {target_id} comes from missing node {origin_id}")
        if str(target_id) not in nodes:
            report.error("graph", f"link from node {origin_id} targets missing node {target_id}")
        # The endpoints are checked above; the slot is checked here. A link can
        # name two nodes that exist and still point at output 3 of a node that
        # only produces one, which is what ComfyUI reports as a load failure.
        origin_class = (nodes.get(origin_key) or {}).get("type")
        if not isinstance(origin_class, str) or origin_class in VIRTUAL_TYPES:
            continue
        outputs = (object_info.get(origin_class) or {}).get("output") or []
        if not outputs:
            continue
        if isinstance(origin_slot, bool) or not isinstance(origin_slot, int) or origin_slot < 0:
            report.error("graph", f"link from node {origin_id} has an invalid output slot {origin_slot!r}")
            continue
        if origin_slot >= len(outputs):
            report.error(
                "graph",
                f"link from node {origin_id} takes slot {origin_slot} but '{origin_class}' has "
                f"{len(outputs)} output(s)",
            )
    return report


def fetch_object_info(base_url, timeout=10.0, opener=None):
    """GET /object_info, raising a clean error when the server is not there."""
    if opener is None:
        return http_get_json(base_url, "/object_info", timeout=timeout)
    return http_get_json(base_url, "/object_info", timeout=timeout, opener=opener)


def build_model_index(base_url, timeout=10.0, opener=None, folders=None):
    """Filename index per model folder, taken from /models when reachable.

    Returns ``None`` when the API cannot be reached, which makes the caller
    skip the availability warnings instead of reporting false negatives.
    """
    kwargs = {} if opener is None else {"opener": opener}
    if folders is None:
        try:
            folders = http_get_json(base_url, "/models", timeout=timeout, **kwargs)
        except ApiError:
            return None
    if not isinstance(folders, list):
        return None
    index = {}
    for folder in folders:
        if not isinstance(folder, str):
            continue
        try:
            names = http_get_json(base_url, f"/models/{folder}", timeout=timeout, **kwargs)
        except ApiError:
            continue
        if isinstance(names, list):
            index[folder] = [name for name in names if isinstance(name, str)]
    return index


def build_parser():
    parser = argparse.ArgumentParser(
        description="Validate a ComfyUI workflow without running a prompt."
    )
    parser.add_argument("workflow", help="path to a workflow .json file")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    workflow, kind = load_workflow(args.workflow)
    base_url = resolve_base_url(args.base_url)
    try:
        object_info = fetch_object_info(base_url, timeout=args.timeout)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: start ComfyUI, or pass --base-url pointing at a running instance", file=sys.stderr)
        return 3
    if not isinstance(object_info, dict) or not object_info:
        print("error: /object_info returned no node definitions", file=sys.stderr)
        return 3

    model_index = build_model_index(base_url, timeout=args.timeout)
    if kind == "api":
        report = validate_api_workflow(workflow, object_info, model_index=model_index)
    else:
        report = validate_ui_workflow(workflow, object_info)
        if model_index is None:
            report.warn("graph", "model availability was not checked (the API did not answer /models)")

    payload = report.as_dict()
    payload["workflow"] = os.path.abspath(args.workflow)
    payload["format"] = kind
    payload["base_url"] = base_url
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"workflow : {payload['workflow']}")
        print(f"format   : {kind}")
        print(f"checked  : {len(object_info)} node classes from {base_url}/object_info")
        print("")
        for entry in payload["errors"]:
            print(f"  ERROR   {entry['where']}: {entry['message']}")
        for entry in payload["warnings"]:
            print(f"  WARNING {entry['where']}: {entry['message']}")
        if not payload["errors"] and not payload["warnings"]:
            print("  no problems found")
        print("")
        print(
            f"{payload['error_count']} error(s), {payload['warning_count']} warning(s). "
            "Loadable does not mean the inference result is correct."
        )

    if payload["error_count"]:
        return 2
    if args.fail_on_warning and payload["warning_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
