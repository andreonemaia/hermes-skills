#!/usr/bin/env python3
"""Automated tests for the comfyui-model-manager scripts.

Standard library only (``unittest``), no network access. Every HTTP call is
driven through a fake opener injected into the module under test, and every
filesystem test uses a temporary directory.

Run from the repository root:

    python -m unittest discover -s skills/comfyui-model-manager/tests -v
"""

from __future__ import annotations

import contextlib
import email.message
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402
import comfy_env  # noqa: E402
import comfy_models  # noqa: E402
import comfy_validate  # noqa: E402


def run_main(module, argv):
    """Run a script's ``main`` with stdout and stderr captured.

    The CLI prints a human-readable report; capturing it keeps the test output
    readable and lets a test assert on the text when it matters.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for the object ``urlopen`` returns."""

    def __init__(self, body, status=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = status
        self.code = status

    def read(self, size=-1):
        if size is None or size < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:size], self._body[size:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def json_opener(payload, status=200, calls=None):
    """An opener that always answers with ``payload`` serialised as JSON."""

    def opener(request, timeout=None):
        if calls is not None:
            calls.append(request.full_url)
        body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        return FakeResponse(body, status=status)

    return opener


def failing_opener(exc):
    """An opener that raises ``exc`` for every request."""

    def opener(request, timeout=None):
        raise exc

    return opener


def bytes_opener(data, record=None, range_support=True):
    """An opener that serves ``data`` and honours ``Range`` when asked."""

    def opener(request, timeout=None):
        start = 0
        header = request.headers.get("Range")
        if header and range_support:
            start = int(header.split("=", 1)[1].split("-", 1)[0])
        if record is not None:
            record.append({"url": request.full_url, "range": header})
        status = 206 if (header and range_support) else 200
        return FakeResponse(data[start:], status=status)

    return opener


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def isolate_discovery(case):
    """Keep discovery from ever reading the machine running the tests.

    ``comfy_env.discover`` reaches the host through two doors: the filesystem
    scan in ``platform_roots`` (which also expands ``~``) and the environment
    (``COMFYUI_BASE_URL``). Both are pinned to ``case.tmp`` here, so a test can
    never pass or fail because of a ComfyUI installation that happens to exist
    on the machine running it.

    The rest of the environment is neutralised as belt and braces: nothing in
    this suite should reach out, but a future test (or a library default) must
    not be able to route traffic through a proxy configured on the host, and
    the home-ish variables are pinned so that any ``expanduser`` cannot leak
    the real user profile into a test either. ``no_proxy`` is the variable
    ``proxy_bypass`` actually consults on Windows.
    """
    original_roots = comfy_env.platform_roots
    comfy_env.platform_roots = lambda env=None: [case.tmp]
    case.addCleanup(setattr, comfy_env, "platform_roots", original_roots)
    saved = os.environ.pop("COMFYUI_BASE_URL", None)
    if saved is not None:
        case.addCleanup(os.environ.__setitem__, "COMFYUI_BASE_URL", saved)
    for key in ("NO_PROXY", "no_proxy"):
        previous = os.environ.get(key)
        os.environ[key] = "127.0.0.1,localhost,::1"
        if previous is None:
            case.addCleanup(os.environ.pop, key, None)
        else:
            case.addCleanup(os.environ.__setitem__, key, previous)
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        previous = os.environ.get(key)
        os.environ[key] = case.tmp
        if previous is None:
            case.addCleanup(os.environ.pop, key, None)
        else:
            case.addCleanup(os.environ.__setitem__, key, previous)


def assert_paths_inside_tmp(case, report):
    """Fail loudly when a discovery report mentions anything outside the tmpdir."""
    root = os.path.abspath(case.tmp)
    seen = [entry["base_path"] for entry in report["model_roots"]]
    seen += list(report["installation"]["configs_found"])
    if report["installation"]["comfyui_dir"]:
        seen.append(report["installation"]["comfyui_dir"])
    for path in seen:
        case.assertTrue(path.startswith(root), f"host path leaked: {path}")


class TempDirMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="cmm-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def write(self, relative, content):
        target = self.path(relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with open(target, mode, **kwargs) as handle:
            handle.write(content)
        return target


# ---------------------------------------------------------------------------
# _common
# ---------------------------------------------------------------------------


class CommonUrlTests(unittest.TestCase):
    def test_default_when_nothing_supplied(self):
        self.assertEqual(
            _common.resolve_base_url(env={}), _common.DEFAULT_BASE_URL
        )

    def test_env_var_used(self):
        self.assertEqual(
            _common.resolve_base_url(env={"COMFYUI_BASE_URL": "http://box:8189"}),
            "http://box:8189",
        )

    def test_explicit_beats_env(self):
        self.assertEqual(
            _common.resolve_base_url(
                "http://explicit:1", env={"COMFYUI_BASE_URL": "http://box:8189"}
            ),
            "http://explicit:1",
        )

    def test_trailing_slash_stripped(self):
        self.assertEqual(
            _common.resolve_base_url("http://box:8188/", env={}), "http://box:8188"
        )


class CommonHttpTests(unittest.TestCase):
    def test_decodes_json(self):
        result = _common.http_get_json(
            "http://box:8188", "/x", opener=json_opener({"a": 1})
        )
        self.assertEqual(result, {"a": 1})

    def test_http_error_becomes_api_error(self):
        error = urllib.error.HTTPError("u", 500, "boom", email.message.Message(), None)
        self.addCleanup(error.close)
        opener = failing_opener(error)
        with self.assertRaises(_common.ApiError):
            _common.http_get_json("http://box:8188", "/x", opener=opener)

    def test_url_error_becomes_api_error(self):
        opener = failing_opener(urllib.error.URLError("refused"))
        with self.assertRaises(_common.ApiError):
            _common.http_get_json("http://box:8188", "/x", opener=opener)

    def test_os_error_becomes_api_error(self):
        opener = failing_opener(OSError("nope"))
        with self.assertRaises(_common.ApiError):
            _common.http_get_json("http://box:8188", "/x", opener=opener)

    def test_invalid_json_becomes_api_error(self):
        with self.assertRaises(_common.ApiError):
            _common.http_get_json("http://box:8188", "/x", opener=json_opener(b"<html>"))


class CommonFileTests(TempDirMixin, unittest.TestCase):
    def test_sha256_file_matches_hashlib(self):
        target = self.write("a.bin", b"payload")
        self.assertEqual(_common.sha256_file(target), sha256(b"payload"))

    def test_human_bytes_units(self):
        self.assertEqual(_common.human_bytes(0), "0 B")
        self.assertEqual(_common.human_bytes(1024), "1.00 KiB")
        self.assertEqual(_common.human_bytes(None), "unknown")

    def test_free_bytes_never_raises(self):
        # shutil.disk_usage answers for the containing volume on Windows and
        # raises on some platforms. The contract is only "an int or None,
        # never an exception".
        result = _common.free_bytes(self.path("no", "such", "place", "x"))
        self.assertTrue(result is None or isinstance(result, int))


class SafeChildPathTests(TempDirMixin, unittest.TestCase):
    def test_accepts_nested_relative(self):
        result = _common.safe_child_path(self.tmp, os.path.join("checkpoints", "a.safetensors"))
        self.assertTrue(result.startswith(os.path.abspath(self.tmp)))

    def test_rejects_empty(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, "   ")

    def test_rejects_absolute(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, os.path.abspath(self.tmp))

    def test_rejects_drive_letter(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, "C:file")

    def test_rejects_parent_escape(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, os.path.join("..", "outside"))

    def test_rejects_leading_separator(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, "\\windows\\system32")

    def test_non_string_rejected(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, None)

    def test_rejects_the_root_itself(self):
        # "." and "checkpoints/.." both normalise back to the root. A file at
        # the root is not a child of the root, and letting one through would
        # write the ``.part`` sidecar beside the model directory.
        for relative in (".", os.path.join("checkpoints", "..")):
            with self.assertRaises(_common.ComfyModelManagerError):
                _common.safe_child_path(self.tmp, relative)

    def test_rejects_control_characters(self):
        # A NUL byte reaches ``open`` as a ``ValueError`` rather than an
        # ``OSError``, so it must be refused here instead of escaping the CLI
        # as a traceback with the wrong exit code.
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, "checkpoints/a\x00b.bin")

    def test_rejects_symlink_that_escapes_root(self):
        # A symlink *inside* the root that points outside it must not become a
        # write target. Both sides are ``realpath``-resolved before the
        # containment test, so the escape is caught here rather than letting
        # the write land in the link's target.
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        link = os.path.join(self.tmp, "escape")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted on this platform")
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(self.tmp, os.path.join("escape", "x.bin"))


YAML_SAMPLE = """\
# a comment
comfyui:
    base_path: /srv/models
    is_default: true
    checkpoints: |
        checkpoints
        big/checkpoints
    loras: loras

'shared':
    is_default: no
    vae: "vae dir"
"""


class YamlParsingTests(unittest.TestCase):
    def setUp(self):
        self.sections = _common.parse_model_paths_yaml(YAML_SAMPLE)

    def test_section_names(self):
        self.assertEqual(set(self.sections), {"comfyui", "shared"})

    def test_base_path(self):
        self.assertEqual(self.sections["comfyui"]["base_path"], "/srv/models")

    def test_is_default_true(self):
        self.assertTrue(self.sections["comfyui"]["is_default"])

    def test_is_default_false(self):
        self.assertFalse(self.sections["shared"]["is_default"])

    def test_block_scalar(self):
        self.assertEqual(
            self.sections["comfyui"]["folders"]["checkpoints"],
            ["checkpoints", "big/checkpoints"],
        )

    def test_inline_and_quoted_values(self):
        self.assertEqual(self.sections["comfyui"]["folders"]["loras"], ["loras"])
        self.assertEqual(self.sections["shared"]["folders"]["vae"], ["vae dir"])

    def test_empty_text(self):
        self.assertEqual(_common.parse_model_paths_yaml(""), {})


class ConfigResolutionTests(TempDirMixin, unittest.TestCase):
    def test_relative_subpath_resolved_against_base(self):
        resolved = _common.resolve_config_paths("/base", ["loras"])
        self.assertEqual(resolved, [os.path.normpath(os.path.join("/base", "loras"))])

    def test_absolute_subpath_kept(self):
        absolute = os.path.abspath(self.tmp)
        self.assertEqual(_common.resolve_config_paths("/base", [absolute]), [absolute])

    def test_model_roots_from_config(self):
        config = self.write("extra_model_paths.yaml", YAML_SAMPLE)
        roots = _common.model_roots_from_config(config, base_dir=self.tmp)
        names = {root["section"] for root in roots}
        self.assertEqual(names, {"comfyui", "shared"})
        comfy = next(root for root in roots if root["section"] == "comfyui")
        # base_path is absolute in the sample, so it is kept as-is (made
        # absolute against the current volume on Windows).
        self.assertTrue(os.path.isabs(comfy["base_path"]))
        self.assertTrue(comfy["base_path"].endswith(os.path.join("srv", "models")))

    def test_relative_base_path_resolved_against_base_dir(self):
        config = self.write(
            "extra_model_paths.yaml", "local:\n    base_path: models\n    loras: loras\n"
        )
        roots = _common.model_roots_from_config(config, base_dir=self.tmp)
        self.assertEqual(
            roots[0]["base_path"], os.path.normpath(os.path.join(self.tmp, "models"))
        )


class ManifestTests(TempDirMixin, unittest.TestCase):
    def entry(self, **overrides):
        base = {
            "url": "https://example.invalid/a.safetensors",
            "folder": "checkpoints",
            "filename": "a.safetensors",
            "sha256": "a" * 64,
        }
        base.update(overrides)
        return base

    def test_is_sha256(self):
        self.assertTrue(_common.is_sha256("A" * 64))
        self.assertFalse(_common.is_sha256("a" * 63))
        self.assertFalse(_common.is_sha256("g" * 64))
        self.assertFalse(_common.is_sha256(None))

    def test_bare_array_manifest(self):
        path = self.write("m.json", json.dumps([self.entry()]))
        entries = _common.load_manifest(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "a.safetensors")

    def test_object_manifest(self):
        path = self.write("m.json", json.dumps({"models": [self.entry(name="nice")]}))
        self.assertEqual(_common.load_manifest(path)[0]["name"], "nice")

    def test_missing_file(self):
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(self.path("absent.json"))

    def test_invalid_json(self):
        path = self.write("m.json", "{not json")
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(path)

    def test_empty_array(self):
        path = self.write("m.json", "[]")
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(path)

    def test_missing_required_field(self):
        path = self.write("m.json", json.dumps([self.entry(url="")]))
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(path)

    def test_bad_digest(self):
        path = self.write("m.json", json.dumps([self.entry(sha256="zz")]))
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(path)

    def test_negative_size(self):
        path = self.write("m.json", json.dumps([self.entry(size=-1)]))
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(path)

    def test_escaping_filename_rejected(self):
        path = self.write(
            "m.json", json.dumps([self.entry(folder="..", filename="escape.bin")])
        )
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.load_manifest(path)

    def test_digest_lowercased(self):
        path = self.write("m.json", json.dumps([self.entry(sha256="A" * 64)]))
        self.assertEqual(_common.load_manifest(path)[0]["sha256"], "a" * 64)


# ---------------------------------------------------------------------------
# comfy_env
# ---------------------------------------------------------------------------


class EnvDiscoveryTests(TempDirMixin, unittest.TestCase):
    def test_platform_roots_deduplicated(self):
        env = {"LOCALAPPDATA": r"C:\AppData", "APPDATA": r"C:\AppData", "HOME": ""}
        roots = comfy_env.platform_roots(env)
        self.assertEqual(roots.count(r"C:\AppData"), 1)

    def test_parse_argv_paths_separate_and_equals(self):
        argv = [
            "--extra-model-paths-config",
            r"C:\cfg\extra.yaml",
            "--output-directory=out",
            "--unrelated",
        ]
        parsed = comfy_env.parse_argv_paths(argv)
        self.assertEqual(parsed["extra_model_paths_config"], r"C:\cfg\extra.yaml")
        self.assertEqual(parsed["output_directory"], "out")

    def test_parse_argv_paths_ignores_missing_value(self):
        self.assertEqual(comfy_env.parse_argv_paths(["--base-directory"]), {})

    def test_probe_api_unreachable(self):
        result = comfy_env.probe_api(
            "http://box:8188", opener=failing_opener(OSError("refused"))
        )
        self.assertFalse(result["reachable"])
        self.assertIsNotNone(result["error"])

    def test_probe_api_reachable(self):
        stats = {"system": {"comfyui_version": "1.2.3"}, "devices": []}
        result = comfy_env.probe_api("http://box:8188", opener=json_opener(stats))
        self.assertTrue(result["reachable"])
        self.assertEqual(result["stats"]["system"]["comfyui_version"], "1.2.3")

    def test_probe_api_unexpected_payload(self):
        result = comfy_env.probe_api("http://box:8188", opener=json_opener(["nope"]))
        self.assertFalse(result["reachable"])

    def test_summarise_stats(self):
        stats = {
            "system": {
                "comfyui_version": "1.2.3",
                "os": "nt",
                "python_version": "3.12.0",
                "pytorch_version": "2.4.0",
                "deploy_environment": {"type": "desktop"},
                "ram_total": 100,
                "ram_free": 50,
                "argv": ["--port", "8188"],
            },
            "devices": [
                {"name": "GPU", "type": "cuda", "vram_total": 1024, "vram_free": 512},
                "not a dict",
            ],
        }
        summary = comfy_env.summarise_stats(stats)
        self.assertEqual(summary["comfyui_version"], "1.2.3")
        self.assertEqual(len(summary["devices"]), 1)
        self.assertEqual(summary["argv"], ["--port", "8188"])

    def test_classify_install_manual(self):
        install = os.path.join(self.tmp, "ComfyUI")
        os.makedirs(install)
        self.write("ComfyUI/main.py", "# entry point")
        self.assertEqual(comfy_env.classify_install(install), "manual")

    def test_classify_install_desktop(self):
        self.assertEqual(
            comfy_env.classify_install(r"C:\Comfy-Desktop\ComfyUI-Installs\1\ComfyUI"),
            "desktop",
        )

    def test_classify_install_portable(self):
        install = os.path.join(self.tmp, "portable")
        os.makedirs(os.path.join(install, "python_embeded"))
        self.assertEqual(comfy_env.classify_install(install), "portable")

    def test_classify_install_unknown(self):
        self.assertEqual(comfy_env.classify_install(os.path.join(self.tmp, "x")), "unknown")
        self.assertEqual(comfy_env.classify_install(None), "unknown")

    def test_discover_uses_config_and_reports_unreachable_api(self):
        isolate_discovery(self)
        self.write(
            "ComfyUI/extra_model_paths.yaml",
            "comfyui:\n    base_path: models\n    checkpoints: checkpoints\n",
        )
        report = comfy_env.discover(
            opener=failing_opener(OSError("refused")), extra_roots=[self.tmp]
        )
        self.assertFalse(report["api"]["reachable"])
        self.assertTrue(any("not reachable" in warning for warning in report["warnings"]))
        self.assertEqual(len(report["model_roots"]), 1)
        root = report["model_roots"][0]
        self.assertEqual(root["section"], "comfyui")
        self.assertTrue(root["config"].endswith("extra_model_paths.yaml"))
        # base_path is resolved against the ComfyUI install directory, which
        # here is <tmp>/ComfyUI, so the model root points below the temp dir.
        self.assertTrue(root["base_path"].startswith(os.path.abspath(self.tmp)))

    def test_discover_warns_when_referenced_config_missing(self):
        isolate_discovery(self)
        stats = {"system": {"argv": ["--extra-model-paths-config", self.path("ghost.yaml")]}}
        report = comfy_env.discover(
            opener=json_opener(stats), extra_roots=[self.tmp]
        )
        self.assertTrue(any("missing config" in warning for warning in report["warnings"]))

    def test_discover_falls_back_to_install_models_dir(self):
        isolate_discovery(self)
        models = os.path.join(self.tmp, "ComfyUI", "models")
        os.makedirs(models)
        self.write("ComfyUI/main.py", "# entry point")
        report = comfy_env.discover(
            opener=failing_opener(OSError("refused")), extra_roots=[self.tmp]
        )
        self.assertTrue(any("falling back" in warning for warning in report["warnings"]))
        self.assertEqual(report["installation"]["kind"], "manual")

    def test_render_contains_key_lines(self):
        report = {
            "api": {"base_url": "http://box:8188", "reachable": False, "error": "nope", "summary": None},
            "installation": {
                "kind": "manual",
                "comfyui_dir": None,
                "extra_model_paths_config": None,
                "configs_found": [],
                "output_directory": None,
                "input_directory": None,
            },
            "model_roots": [],
            "warnings": ["something"],
        }
        text = comfy_env.render(report)
        self.assertIn("http://box:8188", text)
        self.assertIn("(none found)", text)
        self.assertIn("warning: something", text)


# ---------------------------------------------------------------------------
# comfy_models
# ---------------------------------------------------------------------------


class InventoryTests(TempDirMixin, unittest.TestCase):
    def test_scan_folder_skips_part_and_dotfiles(self):
        self.write(os.path.join("checkpoints", "good.safetensors"), b"x")
        self.write(os.path.join("checkpoints", "part.safetensors.part"), b"y")
        self.write(os.path.join("checkpoints", ".hidden"), b"z")
        entries = comfy_models.scan_folder(self.tmp, "checkpoints")
        self.assertEqual([entry["name"] for entry in entries], ["good.safetensors"])

    def test_inventory_reads_subdirectories(self):
        self.write(os.path.join("checkpoints", "sub", "deep.bin"), b"abc")
        roots = [{"base_path": self.tmp, "folders": {"checkpoints": [os.path.join(self.tmp, "checkpoints")]}}]
        entries = comfy_models.inventory(roots)
        self.assertEqual([entry["name"] for entry in entries], ["deep.bin"])
        self.assertEqual(entries[0]["root"], self.tmp)

    def test_inventory_infers_folders_from_listing(self):
        self.write(os.path.join("loras", "a.bin"), b"abc")
        entries = comfy_models.inventory([{"base_path": self.tmp, "folders": {}}])
        self.assertEqual([entry["folder"] for entry in entries], ["loras"])

    def test_inventory_with_hash(self):
        self.write(os.path.join("loras", "a.bin"), b"abc")
        entries = comfy_models.inventory([{"base_path": self.tmp, "folders": {}}], with_hash=True)
        self.assertEqual(entries[0]["sha256"], sha256(b"abc"))

    def test_inventory_folder_filter(self):
        self.write(os.path.join("loras", "a.bin"), b"abc")
        self.write(os.path.join("vae", "b.bin"), b"abc")
        entries = comfy_models.inventory([{"base_path": self.tmp, "folders": {}}], folders={"vae"})
        self.assertEqual([entry["folder"] for entry in entries], ["vae"])

    def test_find_duplicates_by_digest(self):
        entries = [
            {"folder": "a", "name": "one.bin", "size": 3, "path": "p1", "sha256": "d" * 64},
            {"folder": "b", "name": "two.bin", "size": 3, "path": "p2", "sha256": "d" * 64},
        ]
        groups = comfy_models.find_duplicates(entries)
        self.assertEqual(groups[0]["reason"], "identical-sha256")

    def test_find_duplicates_by_name_and_size(self):
        entries = [
            {"folder": "a", "name": "same.bin", "size": 3, "path": "p1"},
            {"folder": "a", "name": "same.bin", "size": 3, "path": "p2"},
        ]
        groups = comfy_models.find_duplicates(entries)
        self.assertEqual(groups[0]["reason"], "same-name-and-size")

    def test_find_duplicates_none(self):
        entries = [{"folder": "a", "name": "one.bin", "size": 3, "path": "p1"}]
        self.assertEqual(comfy_models.find_duplicates(entries), [])


class ClassificationTests(TempDirMixin, unittest.TestCase):
    def entry(self, **overrides):
        base = {
            "folder": "checkpoints",
            "filename": "a.bin",
            "url": "https://example.invalid/a.bin",
            "sha256": sha256(b"payload"),
            "size": 7,
            "name": "a.bin",
        }
        base.update(overrides)
        return base

    def test_missing(self):
        entry = self.entry()
        dest = comfy_models.entry_destination(self.tmp, entry)
        self.assertEqual(comfy_models.classify_destination(dest, entry)["status"], "missing")

    def test_ok(self):
        entry = self.entry()
        dest = self.write(entry["filename"], b"payload")
        self.assertEqual(comfy_models.classify_destination(dest, entry)["status"], "ok")

    def test_conflict_on_digest(self):
        entry = self.entry()
        dest = self.write(entry["filename"], b"different")
        self.assertEqual(comfy_models.classify_destination(dest, entry)["status"], "conflict")

    def test_conflict_on_size(self):
        entry = self.entry(size=999)
        dest = self.write(entry["filename"], b"payload")
        self.assertEqual(comfy_models.classify_destination(dest, entry)["status"], "conflict")

    def test_partial(self):
        entry = self.entry()
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as handle:
            handle.write(b"pay")
        outcome = comfy_models.classify_destination(dest, entry)
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["part_size"], 3)

    def test_plan_sums_remaining_bytes(self):
        entry = self.entry(size=7)
        rows, needed = comfy_models.plan_manifest([entry], self.tmp)
        self.assertEqual(needed, 7)

    def test_plan_accounts_for_partial(self):
        entry = self.entry(size=7)
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as handle:
            handle.write(b"pay")
        _rows, needed = comfy_models.plan_manifest([entry], self.tmp)
        self.assertEqual(needed, 4)

    def test_plan_skips_ok(self):
        entry = self.entry()
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(b"payload")
        rows, needed = comfy_models.plan_manifest([entry], self.tmp)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(needed, 0)


