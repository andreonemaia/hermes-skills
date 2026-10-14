#!/usr/bin/env python3
"""skill-execution-report: what a Hermes session actually did with its skills.

Read-only. The tool opens ``state.db`` with ``mode=ro`` (plus
``PRAGMA query_only``), reads ``skills/.usage.json`` as a secondary aggregate
source, scans the skills directories for the set of known skill names, and can
check a local git repository to verify commits. It never writes to any of them,
never runs commands found in the transcript, and never contacts the network.

Usage:
    python scripts/report.py --session latest
    python scripts/report.py --session latest --json
    python scripts/report.py --session latest --include-children --repo <path>

Exit codes: 0 evidence found, 1 no evidence, 2 corrupt source (also used for an
internal analysis/render failure, always with a message on stderr and no
traceback), 3 missing source, 4 usage error, 5 analysis succeeded but ``--out``
could not be written.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (  # noqa: E402
    EXIT_CORRUPT,
    EXIT_MISSING,
    EXIT_NO_EVIDENCE,
    EXIT_OK,
    EXIT_OUTPUT,
    EXIT_USAGE,
    MAX_SKILLS_DEFAULT,
    ReportError,
    SourceCorrupt,
    SourceMissing,
    UsageArgumentParser,
    clock_source,
    default_db_path,
    default_skills_root,
    default_usage_path,
    env_hermes_home,
    file_facts,
    iso,
    load_usage,
    now_ts,
    parse_iso,
    redact,
)
from classify import (  # noqa: E402
    aggregate_findings,
    classify_scope,
    divergences,
    summarise,
)
from evidence import StateDb, SkillIndex, collect_session_events, git_facts  # noqa: E402
from render import build_report, render_text, to_json  # noqa: E402

DEFAULT_LIMITATIONS_NOTE = (
    "Relatório somente leitura. Nenhum dado é enviado para fora desta máquina "
    "e nenhum arquivo de estado é modificado."
)


def build_parser() -> argparse.ArgumentParser:
    parser = UsageArgumentParser(
        prog="skill-execution-report",
        description=(
            "Mostra quais skills foram realmente utilizadas em uma sessão do "
            "Hermes, com evidência verificável para cada classificação."
        ),
    )
    parser.add_argument("--session", default="latest", metavar="REF",
                        help="id completo, prefixo único ou 'latest' (padrão: latest)")
    parser.add_argument("--since", metavar="ISO",
                        help="início da janela de análise (ISO-8601 ou epoch)")
    parser.add_argument("--until", metavar="ISO",
                        help="fim da janela de análise (ISO-8601 ou epoch)")
    parser.add_argument("--include-children", action="store_true",
                        help="inclui sessões filhas vinculadas, em seção separada")
    parser.add_argument("--db", default=None, metavar="PATH",
                        help="caminho do state.db (padrão: HERMES_HOME/state.db)")
    parser.add_argument("--usage", default=None, metavar="PATH",
                        help="caminho do skills/.usage.json (padrão: HERMES_HOME/skills/.usage.json)")
    parser.add_argument("--no-usage", action="store_true",
                        help="não consulta skills/.usage.json")
    parser.add_argument("--skills-root", action="append", default=[], metavar="PATH",
                        help="diretório de skills para o índice de nomes conhecidos (repetível)")
    parser.add_argument("--repo", default=None, metavar="PATH",
                        help="repositório git local usado para verificar commits "
                             "(padrão: git_repo_root da sessão)")
    parser.add_argument("--no-artifact-check", action="store_true",
                        help="não verifica artefatos em disco nem commits")
    parser.add_argument("--json", action="store_true", help="saída JSON estruturada")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="grava o relatório no arquivo informado além do stdout")
    parser.add_argument("--max-skills", type=int, default=MAX_SKILLS_DEFAULT,
                        metavar="N", help="limite da lista de skills disponíveis no JSON "
                                          "(0 = sem limite; negativo é erro de uso)")
    return parser


def parse_window(value: str | None, label: str) -> float | None:
    if value is None:
        return None
    try:
        return parse_iso(value)
    except ValueError as exc:
        raise ReportError("--%s inválido: %s" % (label, exc)) from exc


def collect(args) -> tuple[dict, int]:
    """Collect evidence and return (payload, exit_code)."""
    db_path = args.db or default_db_path()
    usage_path = None if args.no_usage else (args.usage or default_usage_path())
    skills_roots: list[str] = []
    for root in [default_skills_root()] + list(args.skills_root):
        if root and root not in skills_roots:
            skills_roots.append(root)
    since = parse_window(args.since, "since")
    until = parse_window(args.until, "until")
    if since is not None and until is not None and since > until:
        raise ReportError("--since é posterior a --until")
    if args.max_skills is not None and args.max_skills < 0:
        raise ReportError("--max-skills deve ser >= 0 (0 = sem limite)")
    # 0 (or an unset value) means "no cap" everywhere below.
    max_skills = args.max_skills or 0

    db = StateDb(db_path)
    try:
        session = db.resolve_session(args.session)
        index = SkillIndex.scan(skills_roots)
        repo = args.repo or session.get("git_repo_root")

        scopes: list[dict] = []
        all_events: list[dict] = []
        scope_repos: dict[str, str | None] = {}
        for scope_name, scope_session in [("main", session)] + [
            ("child:%s" % child["id"], child)
            for child in (db.children(session["id"]) if args.include_children else [])
        ]:
            # Every scope is verified against its own repository: a child session
            # may have run in a different checkout, and --repo stays the explicit
            # override for all of them.
            scope_repo = args.repo or scope_session.get("git_repo_root") or repo
            scope_repos[scope_name] = scope_repo
            events, facts = collect_session_events(
                db, scope_session, index, since, until, scope_name, scope_repo,
                not args.no_artifact_check,
            )
            scopes.append({"scope": scope_name, "session_id": scope_session["id"],
                           "session": scope_session, "facts": facts})
            all_events.extend(events)

        findings: list[dict] = []
        for scope in scopes:
            findings.extend(classify_scope(
                scope["scope"], all_events, scope["session"], set(index.sorted_names()),
                [root for root in (scope["session"].get("cwd"),
                                   scope["session"].get("git_repo_root"),
                                   scope_repos.get(scope["scope"])) if root],
                not args.no_artifact_check,
                bool(since or until),
                bool(scope["facts"].get("compacted_messages")),
            ))
        findings.sort(key=lambda item: (-item["state_rank"], item["skill"], item["scope"]))

        usage = load_usage(usage_path)
        covered = {(finding["skill"], finding["scope"]) for finding in findings}
        if usage.get("present"):
            findings.extend(aggregate_findings(usage, covered, skills_roots))
            findings.sort(key=lambda item: (-item["state_rank"], item["skill"], item["scope"]))
        divergence_rows = divergences(findings, usage) if usage.get("present") else []

        available = index.sorted_names()
        observed = {finding["skill"] for finding in findings if finding["state_rank"] >= 1}
        counts = summarise(findings, set(available), observed)

        sources = {
            "state_db": dict(
                file_facts(db_path),
                schema={"compatible": bool(db.schema.get("compatible", True)),
                        "problems": list(db.schema.get("problems") or [])},
                sessions_total=db.session_count(),
            ),
            "usage_json": dict(
                {"present": usage.get("present", False),
                 "counters_are_global": bool(usage.get("present", False))},
                **({"malformed_entries": list(usage["malformed_entries"])}
                   if usage.get("malformed_entries") else {}),
                **(usage.get("file") or {}),
            ),
            "skills_index": {"roots_count": len(skills_roots),
                             "skills_known": len(available)},
            "git": git_facts(repo),
        }
        # Every source the run actually consulted is named here, so a reader can
        # tell what was looked at and what was skipped.
        sources_enabled = ["state_db"]
        if usage.get("present"):
            sources_enabled.append("usage_json")
        sources_enabled.append("skills_index")
        if repo:
            sources_enabled.append("git")
        options = {
            "since": iso(since), "until": iso(until),
            "include_children": args.include_children,
            "artifact_check": not args.no_artifact_check,
            "max_skills": args.max_skills,
            "sources_enabled": sources_enabled,
        }
        payload = build_report(
            generated_at=iso(now_ts()) or "", session=session, scopes=scopes,
            findings=findings, divergences=divergence_rows,
            available_names=available[:max_skills] if max_skills > 0 else available,
            usage=usage, index_roots=skills_roots, sources=sources,
            options=options, counts=counts,
        )
        payload["clock_source"] = clock_source()
        payload["note"] = DEFAULT_LIMITATIONS_NOTE
        payload["evidence_events"] = len(all_events)
        exit_code = EXIT_OK if counts["findings"] else EXIT_NO_EVIDENCE
        return payload, exit_code
    finally:
        db.close()


def make_stream_tolerant(stream) -> None:
    """Make ``stream`` survive characters its encoding cannot represent.

    The report quotes transcript text and skill names, so a redirected stdout or
    stderr on a narrow code page (cp1252 on Windows) would raise
    ``UnicodeEncodeError`` halfway through the output, leaving a truncated report
    and a traceback. Replacing the unrepresentable character keeps the report
    readable and the exit code intact. Streams that cannot be reconfigured (a
    wrapper, a test double, a closed pipe) are left untouched.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


