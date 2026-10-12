#!/usr/bin/env python3
"""Discover a ComfyUI installation and its model directories.

Read-only. The script never starts, stops, installs or reconfigures anything:
it probes the HTTP API when one is running and falls back to inspecting
well-known install layouts plus any ``extra_model_paths`` config it can find.

Usage:
    python comfy_env.py [--base-url URL] [--root DIR] [--json] [--timeout S]

Exit codes: 0 discovered something (installation, config or model roots),
3 the API was requested but unreachable *and* nothing was discovered. A
reachable-API warning never changes the exit code on its own; read the
``warnings`` list in the JSON payload to tell the two cases apart.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (  # noqa: E402
    ApiError,
    free_bytes,
    http_get_json,
    human_bytes,
    model_roots_from_config,
    resolve_base_url,
)

# Directories that hold model-path configs, relative to a platform root.
CONFIG_GLOBS = (
    "Comfy Desktop/instance-model-paths/*.yaml",
    "ComfyUI/extra_model_paths.yaml",
    "comfy/extra_model_paths.yaml",
)

# Install directories worth probing, relative to a platform root.
INSTALL_GLOBS = (
    "Comfy-Desktop/ComfyUI-Installs/*/ComfyUI",
    "Comfy-Desktop/ComfyUI-Shared",
    "ComfyUI",
    "comfy/ComfyUI",
    "Documents/ComfyUI",
    "AppData/Local/Programs/@comfyorgcomfyui-electron/resources/ComfyUI",
)


def platform_roots(env=None):
    """Return candidate parent directories for the current platform."""
    env = os.environ if env is None else env
    roots = []
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME"):
        value = env.get(key)
        if value:
            roots.append(value)
    roots.append(os.path.expanduser("~"))
    seen = []
    for root in roots:
        if root and root not in seen:
            seen.append(root)
    return seen


def candidate_configs(roots):
    """Every model-path config file that exists under ``roots``."""
    found = []
    for root in roots:
        for pattern in CONFIG_GLOBS:
            for match in glob.glob(os.path.join(root, pattern)):
                if os.path.isfile(match):
                    found.append(os.path.normpath(match))
    return sorted(dict.fromkeys(found))


def candidate_installs(roots):
    """Every plausible ComfyUI directory that exists under ``roots``."""
    found = []
    for root in roots:
        for pattern in INSTALL_GLOBS:
            for match in glob.glob(os.path.join(root, pattern)):
                if os.path.isdir(match):
                    found.append(os.path.normpath(match))
    return sorted(dict.fromkeys(found))


def looks_like_install(directory):
    """True when ``directory`` plausibly is the root of a ComfyUI install."""
    if not directory or not os.path.isdir(directory):
        return False
    markers = ("main.py", "models", "python_embeded", "custom_nodes", "web")
    return any(os.path.exists(os.path.join(directory, marker)) for marker in markers)


def is_within(path, directory):
    """True when ``path`` lives inside ``directory`` (both fully resolved)."""
    try:
        resolved = os.path.realpath(path)
        root = os.path.realpath(directory)
        common = os.path.commonpath([resolved, root])
    except (OSError, ValueError):
        return False
    return os.path.normcase(common) == os.path.normcase(root)


def infer_install_dir(config_path, installs):
    """Best guess at the install directory a model-path config belongs to.

    ``extra_model_paths.yaml`` usually sits one level below the install, so the
    grandparent of the config is the install. That assumption breaks for some
    layouts (a Comfy Desktop ``instance-model-paths`` config sits two levels
    below a directory that is not an install at all), so a guess is only
    accepted when a discovered install contains the config or when the
    grandparent genuinely looks like an install. Returning ``None`` is safe:
    relative ``base_path`` values then resolve against the config's directory.
    """
    if not config_path:
        return None
    for install in installs:
        if is_within(config_path, install):
            return install
    candidate = os.path.dirname(os.path.dirname(os.path.realpath(config_path)))
    if looks_like_install(candidate):
        return candidate
    return None


def classify_install(directory):
    """Label an install directory as desktop, portable, manual or unknown."""
    if not directory:
        return "unknown"
    normalised = directory.replace("\\", "/").lower()
    if "comfy-desktop" in normalised or "comfyui-electron" in normalised:
        return "desktop"
    if os.path.isdir(os.path.join(directory, "python_embeded")):
        return "portable"
    if os.path.isfile(os.path.join(directory, "main.py")):
        return "manual"
    return "unknown"


def parse_argv_paths(argv):
    """Extract the path-bearing flags ComfyUI reports in ``/system_stats``.

    ``argv`` is the list the running server was launched with, so it is the
    most authoritative description of where the live instance keeps things.
    """
    flags = {
        "--extra-model-paths-config": "extra_model_paths_config",
        "--base-directory": "base_directory",
        "--output-directory": "output_directory",
        "--input-directory": "input_directory",
    }
    parsed = {}
    items = list(argv or [])
    for index, item in enumerate(items):
        if item in flags and index + 1 < len(items):
            parsed[flags[item]] = items[index + 1]
        else:
            for flag, key in flags.items():
                prefix = flag + "="
                if item.startswith(prefix):
                    parsed[key] = item[len(prefix) :]
    return parsed


def probe_api(base_url, timeout=10.0, opener=None):
    """Query ``/system_stats``; never raises, always reports reachability."""
    result = {"base_url": base_url, "reachable": False, "error": None, "stats": None}
    try:
        if opener is None:
            stats = http_get_json(base_url, "/system_stats", timeout=timeout)
        else:
            stats = http_get_json(base_url, "/system_stats", timeout=timeout, opener=opener)
    except ApiError as exc:
        result["error"] = str(exc)
        return result
    if not isinstance(stats, dict):
        result["error"] = "unexpected /system_stats payload"
        return result
    result["reachable"] = True
    result["stats"] = stats
    return result


def summarise_stats(stats):
    """Reduce a ``/system_stats`` payload to the fields worth reporting."""
    system = stats.get("system") or {}
    devices = []
    for device in stats.get("devices") or []:
        if not isinstance(device, dict):
            continue
        devices.append(
            {
                "name": device.get("name"),
                "type": device.get("type"),
                "vram_total": device.get("vram_total"),
                "vram_free": device.get("vram_free"),
            }
        )
    return {
        "comfyui_version": system.get("comfyui_version"),
        "os": system.get("os"),
        "python_version": system.get("python_version"),
        "pytorch_version": system.get("pytorch_version"),
        "deploy_environment": system.get("deploy_environment"),
        "ram_total": system.get("ram_total"),
        "ram_free": system.get("ram_free"),
        "devices": devices,
        "argv": system.get("argv") or [],
    }


def discover(base_url=None, timeout=10.0, opener=None, env=None, extra_roots=()):
    """Build a full picture of the ComfyUI environment.

    Combines three sources, in decreasing order of authority: the live API,
    the model-path config referenced by the running process, and the
    filesystem scan of well-known layouts.
    """
    env = os.environ if env is None else env
    base_url = resolve_base_url(base_url, env)
    warnings = []

    api = probe_api(base_url, timeout=timeout, opener=opener)
    summary = summarise_stats(api["stats"]) if api["reachable"] else None
    argv_paths = parse_argv_paths(summary["argv"]) if summary else {}

    roots = list(platform_roots(env))
    for extra in extra_roots:
        if extra:
            roots.insert(0, extra)

    configs = candidate_configs(roots)
    config_path = argv_paths.get("extra_model_paths_config")
    if config_path and os.path.isfile(config_path):
        configs.insert(0, os.path.normpath(config_path))
    elif config_path:
        warnings.append(f"running instance references a missing config: {config_path}")
    configs = list(dict.fromkeys(configs))

    installs = candidate_installs(roots)
    comfyui_dir = infer_install_dir(config_path, installs)
    if not comfyui_dir and installs:
        comfyui_dir = installs[0]
        if config_path and os.path.isfile(config_path):
            warnings.append(
                f"cannot tell which install {config_path} belongs to; using "
                f"{comfyui_dir} as the base directory for relative base_path values"
            )

    model_roots = []
    for config in configs:
        try:
            loaded = model_roots_from_config(config, base_dir=comfyui_dir)
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"cannot read {config}: {exc}")
            continue
        for root in loaded:
            root["config"] = config
            root["free_bytes"] = free_bytes(root["base_path"])
            model_roots.append(root)

    if not model_roots:
        for install in installs:
            models_dir = os.path.join(install, "models")
            if os.path.isdir(models_dir):
                model_roots.append(
                    {
                        "section": "default",
                        "base_path": os.path.normpath(models_dir),
                        "is_default": True,
                        "folders": {},
                        "config": None,
                        "free_bytes": free_bytes(models_dir),
                    }
                )
        if model_roots:
            warnings.append(
                "no extra_model_paths config found; falling back to <install>/models"
            )

    if not api["reachable"]:
        warnings.append(
            "the ComfyUI API is not reachable; version and device details are unavailable"
        )

    kind = classify_install(comfyui_dir)

    return {
        "api": {
            "base_url": base_url,
            "reachable": api["reachable"],
            "error": api["error"],
            "summary": summary,
        },
        "installation": {
            "kind": kind,
            "comfyui_dir": comfyui_dir,
            "extra_model_paths_config": config_path,
            "configs_found": configs,
            "output_directory": argv_paths.get("output_directory"),
            "input_directory": argv_paths.get("input_directory"),
        },
        "model_roots": model_roots,
        "warnings": warnings,
    }


def render(report):
    """Render the report as plain text for a terminal."""
    lines = []
    api = report["api"]
    lines.append("ComfyUI environment")
    lines.append(f"  API endpoint      : {api['base_url']}")
    lines.append(f"  API reachable     : {'yes' if api['reachable'] else 'no'}")
    if api["error"]:
        lines.append(f"  API error         : {api['error']}")
    summary = api.get("summary")
    if summary:
        lines.append(f"  ComfyUI version   : {summary['comfyui_version']}")
        lines.append(f"  Platform / Python : {summary['os']} / {summary['python_version']}")
        lines.append(f"  PyTorch           : {summary['pytorch_version']}")
        lines.append(f"  Deploy environment: {summary['deploy_environment']}")
        for device in summary["devices"]:
            lines.append(
                f"  Device            : {device['name']} "
                f"({human_bytes(device['vram_free'])} VRAM free "
                f"of {human_bytes(device['vram_total'])})"
            )
    installation = report["installation"]
    lines.append(f"  Install kind      : {installation['kind']}")
    lines.append(f"  ComfyUI directory : {installation['comfyui_dir'] or 'not located'}")
    lines.append(f"  Model-path config : {installation['extra_model_paths_config'] or 'none referenced'}")
    lines.append("")
    lines.append("Model roots")
    if not report["model_roots"]:
        lines.append("  (none found)")
    for root in report["model_roots"]:
        default = " [default]" if root["is_default"] else ""
        lines.append(f"  - {root['base_path']}{default}")
        lines.append(
            f"      section={root['section']} free={human_bytes(root['free_bytes'])}"
            f" config={root['config'] or 'inferred'}"
        )
        for folder in sorted(root["folders"]):
            for path in root["folders"][folder]:
                lines.append(f"      {folder}: {path}")
    for warning in report["warnings"]:
        lines.append("")
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Discover the active ComfyUI installation and its model directories."
    )
    parser.add_argument("--base-url", default=None, help="ComfyUI API base URL")
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="extra directory to scan for installs and configs (repeatable)",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="API timeout in seconds")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = discover(
        base_url=args.base_url, timeout=args.timeout, extra_roots=args.root
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    if not report["api"]["reachable"] and not report["model_roots"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