class DownloadTests(TempDirMixin, unittest.TestCase):
    def entry(self, data=b"payload"):
        return {
            "folder": "checkpoints",
            "filename": "a.bin",
            "url": "https://example.invalid/a.bin",
            "sha256": sha256(data),
            "size": len(data),
            "name": "a.bin",
        }

    def test_successful_download(self):
        data = b"payload"
        entry = self.entry(data)
        dest = comfy_models.entry_destination(self.tmp, entry)
        result = comfy_models.download_file(
            entry["url"], dest, entry["sha256"], expected_size=entry["size"],
            opener=bytes_opener(data),
        )
        self.assertEqual(result["sha256"], sha256(data))
        self.assertTrue(os.path.isfile(dest))
        self.assertFalse(os.path.isfile(dest + ".part"))

    def test_digest_mismatch_discards_partial(self):
        entry = self.entry(b"payload")
        dest = comfy_models.entry_destination(self.tmp, entry)
        with self.assertRaises(_common.IntegrityError):
            comfy_models.download_file(
                entry["url"], dest, "b" * 64, expected_size=len(b"payload"),
                opener=bytes_opener(b"payload"),
            )
        self.assertFalse(os.path.isfile(dest))
        self.assertFalse(os.path.isfile(dest + ".part"))

    def test_size_mismatch_keeps_partial(self):
        entry = self.entry(b"payload")
        dest = comfy_models.entry_destination(self.tmp, entry)
        with self.assertRaises(_common.IntegrityError):
            comfy_models.download_file(
                entry["url"], dest, entry["sha256"], expected_size=999,
                opener=bytes_opener(b"payload"),
            )
        self.assertTrue(os.path.isfile(dest + ".part"))

    def test_resume_sends_range_header(self):
        data = b"payload"
        entry = self.entry(data)
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as handle:
            handle.write(data[:3])
        record = []
        result = comfy_models.download_file(
            entry["url"], dest, entry["sha256"], expected_size=len(data),
            opener=bytes_opener(data, record=record),
        )
        self.assertEqual(result["resumed_from"], 3)
        self.assertEqual(record[0]["range"], "bytes=3-")
        self.assertTrue(os.path.isfile(dest))

    def test_server_ignoring_range_rewrites_from_zero(self):
        data = b"payload"
        entry = self.entry(data)
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as handle:
            handle.write(b"xxx")
        comfy_models.download_file(
            entry["url"], dest, entry["sha256"], expected_size=len(data),
            opener=bytes_opener(data, range_support=False),
        )
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), data)

    def test_partial_larger_than_expected_restarts(self):
        data = b"payload"
        entry = self.entry(data)
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as handle:
            handle.write(b"x" * 50)
        record = []
        result = comfy_models.download_file(
            entry["url"], dest, entry["sha256"], expected_size=len(data),
            opener=bytes_opener(data, record=record),
        )
        self.assertEqual(result["resumed_from"], 0)
        self.assertNotIn("Range", {entry["range"] for entry in record if entry["range"]})

    def test_http_error_becomes_integrity_error(self):
        entry = self.entry(b"payload")
        dest = comfy_models.entry_destination(self.tmp, entry)
        error = urllib.error.HTTPError("u", 404, "gone", email.message.Message(), None)
        # HTTPError is a file-like wrapper; closing it keeps the suite free of
        # ResourceWarning noise from the garbage collector.
        self.addCleanup(error.close)
        opener = failing_opener(error)
        with self.assertRaises(_common.IntegrityError):
            comfy_models.download_file(entry["url"], dest, entry["sha256"], opener=opener)

    def test_416_on_resume_restarts(self):
        data = b"payload"
        entry = self.entry(data)
        dest = comfy_models.entry_destination(self.tmp, entry)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + ".part", "wb") as handle:
            handle.write(data[:3])

        seen = []

        def opener(request, timeout=None):
            seen.append(request.headers.get("Range"))
            if request.headers.get("Range"):
                # The server refuses the byte range even though the partial
                # file is shorter than the payload.
                error = urllib.error.HTTPError(
                    request.full_url, 416, "range", email.message.Message(), None
                )
                self.addCleanup(error.close)
                raise error
            return FakeResponse(data, status=200)

        result = comfy_models.download_file(
            entry["url"], dest, entry["sha256"], expected_size=len(data), opener=opener
        )
        self.assertEqual(seen, ["bytes=3-", None])
        self.assertEqual(result["resumed_from"], 0)
        self.assertEqual(result["sha256"], sha256(data))
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), data)


