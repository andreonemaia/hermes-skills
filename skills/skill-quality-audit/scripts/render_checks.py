#!/usr/bin/env python3
"""Regenerate ``references/checks.md`` from the catalogue in ``checks.py``.

Usage:
    python scripts/render_checks.py            # rewrite references/checks.md
    python scripts/render_checks.py --check    # fail if the file is stale

The output is deterministic: it depends only on ``checks.CATALOGUE`` and the
constant templates below, never on the working directory contents, the clock or
the scratch area. ``tests/test_catalogue_docs.py`` asserts that the committed
file is byte-identical to what this script produces, so the catalogue, the
documentation and the published command cannot drift apart silently.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import checks  # noqa: E402

HEADER = """\
# Catálogo de verificações

<!-- Gerado por `python scripts/render_checks.py` a partir de `scripts/checks.py`.
     Não editar à mão: regenere o arquivo e confira o diff. -->
"""

STRUCTURAL_INTRO = (
    "Executadas por `python scripts/audit_skill.py audit --target <skill>`; "
    "cada resultado traz status executado, motivo e achados."
)

SEMANTIC_INTRO = (
    "Conduzidas pelo agente a partir das evidências coletadas; o script entrega "
    "o checklist e não as executa. A conclusão de cada item registra a evidência "
    "usada."
)


def _field(label: str, value: str) -> str:
    """Render one list field, keeping continuation lines inside the item.

    ``how`` and ``limits`` are prose and may wrap; indenting the continuation by
    two spaces keeps them in the same markdown list item instead of starting a
    new paragraph that breaks the list.
    """
    lines = value.splitlines() or [""]
    out = [f"- {label}: {lines[0]}\n"]
    for line in lines[1:]:
        out.append(f"  {line}\n" if line.strip() else "\n")
    return "".join(out)


def _section(check: checks.Check) -> str:
    return (
        f"### {check.code} — {check.summary}\n"
        "\n"
        f"- categoria: {check.category}\n"
        f"- severidade padrão: {check.default_severity}\n"
        f"- o que verifica: {check.summary}\n"
        + _field("como", check.how)
        + _field("limites", check.limits)
    )


def render() -> str:
    """Return the full markdown document for the current catalogue."""
    structural = [c for c in checks.CATALOGUE if c.method == "structural"]
    semantic = [c for c in checks.CATALOGUE if c.method == "semantic"]
    parts = [
        HEADER,
        f"{len(checks.CATALOGUE)} verificações: {len(structural)} estruturais "
        f"executadas pelo script e {len(semantic)} semânticas conduzidas pelo agente.\n",
        "| código | método | severidade padrão | título |",
        "| --- | --- | --- | --- |",
    ]
    for check in checks.CATALOGUE:
        parts.append(
            f"| {check.code} | {check.method} | {check.default_severity} | "
            f"{check.summary} |"
        )
    parts.append("")
    parts.append("## Verificações estruturais")
    parts.append("")
    parts.append(STRUCTURAL_INTRO)
    parts.append("")
    parts.extend(_section(check) for check in structural)
    parts.append("## Verificações semânticas")
    parts.append("")
    parts.append(SEMANTIC_INTRO)
    parts.append("")
    parts.extend(_section(check) for check in semantic)
    return "\n".join(parts).rstrip("\n") + "\n"


def target_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "references" / "checks.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="não escreve: sai com 1 se o arquivo versionado estiver desatualizado",
    )
    args = parser.parse_args(argv)
    path = target_path()
    rendered = render()
    if args.check:
        # Compare bytes: ``read_text`` applies universal-newline translation,
        # so a CRLF checkout of an identical file would pass while the
        # committed bytes differ from what this script writes.
        expected = rendered.encode("utf-8")
        try:
            current = path.read_bytes() if path.exists() else b""
        except OSError as exc:
            print(f"não foi possível ler {path}: {exc}", file=sys.stderr)
            return 1
        if current != expected:
            print(f"desatualizado: {path}", file=sys.stderr)
            return 1
        print(f"atualizado: {path}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"escrito: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