def _silence_stdout() -> None:
    """Point further stdout writes at the null device.

    Called when the reader of the pipe went away (``report.py | head``): the
    interpreter would otherwise try to flush the buffered text while exiting and
    print a second, confusing error after the one already handled.
    """
    try:
        fileno = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        # A replaced stdout (test double, captured stream) has no descriptor;
        # there is nothing to silence and no reason to open the null device.
        return
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), fileno)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the exit code.

    Every failure path returns a documented code instead of letting an exception
    escape: an uncaught exception would make the process exit with 1, which is
    reserved for "no findings", and a crash would then be indistinguishable from
    a clean run that found nothing.
    """
    make_stream_tolerant(sys.stdout)
    make_stream_tolerant(sys.stderr)
    args = build_parser().parse_args(argv)
    # Sentinel: if the run is interrupted before ``collect`` returns its code,
    # the outcome is unknown, and unknown must not look like "no findings".
    code = EXIT_CORRUPT
    try:
        payload, code = collect(args)
        text = to_json(payload) if args.json else render_text(payload)
        sys.stdout.write(text)
        # Flush here so a closed pipe surfaces now, inside the handler below,
        # instead of during interpreter shutdown where nothing can catch it.
        sys.stdout.flush()
        if args.out:
            try:
                with open(args.out, "w", encoding="utf-8") as handle:
                    handle.write(text)
            except OSError as exc:
                print("não foi possível gravar %s: %s" % (args.out, exc), file=sys.stderr)
                return EXIT_OUTPUT
    except BrokenPipeError:
        # The reader closed the pipe; the analysis itself succeeded and is
        # reported through the exit code. Nothing is printed, because there is
        # nowhere left to print to.
        _silence_stdout()
        return code
    except SourceMissing as exc:
        # Achado 46: a mensagem carrega referências do usuário (id de sessão,
        # prefixo, caminho do banco). Passa pelo mesmo `redact` dos demais
        # erros, senão o stderr vira uma segunda via de vazamento, fora do
        # relatório que já é sanitizado.
        print("fonte ausente: %s" % redact(str(exc), 200), file=sys.stderr)
        return EXIT_MISSING
    except SourceCorrupt as exc:
        print("fonte ilegível: %s" % redact(str(exc), 200), file=sys.stderr)
        return EXIT_CORRUPT
    except (sqlite3.Error, OSError) as exc:
        print("erro ao ler a fonte: %s" % redact(str(exc), 200), file=sys.stderr)
        return EXIT_CORRUPT
    except ReportError as exc:
        print("erro: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - no documented code means "crash"
        # An internal fault is reported as an untrustworthy analysis (the same
        # family as EXIT_CORRUPT) with a message and no traceback. The one thing
        # it must never do is exit 1, which would claim "no evidence found".
        print("erro interno (%s): %s" % (type(exc).__name__, redact(str(exc), 200)),
              file=sys.stderr)
        return EXIT_CORRUPT
    return code


if __name__ == "__main__":
    sys.exit(main())