class InstallTests(TempDirMixin, unittest.TestCase):
    def entry(self, data=b"payload", **overrides):
        base = {
            "folder": "checkpoints",
            "filename": "a.bin",
            "url": "https://example.invalid/a.bin",
            "sha256": sha256(data),
            "size": len(data),
            "name": "a.bin",
        }
        base.update(overrides)
        return base

    def test_dry_run_never_downloads(self):
        record = []
        payload = comfy_models.install_manifest(
            [self.entry()], self.tmp, dry_run=True, confirmed=False,
            opener=bytes_opener(b"payload", record=record),
        )
        self.assertTrue(payload["dry_run"])
        self.assertEqual(record, [])
        self.assertFalse(os.path.isfile(comfy_models.entry_destination(self.tmp, self.entry())))

    def test_unconfirmed_is_dry_run_even_when_not_flagged(self):
        payload = comfy_models.install_manifest(
            [self.entry()], self.tmp, dry_run=False, confirmed=False,
            opener=bytes_opener(b"payload"),
        )
        self.assertTrue(payload["dry_run"])

    def test_confirmed_installs(self):
        entry = self.entry()
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, opener=bytes_opener(b"payload")
        )
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["results"][0]["action"], "installed")
        self.assertTrue(os.path.isfile(comfy_models.entry_destination(self.tmp, entry)))

    def place(self, entry, data):
        """Write ``data`` where the entry is expected to live on disk."""
        return self.write(os.path.join(entry["folder"], entry["filename"]), data)

    def test_existing_match_is_skipped(self):
        entry = self.entry()
        self.place(entry, b"payload")
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, opener=bytes_opener(b"payload")
        )
        self.assertEqual(payload["results"][0]["action"], "skipped")

    def test_conflict_refused_without_allow_overwrite(self):
        entry = self.entry()
        self.place(entry, b"different")
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, opener=bytes_opener(b"payload")
        )
        self.assertEqual(payload["results"][0]["action"], "refused")

    def test_conflict_allowed_with_flag(self):
        entry = self.entry()
        self.place(entry, b"different")
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, allow_overwrite=True,
            opener=bytes_opener(b"payload"),
        )
        self.assertEqual(payload["results"][0]["action"], "installed")

    def test_failure_is_reported_not_raised(self):
        entry = self.entry()
        opener = failing_opener(urllib.error.URLError("refused"))
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, opener=opener
        )
        self.assertEqual(payload["results"][0]["action"], "failed")

    def test_insufficient_space(self):
        # install measures the volumes the files land on, not the root, so both
        # helpers have to report zero for the guard to fire.
        entry = self.entry()
        original_free = comfy_models.free_bytes
        original_min = comfy_models.min_free_bytes
        comfy_models.free_bytes = lambda path: 0
        comfy_models.min_free_bytes = lambda paths: 0
        try:
            with self.assertRaises(_common.IntegrityError):
                comfy_models.install_manifest(
                    [entry], self.tmp, dry_run=False, confirmed=True,
                    opener=bytes_opener(b"payload"),
                )
        finally:
            comfy_models.free_bytes = original_free
            comfy_models.min_free_bytes = original_min

    def test_entry_destination_rejects_escape(self):
        with self.assertRaises(_common.ComfyModelManagerError):
            comfy_models.entry_destination(self.tmp, {"folder": "..", "filename": "x"})

    def test_directory_conflict_remedy_does_not_offer_allow_overwrite(self):
        # --allow-overwrite replaces a *file*; it cannot remove the directory
        # occupying the destination, so suggesting it points at a fix that can
        # never succeed. The refusal has to name the directory instead.
        entry = self.entry()
        os.makedirs(comfy_models.entry_destination(self.tmp, entry))
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, opener=bytes_opener(b"payload")
        )
        result = payload["results"][0]
        self.assertEqual(result["action"], "refused")
        self.assertIn("remove the directory", result["detail"])
        self.assertNotIn("--allow-overwrite", result["detail"])

    def test_file_conflict_remedy_offers_allow_overwrite(self):
        entry = self.entry()
        self.place(entry, b"different")
        payload = comfy_models.install_manifest(
            [entry], self.tmp, dry_run=False, confirmed=True, opener=bytes_opener(b"payload")
        )
        result = payload["results"][0]
        self.assertEqual(result["action"], "refused")
        self.assertIn("--allow-overwrite", result["detail"])


