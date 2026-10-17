"""Shared helpers for the audit test-suite.

Fixtures are generated inside a temporary directory instead of being committed
to ``tests/fixtures``: the privacy cases need values that match real formats,
and keeping them out of the tree means the skill's own audit does not report
its own fixtures as leaks.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _common as common  # noqa: E402
import audit_skill  # noqa: E402
import checks  # noqa: E402
import render_checks  # noqa: E402

SKILL_MD = """---
name: {name}
description: Use when exercising the audit fixtures. Keeps the sample minimal.
version: {version}
platforms: [{platforms}]
metadata:
  hermes:
    tags: [sample, audit]
    category: testing
---

# {name}

Skill de exemplo usada pelos testes automatizados deste repositório.

## Uso

Executar o script de exemplo e conferir a saída no terminal.

## Exemplo

Pedido em linguagem natural: "mostre a versão do script de exemplo".

```console
$ python scripts/run.py
0.1.0
```

O comportamento foi validado no Linux com Python 3.11.
"""

README_MD = """# {name}

Skill de exemplo usada pelos testes automatizados deste repositório.

## Objetivo

Demonstrar a estrutura mínima que a auditoria considera bem formada, sem
depender de serviço externo nem de configuração local.

## Uso

Execute o script de exemplo e confira a saída impressa no terminal.

## Exemplo

Pedido: "mostre a versão do script".

```console
$ python scripts/run.py
0.1.0
```

## Limites

O exemplo não cobre integração com outros sistemas.

Versão documentada: {version}.
"""

RUN_PY = '''#!/usr/bin/env python3
"""Example script used by the audit fixtures."""

import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Print the sample version.")
    parser.add_argument("--version", action="store_true")
    parser.parse_args(argv)
    print("0.1.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

FIXTURE_TEST = '''"""Fixture test file: exists so SQA-015/SQA-016 see tests/."""

import unittest


class SampleTest(unittest.TestCase):
    def test_placeholder(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
'''

BASE_FILES = {
    "scripts/run.py": RUN_PY,
    "tests/test_run.py": FIXTURE_TEST,
}


def build_skill(
    base,
    *,
    name: str = "sample-skill",
    version: str = "0.1.0",
    platforms: str = "linux",
    skill_md: str | None = None,
    readme: str | None = None,
    files: dict[str, str] | None = None,
    drop: tuple[str, ...] = (),
) -> pathlib.Path:
    """Write a minimal skill under ``base`` and return its directory."""
    root = pathlib.Path(base) / name
    root.mkdir(parents=True, exist_ok=True)
    contents = {
        "SKILL.md": (
            SKILL_MD.format(name=name, version=version, platforms=platforms)
            if skill_md is None
            else skill_md
        ),
        "README.md": (
            README_MD.format(name=name, version=version) if readme is None else readme
        ),
        **BASE_FILES,
        **(files or {}),
    }
    for rel, text in contents.items():
        if rel in drop or text is None:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def audit(root, **kwargs) -> dict:
    return audit_skill.audit_target(str(root), **kwargs)


def codes(report: dict) -> list[str]:
    return [item["id"] for item in report["findings"]]


def findings(report: dict, code: str) -> list[dict]:
    return [item for item in report["findings"] if item["id"] == code]


def status(report: dict, code: str) -> str:
    for entry in report["checks_run"]:
        if entry["code"] == code:
            return entry["status"]
    raise AssertionError(f"verificação {code} não registrada")


@contextlib.contextmanager
def working_directory(path):
    """Temporarily change the process working directory."""
    previous = pathlib.Path.cwd()
    import os

    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