# ---------------------------------------------------------------------------
# comfy_validate
# ---------------------------------------------------------------------------


OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"]]}},
        "output": ["MODEL", "CLIP", "VAE"],
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "seed": ["INT"],
                "steps": ["INT"],
                "cfg": ["FLOAT"],
                "sampler_name": [["euler"]],
                "scheduler": [["normal"]],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "denoise": ["FLOAT"],
            }
        },
        "output": ["LATENT"],
    },
    "EmptyLatentImage": {
        "input": {"required": {"width": ["INT"], "height": ["INT"], "batch_size": ["INT"]}},
        "output": ["LATENT"],
    },
}


def ksampler_inputs(**overrides):
    inputs = {
        "model": ["1", 0],
        "seed": 1,
        "steps": 20,
        "cfg": 7.0,
        "sampler_name": "euler",
        "scheduler": "normal",
        "positive": ["2", 0],
        "negative": ["3", 0],
        "latent_image": ["4", 0],
        "denoise": 1.0,
    }
    inputs.update(overrides)
    return inputs


VALID_API_WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}},
    "2": {"class_type": "CLIPTextEncodeStub", "inputs": {}},
    "3": {"class_type": "KSampler", "inputs": ksampler_inputs()},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
}


class LoadWorkflowTests(TempDirMixin, unittest.TestCase):
    def test_api_format(self):
        path = self.write("api.json", json.dumps(VALID_API_WORKFLOW))
        _doc, kind = comfy_validate.load_workflow(path)
        self.assertEqual(kind, "api")

    def test_ui_format(self):
        path = self.write("ui.json", json.dumps({"nodes": [], "links": []}))
        _doc, kind = comfy_validate.load_workflow(path)
        self.assertEqual(kind, "ui")

    def test_unrecognised_format(self):
        path = self.write("x.json", json.dumps({"hello": "world"}))
        with self.assertRaises(SystemExit):
            comfy_validate.load_workflow(path)

    def test_missing_file(self):
        with self.assertRaises(SystemExit):
            comfy_validate.load_workflow(self.path("absent.json"))

    def test_invalid_json(self):
        path = self.write("x.json", "{oops")
        with self.assertRaises(SystemExit):
            comfy_validate.load_workflow(path)

    def test_directory_is_reported_not_traceback(self):
        # Opening a directory raises PermissionError / IsADirectoryError, which
        # are OSError subclasses: they must become a clean SystemExit message
        # instead of escaping ``main`` as a traceback with exit code 1.
        os.makedirs(self.path("a-directory"))
        with self.assertRaises(SystemExit) as caught:
            comfy_validate.load_workflow(self.path("a-directory"))
        self.assertIn("cannot read workflow", str(caught.exception))


class SpecHelperTests(unittest.TestCase):
    def test_is_link_spec(self):
        self.assertTrue(comfy_validate.is_link_spec(["MODEL"]))
        self.assertFalse(comfy_validate.is_link_spec([["a", "b"]]))
        self.assertFalse(comfy_validate.is_link_spec(["INT"]))
        self.assertTrue(comfy_validate.is_link_spec([{"type": "LATENT"}]))

    def test_combo_choices(self):
        self.assertEqual(comfy_validate.combo_choices([["a", "b"]]), ["a", "b"])
        self.assertIsNone(comfy_validate.combo_choices(["INT"]))

    def test_scalar_kind(self):
        self.assertEqual(comfy_validate.scalar_kind(["INT"]), "INT")
        self.assertEqual(comfy_validate.scalar_kind(["FLOAT"]), "FLOAT")
        self.assertIsNone(comfy_validate.scalar_kind(["MODEL"]))

    def test_widget_inputs_excludes_links(self):
        names = comfy_validate.widget_inputs(OBJECT_INFO, "KSampler")
        self.assertIn("seed", names)
        self.assertNotIn("model", names)


class ApiValidationTests(unittest.TestCase):
    def test_unknown_class_is_error(self):
        workflow = {"1": {"class_type": "NoSuchNode", "inputs": {}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 1)

    def test_missing_class_type_is_error(self):
        workflow = {"1": {"inputs": {}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 1)

    def test_missing_required_input_is_error(self):
        workflow = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("batch_size" in error["message"] for error in report.errors))

    def test_link_to_missing_node_is_error(self):
        workflow = {
            "1": {"class_type": "KSampler", "inputs": ksampler_inputs(model=["99", 0])},
        }
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("not in the workflow" in error["message"] for error in report.errors))

    def test_link_slot_out_of_range_is_error(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}},
            "2": {"class_type": "KSampler", "inputs": ksampler_inputs(model=["1", 9])},
        }
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("output(s)" in error["message"] for error in report.errors))

    def test_widget_given_a_link_is_error(self):
        workflow = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["1", 0], "height": 512, "batch_size": 1}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("was given a link" in error["message"] for error in report.errors))

    def test_int_given_string_is_error(self):
        workflow = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": "wide", "height": 512, "batch_size": 1}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("expects INT" in error["message"] for error in report.errors))

    def test_bool_for_int_is_error(self):
        workflow = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": True, "height": 512, "batch_size": 1}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("expects INT" in error["message"] for error in report.errors))

    def test_float_accepts_int(self):
        workflow = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 0)

    def test_undeclared_input_is_warning(self):
        workflow = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1, "bogus": 2}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertTrue(any("does not declare" in warning["message"] for warning in report.warnings))

    def test_missing_model_is_warning(self):
        workflow = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ghost.safetensors"}}}
        report = comfy_validate.validate_api_workflow(
            workflow, OBJECT_INFO, model_index={"checkpoints": ["a.safetensors"]}
        )
        self.assertTrue(any("not present" in warning["message"] for warning in report.warnings))

    def test_present_model_not_warned(self):
        workflow = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}}}
        report = comfy_validate.validate_api_workflow(
            workflow, OBJECT_INFO, model_index={"checkpoints": ["a.safetensors"]}
        )
        self.assertEqual(report.as_dict()["warning_count"], 0)

    def test_virtual_node_skipped(self):
        workflow = {"1": {"class_type": "Note", "inputs": {"text": "hi"}}}
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 0)


class UiValidationTests(unittest.TestCase):
    def test_unknown_node_type_is_error(self):
        workflow = {"nodes": [{"id": 1, "type": "Ghost", "widgets_values": []}], "links": []}
        report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 1)

    def test_widget_count_mismatch_is_warning(self):
        workflow = {
            "nodes": [{"id": 1, "type": "EmptyLatentImage", "widgets_values": [512, 512]}],
            "links": [],
        }
        report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["warning_count"], 1)

    def test_link_to_missing_node_is_error(self):
        workflow = {
            "nodes": [{"id": 1, "type": "EmptyLatentImage", "widgets_values": [512, 512, 1]}],
            "links": [[1, 1, 0, 2, 0, "INT"]],
        }
        report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
        self.assertGreaterEqual(report.as_dict()["error_count"], 1)

    def test_link_slot_out_of_range_is_error(self):
        # Both endpoints exist, so this used to pass: the link still points at
        # output 1 of a node that only produces output 0, which is the load
        # failure the deeper check is there to catch.
        workflow = {
            "nodes": [
                {"id": 1, "type": "EmptyLatentImage", "widgets_values": [512, 512, 1]},
                {"id": 2, "type": "KSampler"},
            ],
            "links": [[1, 1, 1, 2, 0, "LATENT"]],
        }
        report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
        messages = [issue["message"] for issue in report.as_dict()["errors"]]
        self.assertTrue(
            any("slot 1" in message and "EmptyLatentImage" in message for message in messages),
            messages,
        )

    def test_link_slot_in_range_is_accepted(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "EmptyLatentImage", "widgets_values": [512, 512, 1]},
                {"id": 2, "type": "KSampler"},
            ],
            "links": [[1, 1, 0, 2, 0, "LATENT"]],
        }
        report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 0)

    def test_link_invalid_slot_values_are_errors(self):
        # Each of these is rejected by the guard that runs before the range
        # check. ``True`` matters most: ``bool`` is a subclass of ``int``, so
        # without the explicit ``isinstance(..., bool)`` test it would be read
        # as slot 1 and pass for a node that only produces one output.
        for origin_slot in (-1, True, "0", None):
            with self.subTest(origin_slot=origin_slot):
                workflow = {
                    "nodes": [
                        {"id": 1, "type": "EmptyLatentImage", "widgets_values": [512, 512, 1]},
                        {"id": 2, "type": "KSampler"},
                    ],
                    "links": [[1, 1, origin_slot, 2, 0, "LATENT"]],
                }
                report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
                messages = [issue["message"] for issue in report.as_dict()["errors"]]
                self.assertTrue(
                    any("invalid output slot" in message for message in messages),
                    messages,
                )

    def test_link_from_virtual_node_skips_slot_check(self):
        # A reroute/note node is not declared by ``/object_info`` and has no
        # output list, so its slot must not be range-checked: doing so would
        # report a load error for a graph ComfyUI accepts.
        workflow = {
            "nodes": [
                {"id": 1, "type": "Reroute"},
                {"id": 2, "type": "KSampler"},
            ],
            "links": [[1, 1, 5, 2, 0, "LATENT"]],
        }
        report = comfy_validate.validate_ui_workflow(workflow, OBJECT_INFO)
        self.assertEqual(report.as_dict()["error_count"], 0)


class ModelIndexTests(unittest.TestCase):
    def test_build_model_index_reads_folders(self):
        def opener(request, timeout=None):
            if request.full_url.endswith("/models"):
                return FakeResponse(json.dumps(["checkpoints", "loras"]))
            if request.full_url.endswith("/models/checkpoints"):
                return FakeResponse(json.dumps(["a.safetensors"]))
            if request.full_url.endswith("/models/loras"):
                return FakeResponse(json.dumps(["l.safetensors"]))
            return FakeResponse(json.dumps([]))

        index = comfy_validate.build_model_index("http://box:8188", opener=opener)
        self.assertEqual(index["checkpoints"], ["a.safetensors"])
        self.assertEqual(index["loras"], ["l.safetensors"])

    def test_build_model_index_none_when_unreachable(self):
        index = comfy_validate.build_model_index(
            "http://box:8188", opener=failing_opener(OSError("refused"))
        )
        self.assertIsNone(index)


class CliTests(TempDirMixin, unittest.TestCase):
    def test_isolation_pins_platform_roots_to_the_temp_dir(self):
        # The host scan (and its os.path.expanduser("~")) must be unreachable
        # while a CLI test runs, otherwise a real ComfyUI install decides the
        # outcome of the test.
        isolate_discovery(self)
        self.assertEqual(comfy_env.platform_roots(), [self.tmp])

    def test_isolation_removes_the_base_url_env_var(self):
        os.environ["COMFYUI_BASE_URL"] = "http://env-host:9999"
        self.addCleanup(os.environ.pop, "COMFYUI_BASE_URL", None)
        isolate_discovery(self)
        self.assertNotIn("COMFYUI_BASE_URL", os.environ)
        self.assertEqual(_common.resolve_base_url(None), _common.DEFAULT_BASE_URL)

    def test_isolation_disables_proxying_for_loopback(self):
        # The CLI tests below really do call http://127.0.0.1:1; a proxy that
        # is not bypassed for loopback would turn a fast refusal into a hang.
        isolate_discovery(self)
        self.assertIn("127.0.0.1", os.environ["no_proxy"])
        self.assertIn("127.0.0.1", os.environ["NO_PROXY"])

    def test_isolation_pins_the_home_variables_to_the_temp_dir(self):
        isolate_discovery(self)
        self.assertEqual(os.path.expanduser("~"), self.tmp)
        self.assertEqual(os.environ["HOME"], self.tmp)

    def unreachable_probe(self, base_url, timeout=None, opener=None):
        """Report the API as refused without opening a socket.

        The suite must stay network-free (docs/testing.md); the transport
        itself is covered by the injected-opener tests above, and the CLI only
        has to map an unreachable API to exit 3.
        """
        return {"base_url": base_url, "reachable": False,
                "error": "connection refused", "stats": None}

    def test_env_main_returns_three_when_nothing_found(self):
        isolate_discovery(self)
        with mock.patch.object(comfy_env, "probe_api", side_effect=self.unreachable_probe):
            code, out, _ = run_main(
                comfy_env, ["--root", self.tmp, "--base-url", "http://127.0.0.1:1"]
            )
        self.assertEqual(code, 3)
        self.assertIn("not reachable", out)
        self.assertIn("(none found)", out)

    def test_env_main_json_stays_inside_the_temp_root(self):
        isolate_discovery(self)
        self.write("ComfyUI/models/keep.bin", b"x")
        with mock.patch.object(comfy_env, "probe_api", side_effect=self.unreachable_probe):
            code, out, _ = run_main(
                comfy_env, ["--root", self.tmp, "--base-url", "http://127.0.0.1:1", "--json"]
            )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(
            report["model_roots"][0]["base_path"],
            os.path.join(os.path.abspath(self.tmp), "ComfyUI", "models"),
        )
        assert_paths_inside_tmp(self, report)

    def test_models_main_inventory_without_dir(self):
        code, out, _ = run_main(comfy_models, ["inventory", "--models-dir", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("0 files", out)

    def test_models_main_verify_conflict_exits_five(self):
        # The file is present with the expected size but the wrong digest, so
        # this is the more dangerous "conflict" case, not a missing file.
        self.write("a.bin", b"payload")
        manifest = self.write(
            "m.json",
            json.dumps([{"url": "u", "folder": "checkpoints", "filename": "a.bin", "sha256": "a" * 64, "size": 7}]),
        )
        code, out, _ = run_main(comfy_models, ["verify", "--manifest", manifest, "--models-dir", self.tmp])
        self.assertEqual(code, 5)
        self.assertIn("1 file(s) need attention", out)

    def test_models_main_install_dry_run_exits_zero(self):
        manifest = self.write(
            "m.json",
            json.dumps([{"url": "u", "folder": "checkpoints", "filename": "a.bin", "sha256": sha256(b"payload"), "size": 7}]),
        )
        code, out, _ = run_main(comfy_models, ["install", "--manifest", manifest, "--models-dir", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "checkpoints", "a.bin")))

    def test_models_main_install_bad_manifest_exits_four(self):
        manifest = self.write("m.json", json.dumps([{"url": "u", "folder": "checkpoints", "filename": "a.bin"}]))
        code, _, err = run_main(comfy_models, ["install", "--manifest", manifest, "--models-dir", self.tmp])
        self.assertEqual(code, 4)
        self.assertIn("sha256", err)

    def test_models_main_missing_manifest_exits_four(self):
        code, _, err = run_main(
            comfy_models, ["verify", "--manifest", os.path.join(self.tmp, "nope.json"), "--models-dir", self.tmp]
        )
        self.assertEqual(code, 4)
        self.assertIn("not found", err)

    def test_validate_main_returns_three_without_server(self):
        workflow = self.write("w.json", json.dumps(VALID_API_WORKFLOW))
        with mock.patch.object(
            comfy_validate, "fetch_object_info", side_effect=_common.ApiError("connection refused")
        ):
            code, _, err = run_main(comfy_validate, [workflow, "--base-url", "http://127.0.0.1:1"])
        self.assertEqual(code, 3)
        self.assertIn("hint:", err)

    def test_validate_main_rejects_unreadable_workflow(self):
        # load_workflow exits with a message, which Python turns into exit code 1.
        workflow = self.write("w.json", "{not json")
        with self.assertRaises(SystemExit) as caught:
            run_main(comfy_validate, [workflow, "--base-url", "http://127.0.0.1:1"])
        self.assertIn("invalid JSON", str(caught.exception.code))

    def test_validate_main_rejects_unknown_format(self):
        workflow = self.write("w.json", json.dumps({"hello": "world"}))
        with self.assertRaises(SystemExit) as caught:
            run_main(comfy_validate, [workflow, "--base-url", "http://127.0.0.1:1"])
        self.assertIn("unrecognised workflow format", str(caught.exception.code))


# ---------------------------------------------------------------------------
# Regressions from the independent review
# ---------------------------------------------------------------------------


class BatchRobustnessTests(TempDirMixin, unittest.TestCase):
    """One failing entry must never take the remaining downloads down with it."""

    def manifest_of(self, entries):
        """Normalise entries exactly as the CLI does, from a real file."""
        return _common.load_manifest(self.write("m.json", json.dumps(entries)))

    def test_unwritable_entry_does_not_abort_the_batch(self):
        body = b"checkpoint-bytes"
        # A directory sitting where the model file must land makes
        # os.replace(part, dest) raise OSError (EISDIR on POSIX, PermissionError
        # on Windows). Before the fix that error escaped install_manifest and
        # every later entry was silently never downloaded.
        self.write("checkpoints/a.safetensors/occupied.txt", b"x")
        entries = self.manifest_of(
            [
                {"url": "http://box:8188/a", "folder": "checkpoints",
                 "filename": "a.safetensors", "sha256": sha256(body), "size": len(body)},
                {"url": "http://box:8188/b", "folder": "checkpoints",
                 "filename": "b.safetensors", "sha256": sha256(body), "size": len(body)},
            ]
        )
        payload = comfy_models.install_manifest(
            entries, self.tmp, dry_run=False, confirmed=True, allow_overwrite=True,
            opener=bytes_opener(body), space_check=False,
        )
        actions = {result["entry"]: result["action"] for result in payload["results"]}
        self.assertEqual(actions["a.safetensors"], "failed")
        self.assertEqual(actions["b.safetensors"], "installed")
        # The entry after the failure is complete and verified, not a .part.
        survivor = self.path("checkpoints", "b.safetensors")
        self.assertEqual(Path(survivor).read_bytes(), body)
        self.assertFalse(os.path.exists(survivor + ".part"))

    def test_failed_entry_reports_its_reason(self):
        self.write("checkpoints/a.safetensors/occupied.txt", b"x")
        entries = self.manifest_of(
            [
                {"url": "http://box:8188/a", "folder": "checkpoints",
                 "filename": "a.safetensors", "sha256": sha256(b"x"), "size": 1},
            ]
        )
        payload = comfy_models.install_manifest(
            entries, self.tmp, dry_run=False, confirmed=True, allow_overwrite=True,
            opener=bytes_opener(b"x"), space_check=False,
        )
        detail = payload["results"][0]["detail"]
        self.assertIn("cannot write", detail)
        self.assertIn("a.safetensors", detail)


    def test_download_error_is_reported_not_raised(self):
        """An OSError escaping the download layer must not abort the batch."""
        body = b"checkpoint-bytes"
        entries = self.manifest_of(
            [
                {"url": "http://box:8188/a", "folder": "checkpoints",
                 "filename": "a.safetensors", "sha256": sha256(body), "size": len(body)},
                {"url": "http://box:8188/b", "folder": "checkpoints",
                 "filename": "b.safetensors", "sha256": sha256(body), "size": len(body)},
            ]
        )
        real_download = comfy_models.download_file

        def flaky(url, destination, digest, **kwargs):
            if url.endswith("/a"):
                raise OSError("device not ready")
            return real_download(url, destination, digest, **kwargs)

        with mock.patch.object(comfy_models, "download_file", side_effect=flaky):
            payload = comfy_models.install_manifest(
                entries, self.tmp, dry_run=False, confirmed=True,
                opener=bytes_opener(body), space_check=False,
            )
        by_entry = {result["entry"]: result for result in payload["results"]}
        self.assertEqual(by_entry["a.safetensors"]["action"], "failed")
        self.assertIn("device not ready", by_entry["a.safetensors"]["detail"])
        self.assertEqual(by_entry["b.safetensors"]["action"], "installed")
        self.assertEqual(Path(self.path("checkpoints", "b.safetensors")).read_bytes(), body)


class UnknownSizeTests(TempDirMixin, unittest.TestCase):
    """A manifest entry without ``size`` must not be presented as counted."""

    def without_size(self):
        return self.write(
            "m.json",
            json.dumps([{"url": "http://box:8188/a", "folder": "checkpoints",
                         "filename": "a.bin", "sha256": sha256(b"payload")}]),
        )

    def test_plan_does_not_invent_a_size(self):
        entries = _common.load_manifest(self.without_size())
        rows, needed = comfy_models.plan_manifest(entries, self.tmp)
        self.assertEqual(needed, 0)
        self.assertEqual([row["name"] for row in comfy_models.unknown_size_rows(rows)], ["a.bin"])

    def test_unknown_size_entries_still_need_work(self):
        entries = _common.load_manifest(self.without_size())
        rows, _needed = comfy_models.plan_manifest(entries, self.tmp)
        # Missing from the byte total, but not from the work list.
        self.assertNotEqual(rows[0]["status"], comfy_models.STATUS_OK)

    def test_installed_entry_is_not_reported_as_unknown(self):
        body = b"payload"
        self.write("checkpoints/a.bin", body)
        entries = _common.load_manifest(self.without_size())
        rows, needed = comfy_models.plan_manifest(entries, self.tmp)
        self.assertEqual(comfy_models.STATUS_OK, rows[0]["status"])
        self.assertEqual(needed, 0)
        self.assertEqual(comfy_models.unknown_size_rows(rows), [])

    def test_install_dry_run_reports_bytes_unknown(self):
        code, out, _ = run_main(
            comfy_models,
            ["install", "--manifest", self.without_size(), "--models-dir", self.tmp, "--json"],
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["bytes_needed"], 0)
        self.assertEqual(payload["bytes_unknown"], 1)


class InstallExitCodeTests(TempDirMixin, unittest.TestCase):
    """The install command must not report success for a failed run."""

    def manifest_file(self):
        return self.write(
            "m.json",
            json.dumps([{"url": "u", "folder": "checkpoints", "filename": "a.bin",
                         "sha256": sha256(b"payload"), "size": 7}]),
        )

    def run_install(self, action):
        payload = {
            "dry_run": False,
            "dest_root": self.tmp,
            "rows": [],
            "bytes_needed": 0,
            "bytes_unknown": 0,
            "free_bytes": None,
            "results": [{"entry": "a.bin", "action": action, "detail": action}],
        }
        with mock.patch.object(comfy_models, "install_manifest", return_value=payload):
            return run_main(
                comfy_models,
                ["install", "--manifest", self.manifest_file(), "--models-dir", self.tmp, "--yes"],
            )[0]

    def test_failed_entry_exits_five(self):
        self.assertEqual(self.run_install("failed"), 5)

    def test_refused_entry_exits_six(self):
        self.assertEqual(self.run_install("refused"), 6)

    def test_untouched_entry_exits_zero(self):
        self.assertEqual(self.run_install("skipped"), 0)


class ContainmentTests(TempDirMixin, unittest.TestCase):
    """A link inside the model directory must not redirect a write outside it."""

    def link_or_skip(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            # Creating a symlink needs privileges this host may withhold. A
            # Windows junction behaves identically here and would be rejected
            # by the same realpath comparison.
            self.skipTest(f"cannot create a directory link on this host: {exc}")

    def test_link_escaping_the_root_is_refused(self):
        root = self.path("models")
        outside = self.path("outside")
        os.makedirs(root)
        os.makedirs(outside)
        self.link_or_skip(outside, self.path("models", "checkpoints"))
        with self.assertRaises(_common.ComfyModelManagerError):
            _common.safe_child_path(root, os.path.join("checkpoints", "a.safetensors"))

    def test_entry_destination_refuses_an_escaping_link(self):
        root = self.path("models")
        os.makedirs(root)
        outside = self.path("outside")
        os.makedirs(outside)
        self.link_or_skip(outside, self.path("models", "checkpoints"))
        with self.assertRaises(_common.ComfyModelManagerError):
            comfy_models.entry_destination(root, {"folder": "checkpoints", "filename": "a.bin"})

    def test_link_staying_inside_the_root_is_allowed(self):
        root = self.path("models")
        real = self.path("models", "real")
        os.makedirs(real)
        link = self.path("models", "checkpoints")
        try:
            os.symlink(real, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create a directory link on this host: {exc}")
        resolved = _common.safe_child_path(root, os.path.join("checkpoints", "a.bin"))
        self.assertEqual(resolved, os.path.join(os.path.realpath(real), "a.bin"))

    def test_plain_relative_path_still_resolves(self):
        root = self.path("models")
        os.makedirs(root)
        self.assertEqual(
            _common.safe_child_path(root, os.path.join("loras", "x.bin")),
            os.path.join(os.path.realpath(root), "loras", "x.bin"),
        )


class CliExitCodeTests(TempDirMixin, unittest.TestCase):
    """Every documented exit code must survive the paths that used to traceback.

    Both cases here used to escape ``main`` as an uncaught exception, which
    Python reports as exit 1 with a traceback, hiding the status the docs and
    the module docstring promise.
    """

    def escaping_manifest(self):
        return self.write(
            "esc.json",
            json.dumps([{"url": "http://box:8188/a", "folder": "..",
                         "filename": "evil.bin", "sha256": "0" * 64}]),
        )

    def sized_manifest(self, filename="a.bin"):
        return self.write(
            "m.json",
            json.dumps([{"url": "http://box:8188/a", "folder": "checkpoints",
                         "filename": filename, "sha256": sha256(b"payload"), "size": 7}]),
        )

    def test_escaping_manifest_exits_four_on_verify(self):
        code, _, err = run_main(
            comfy_models,
            ["verify", "--manifest", self.escaping_manifest(), "--models-dir", self.tmp],
        )
        self.assertEqual(code, 4)
        self.assertIn("escapes its root", err)

    def test_escaping_manifest_exits_four_on_install(self):
        code, _, err = run_main(
            comfy_models,
            ["install", "--manifest", self.escaping_manifest(), "--models-dir", self.tmp, "--yes"],
        )
        self.assertEqual(code, 4)
        self.assertIn("escapes its root", err)

    def test_install_aborts_with_five_when_space_is_short(self):
        with mock.patch.object(comfy_models, "min_free_bytes", return_value=0), \
                mock.patch.object(comfy_models, "free_bytes", return_value=0):
            code, _, err = run_main(
                comfy_models,
                ["install", "--manifest", self.sized_manifest(), "--models-dir", self.tmp, "--yes"],
            )
        self.assertEqual(code, 5)
        self.assertIn("insufficient space", err)
        self.assertFalse(os.path.exists(self.path("checkpoints", "a.bin")))

    def test_failed_and_refused_together_exits_five(self):
        # One entry is refused (a different file already occupies the
        # destination) and one fails (its transfer raises). The more severe
        # status must win, and install_manifest must not be mocked away.
        self.write("checkpoints/taken.bin", b"different-bytes")
        manifest = self.write(
            "m.json",
            json.dumps([
                {"url": "http://box:8188/a", "folder": "checkpoints", "filename": "taken.bin",
                 "sha256": sha256(b"payload"), "size": 7},
                {"url": "http://box:8188/dead", "folder": "checkpoints", "filename": "new.bin",
                 "sha256": sha256(b"payload"), "size": 7},
            ]),
        )
        with mock.patch.object(comfy_models, "min_free_bytes", return_value=None), \
                mock.patch.object(comfy_models, "download_file",
                                  side_effect=_common.IntegrityError("connection refused")):
            code, out, _ = run_main(
                comfy_models,
                ["install", "--manifest", manifest, "--models-dir", self.tmp, "--yes", "--json"],
            )
        self.assertEqual(code, 5)
        actions = {row["entry"]: row["action"] for row in json.loads(out)["results"]}
        self.assertEqual(actions["taken.bin"], "refused")
        self.assertEqual(actions["new.bin"], "failed")

    def test_dry_run_reports_a_directory_blocking_the_destination(self):
        self.write("checkpoints/a.bin/occupied.txt", b"x")
        code, out, _ = run_main(
            comfy_models,
            ["install", "--manifest", self.sized_manifest(), "--models-dir", self.tmp],
        )
        self.assertEqual(code, 0)
        self.assertIn("conflict", out)
        self.assertIn("directory occupies", out)

    def test_duplicate_destination_is_rejected(self):
        manifest = self.write(
            "dup.json",
            json.dumps([
                {"url": "http://box:8188/a", "folder": "checkpoints", "filename": "a.bin",
                 "sha256": "0" * 64},
                {"url": "http://box:8188/b", "folder": "checkpoints", "filename": "a.bin",
                 "sha256": "1" * 64},
            ]),
        )
        code, _, err = run_main(
            comfy_models, ["verify", "--manifest", manifest, "--models-dir", self.tmp]
        )
        self.assertEqual(code, 4)
        self.assertIn("already defined", err)

    def test_duplicate_destination_is_detected_after_normalisation(self):
        # "a/b" + "c.bin" and "a" + "b/c.bin" are the same file on disk.
        manifest = self.write(
            "dup.json",
            json.dumps([
                {"url": "http://box:8188/a", "folder": "a/b", "filename": "c.bin",
                 "sha256": "0" * 64},
                {"url": "http://box:8188/b", "folder": "a", "filename": "b/c.bin",
                 "sha256": "1" * 64},
            ]),
        )
        with self.assertRaises(_common.ManifestError):
            _common.load_manifest(manifest)


class DefaultModelsDirTests(unittest.TestCase):
    """``default_models_dir`` is the only path that reads the host by itself."""

    def run_with(self, roots):
        with mock.patch.object(comfy_env, "discover", return_value={"model_roots": roots}):
            return comfy_models.default_models_dir()

    def test_prefers_the_comfyui_section(self):
        roots = [
            {"base_path": "C:/extra", "is_default": True, "section": "extra_model_paths"},
            {"base_path": "C:/comfy/models", "is_default": True, "section": "ComfyUI"},
        ]
        self.assertEqual(self.run_with(roots), "C:/comfy/models")

    def test_falls_back_to_any_default_root(self):
        roots = [
            {"base_path": "C:/plain", "is_default": False, "section": "a"},
            {"base_path": "C:/default", "is_default": True, "section": "b"},
        ]
        self.assertEqual(self.run_with(roots), "C:/default")

    def test_falls_back_to_the_first_root(self):
        roots = [{"base_path": "C:/only", "is_default": False, "section": "a"}]
        self.assertEqual(self.run_with(roots), "C:/only")

    def test_returns_none_without_roots(self):
        self.assertIsNone(self.run_with([]))


class InferInstallDirTests(TempDirMixin, unittest.TestCase):
    """A guessed install directory must be proven, never assumed."""

    def test_config_inside_a_discovered_install_wins(self):
        install = self.path("ComfyUI")
        config = self.write("ComfyUI/extra_model_paths.yaml", "a:\n  base_path: models\n")
        self.assertEqual(comfy_env.infer_install_dir(config, [install]), install)

    def test_grandparent_that_looks_like_an_install_is_used(self):
        self.write("ComfyUI/main.py", "# install marker\n")
        config = self.write("ComfyUI/user/extra_model_paths.yaml", "a:\n  base_path: models\n")
        self.assertEqual(
            comfy_env.infer_install_dir(config, []),
            os.path.realpath(self.path("ComfyUI")),
        )

    def test_desktop_layout_is_not_mistaken_for_an_install(self):
        # A Comfy Desktop instance config sits two levels below a directory
        # that carries no install markers; the guess must be refused so that
        # relative base_path values resolve against the config's own directory.
        config = self.write(
            "ComfyDesktop/instances/instance-model-paths/config.yaml",
            "a:\n  base_path: models\n",
        )
        self.assertIsNone(comfy_env.infer_install_dir(config, []))

    def test_missing_config_path_returns_none(self):
        self.assertIsNone(comfy_env.infer_install_dir(None, []))


class EmptyOutputSourceTests(unittest.TestCase):
    """A link from a node that produces nothing is an error, not a warning."""

    def object_info(self):
        info = dict(OBJECT_INFO)
        info["NoteOnly"] = {"input": {"required": {}}, "output": []}
        return info

    def test_link_from_node_without_outputs_is_error(self):
        workflow = {
            "1": {"class_type": "NoteOnly", "inputs": {}},
            "2": {"class_type": "KSampler", "inputs": ksampler_inputs(model=["1", 0])},
        }
        report = comfy_validate.validate_api_workflow(workflow, self.object_info())
        self.assertTrue(
            any("produces no outputs" in error["message"] for error in report.errors),
            report.errors,
        )

    def test_virtual_source_absent_from_object_info_is_not_an_error(self):
        # Reroute is a frontend-only node: it is legitimately missing from
        # /object_info and must not be reported as producing nothing.
        workflow = {
            "1": {"class_type": "Reroute", "inputs": {"value": ["2", 0]}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}},
            "3": {"class_type": "KSampler", "inputs": ksampler_inputs(model=["1", 0])},
        }
        report = comfy_validate.validate_api_workflow(workflow, OBJECT_INFO)
        self.assertFalse(
            any("produces no outputs" in error["message"] for error in report.errors),
            report.errors,
        )


if __name__ == "__main__":
    unittest.main()
