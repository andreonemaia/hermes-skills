"""Deterministic report assembly and rendering.

``build_report`` returns the JSON payload (English technical keys, stable
ordering) and ``render_text`` turns that same payload into the human report in
Brazilian Portuguese. Both are pure functions of their arguments, so a test can
assert on the exact structure without touching a real machine.
"""

from __future__ import annotations

from _common import (
    EVIDENCE_LABELS_PT,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SKILL_STATES,
    STATE_LABELS_PT,
    STATE_RANK,
    contains_secret,
    dumps,
    iso,
    iso_local,
)

PT_REASONS = {
    "skill_dir_only": "a skill existe no índice, mas não há registro de uso nesta janela",
    "mention_only": "o nome aparece em texto, o que não comprova carregamento nem execução",
    "skill_view_ok": "carregamento confirmado por chamada skill_view bem-sucedida",
    "skill_view_failed": "apenas tentativas de carregamento, todas com erro observado",
    "skill_manage_only": "edição observada sem carregamento explícito registrado",
    "skill_script_failed": "script da skill foi executado, mas com falha observada",
    "skill_script_ok": "script da skill foi executado com sucesso, sem artefato correlacionado",
    "skill_script_ok_verified_artifact": "script da skill executado com sucesso e artefato verificado",
    "usage_counter_only": "existe apenas contador agregado, sem vínculo com esta sessão",
    "empty_transcript": "nenhuma evidência observável",
}

PT_LIMITATIONS = {
    "no_transcript_evidence": "não há evidência no transcript analisado",
    "no_correlated_artifact": "não foi possível correlacionar um artefato verificável",
    "artifact_check_disabled": "verificação de artefatos desativada nesta execução",
    "out_of_transcript_load": "contador agregado pode vir de fora do transcript analisado",
    "edit_without_load": "edição observada sem carregamento explícito registrado",
    "compacted_messages_present": "a sessão contém mensagens compactadas; parte do histórico foi resumida",
    "window_filtered": "análise limitada a uma janela temporal, não à sessão inteira",
    "child_scope": "evidência de sessão filha; não deve ser atribuída ao agente principal",
    "result_not_observable": "resultado da execução não observável nos registros",
}

PT_UNKNOWNS = {
    "cost_unavailable": "custo: indisponível",
    "duration_unavailable": "duração: não verificável",
    "session_in_progress": "sessão em andamento: contagens e período são parciais",
    "unattributed_tool_results": "resultados de ferramenta sem chamada correspondente na janela",
    "skill_events_without_call": "eventos de skill sem chamada correspondente na janela",
    "usage_counters_are_global": "contadores de .usage.json são globais, não desta sessão",
    "children_excluded": "sessões filhas não foram analisadas (use --include-children)",
    "artifact_check_disabled": "verificação de artefatos desativada",
    "compaction_hides_detail": "trechos compactados não são recuperáveis",
}


def sanitize_tree(obj: object) -> object:
    """Final safety pass: no string in the payload may carry a credential."""
    if isinstance(obj, dict):
        return {key: sanitize_tree(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [sanitize_tree(value) for value in obj]
    if isinstance(obj, str) and contains_secret(obj):
        return "[redacted]"
    return obj


def build_report(*, generated_at: str, session: dict, scopes: list[dict],
                 findings: list[dict], divergences: list[dict],
                 available_names: list[str], usage: dict, index_roots: list[str],
                 sources: dict, options: dict, counts: dict) -> dict:
    """Assemble the JSON payload. Counts come from ``classify.summarise``."""
    started = session.get("started_at")
    ended = session.get("ended_at")
    duration = None
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
        duration = round(float(ended) - float(started), 3)

    period = {
        "started_at": iso(started if isinstance(started, (int, float)) else None),
        "ended_at": iso(ended if isinstance(ended, (int, float)) else None),
        "duration_seconds": duration,
        "source": "state_db:sessions",
    }
    window = {
        "since": options.get("since"),
        "until": options.get("until"),
        "filtered": bool(options.get("since") or options.get("until")),
        "scope": "session",
    }

    limitations: list[str] = []
    unknowns: list[str] = []
    if window["filtered"]:
        limitations.append("window_filtered")
    if any(scope.get("facts", {}).get("compacted_messages") for scope in scopes):
        limitations.append("compacted_messages_present")
    if not options.get("artifact_check", True):
        limitations.append("artifact_check_disabled")
    if duration is None:
        unknowns.append("duration_unavailable")
    cost_status = session.get("cost_status")
    if str(cost_status or "").lower() not in ("known", "actual", "estimated", "confirmed"):
        unknowns.append("cost_unavailable")
    if duration is None:
        unknowns.append("session_in_progress")
    if usage.get("present"):
        unknowns.append("usage_counters_are_global")
    if not options.get("include_children"):
        unknowns.append("children_excluded")
    unattributed = sum(scope.get("facts", {}).get("unattributed_tool_results", 0)
                       for scope in scopes)
    if unattributed:
        unknowns.append("unattributed_tool_results")
    orphan_skill_events = sum(scope.get("facts", {}).get("skill_events_without_call", 0)
                              for scope in scopes)
    if orphan_skill_events:
        unknowns.append("skill_events_without_call")
    if any(finding["state"] == "unverifiable" for finding in findings):
        limitations.append("no_transcript_evidence")

    payload = {
        "schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "analysis": {
            "session_id": session.get("id"),
            "title": session.get("title"),
            "session_source": session.get("source"),
            "scope": "session",
            "window": window,
            "period": period,
            "include_children": bool(options.get("include_children")),
            "children_analysed": [scope["session_id"] for scope in scopes
                                  if scope.get("scope") != "main"],
            "counters_are_global": True,
        },
        "sources": sources,
        "options": {
            "artifact_check": bool(options.get("artifact_check", True)),
            "max_skills": options.get("max_skills"),
            "skills_roots_count": len(index_roots),
            "sources_enabled": options.get("sources_enabled", []),
        },
        "counts": counts,
        "available_skills": available_names,
        "findings": findings,
        "divergences": divergences,
        "limitations": sorted(set(limitations)),
        "unknowns": sorted(set(unknowns)),
        "sessions_analysed": [
            {
                "session_id": scope["session_id"],
                "scope": scope.get("scope"),
                "messages": scope.get("facts", {}).get("messages", 0),
                "active_messages": scope.get("facts", {}).get("active_messages", 0),
                "compacted_messages": scope.get("facts", {}).get("compacted_messages", 0),
                "first_at": iso(scope.get("facts", {}).get("span", {}).get("first_at")),
                "last_at": iso(scope.get("facts", {}).get("span", {}).get("last_at")),
            }
            for scope in scopes
        ],
    }
    return sanitize_tree(payload)


def _fmt_ts(value: str | None) -> str:
    if not value:
        return "não disponível"
    try:
        from _common import parse_iso
        return iso_local(parse_iso(value)) or "não disponível"
    except ValueError:
        return value


def _fmt_state(state: str) -> str:
    return STATE_LABELS_PT.get(state, state)


def _fmt_evidence(item: dict) -> list[str]:
    kind = item.get("kind")
    label = EVIDENCE_LABELS_PT.get(kind, str(kind))
    when = _fmt_ts(item.get("at"))
    line = "    - %s em %s" % (label, when)
    if item.get("tool"):
        line += " (ferramenta: %s)" % item["tool"]
    if item.get("ok") is True:
        line += " — resultado observado: sucesso"
    elif item.get("ok") is False:
        line += " — resultado observado: falha"
    out = [line]
    if item.get("basis"):
        out.append("      base: %s" % item["basis"])
    if item.get("detail"):
        out.append("      referência: %s" % item["detail"])
    if item.get("artifact"):
        out.append("      artefato: %s" % item["artifact"])
    if item.get("excerpt"):
        out.append("      trecho: %s" % item["excerpt"])
    if item.get("error"):
        out.append("      erro: %s" % item["error"])
    if item.get("message_uid"):
        out.append("      mensagem: %s" % item["message_uid"])
    return out


MAX_EVIDENCE_LINES = 4


def _fmt_scope(finding: dict) -> str:
    scope = finding["scope"]
    if scope == "main":
        return "sessão principal"
    if scope == "aggregate":
        return "contador agregado (.usage.json), não atribuível a esta sessão"
    return "sessão filha %s (evidência relatada, não observada pelo agente principal)" % (
        finding.get("session_id"))


def _finding_block(finding: dict, index: int) -> list[str]:
    lines = [
        "%d. %s — %s" % (index, finding["skill"], _fmt_state(finding["state"])),
        "   motivo da classificação: %s" % PT_REASONS.get(
            str(finding["state_reason"]), str(finding["state_reason"])),
        "   escopo: %s" % _fmt_scope(finding),
        "   primeira observação: %s" % _fmt_ts(finding.get("first_seen_at")),
        "   última observação: %s" % _fmt_ts(finding.get("last_seen_at")),
        "   contagens (state_db): carregamentos=%d, execuções=%d (sucesso=%d, falha=%d), edições=%d, menções=%d" % (
            finding["counts"]["loads_ok"], finding["counts"]["invocations"],
            finding["counts"]["invocations_ok"], finding["counts"]["invocations_failed"],
            finding["counts"]["edits"], finding["counts"]["mentions"],
        ),
        "   resultado observado: %s (%s)" % (
            {"succeeded": "sucesso", "failed": "falha", "unknown": "desconhecido"}.get(
                finding["result"]["state"], finding["result"]["state"]),
            finding["result"].get("detail") or "sem detalhe",
        ),
        "   verificação: %s (%s)" % (
            "verificada" if finding["verification"]["state"] == "verified" else "não verificável",
            finding["verification"].get("detail") or "sem detalhe",
        ),
        "   evidências:",
    ]
    if finding["evidence"]:
        shown = finding["evidence"][:MAX_EVIDENCE_LINES]
        for item in shown:
            lines.extend(_fmt_evidence(item))
        remaining = len(finding["evidence"]) - len(shown)
        if remaining > 0:
            lines.append("    - ... e %d outra(s) evidência(s) no JSON (--json)" % remaining)
    else:
        lines.append("    - nenhuma evidência registrada")
    if finding["limitations"]:
        lines.append("   limitações:")
        for code in finding["limitations"]:
            lines.append("    - %s" % PT_LIMITATIONS.get(code, code))
    else:
        lines.append("   limitações: nenhuma")
    return lines


def render_text(payload: dict) -> str:
    """Human report in pt-BR, built only from the JSON payload."""
    analysis = payload["analysis"]
    counts = payload["counts"]
    sources = payload["sources"]
    lines: list[str] = []
    lines.append("RELATÓRIO DE EXECUÇÃO DE SKILLS")
    lines.append("=" * 34)
    lines.append("")
    lines.append("Sessão: %s" % (analysis.get("title") or "(sem título)"))
    lines.append("Identificador: %s" % analysis.get("session_id"))
    lines.append("Origem da sessão: %s" % (analysis.get("session_source") or "desconhecida"))
    lines.append("Período analisado: %s até %s (fonte: %s)" % (
        _fmt_ts(analysis["period"]["started_at"]),
        _fmt_ts(analysis["period"]["ended_at"]),
        analysis["period"]["source"],
    ))
    if analysis["period"]["duration_seconds"] is None:
        lines.append("Duração: não verificável")
    else:
        lines.append("Duração: %.1f s" % analysis["period"]["duration_seconds"])
    window = analysis["window"]
    if window["filtered"]:
        lines.append("Janela: filtrada de %s até %s" % (
            _fmt_ts(window.get("since")), _fmt_ts(window.get("until"))))
    else:
        lines.append("Janela: sessão inteira")
    lines.append("Escopo: sessão principal%s" % (
        "" if not analysis["include_children"] else " + sessões filhas (%d)" % len(
            analysis["children_analysed"])))
    lines.append("Relatório gerado em: %s" % _fmt_ts(payload.get("generated_at")))
    lines.append("")
    lines.append("Fontes de dados")
    db_facts = sources.get("state_db", {})
    lines.append("- state.db: %s (%s)" % (
        "presente" if db_facts.get("present") else "ausente",
        "%s, %s bytes, modificado em %s" % (
            db_facts.get("name", "state.db"), db_facts.get("bytes"),
            _fmt_ts(db_facts.get("modified_at"))) if db_facts.get("present") else "sem dados",
    ))
    usage = sources.get("usage_json", {})
    lines.append("- skills/.usage.json: %s (contadores globais, não por sessão)" % (
        "presente" if usage.get("present") else "ausente"))
    index = sources.get("skills_index", {})
    lines.append("- índice de skills: %d skills conhecidas em %d diretório(s)" % (
        index.get("skills_known", 0), index.get("roots_count", 0)))
    git = sources.get("git", {})
    if git.get("available"):
        lines.append("- git: branch %s, HEAD %s%s" % (
            git.get("branch"), str(git.get("head"))[:12],
            ", working tree com alterações" if git.get("dirty") else ""))
    else:
        lines.append("- git: indisponível (%s)" % git.get("reason", "não fornecido"))
    lines.append("")
    lines.append("Resumo")
    lines.append("- skills disponíveis no índice: %d" % counts["skills_available"])
    transcript_total = counts["findings"] - counts.get("skills_aggregate_only", 0)
    lines.append("- skills com evidência no transcript desta análise: %d" % transcript_total)
    lines.append("- skills com apenas contador agregado (.usage.json): %d" % counts.get(
        "skills_aggregate_only", 0))
    lines.append("- carregadas: %d" % counts["skills_loaded"])
    lines.append("- invocadas: %d" % counts["skills_invoked"])
    lines.append("- execução comprovada: %d" % counts["skills_execution_proven"])
    lines.append("- não verificáveis: %d (dos quais %d apenas com contador agregado)" % (
        counts["skills_unverifiable"], counts.get("skills_aggregate_only", 0)))
    lines.append("")

    main_findings = [f for f in payload["findings"] if f["scope"] == "main"]
    child_findings = [f for f in payload["findings"] if f["scope"].startswith("child:")]
    aggregate_rows = [f for f in payload["findings"] if f["scope"] == "aggregate"]
    lines.append("Skills observadas na sessão principal")
    if not main_findings:
        lines.append("- nenhuma skill observada com evidência suficiente")
    else:
        for position, finding in enumerate(main_findings, start=1):
            lines.extend(_finding_block(finding, position))
            lines.append("")
    if child_findings:
        lines.append("Skills observadas em sessões filhas (evidência relatada, não observada pelo agente principal)")
        for position, finding in enumerate(child_findings, start=1):
            lines.extend(_finding_block(finding, position))
            lines.append("")
    else:
        lines.append("Sessões filhas: nenhuma analisada")
        lines.append("")

    if aggregate_rows:
        lines.append("Contadores agregados (.usage.json) sem vínculo com esta sessão")
        lines.append("Não somar com as contagens do transcript: as fontes divergem.")
        for finding in aggregate_rows:
            counters = finding["counts"]
            lines.append("- %s — não verificável — carregamentos=%d, execuções=%d, edições=%d — última observação: %s" % (
                finding["skill"], counters["loads_ok"], counters["invocations"],
                counters["edits"], _fmt_ts(finding.get("last_seen_at"))))
        lines.append("")

    if payload["divergences"]:
        lines.append("Divergências entre fontes (não somar)")
        for item in payload["divergences"]:
            lines.append("- %s: campo %s — .usage.json=%s, state.db=%s" % (
                item["skill"], item["field"], item["usage_json"], item["state_db"]))
        lines.append("")

    lines.append("Dados comprovados versus desconhecidos")
    lines.append("- Comprovado: classificações com evidência listada acima; artefatos verificados por existência em disco ou commit resolvido no repositório local.")
    if payload["unknowns"]:
        for code in payload["unknowns"]:
            lines.append("- Desconhecido: %s" % PT_UNKNOWNS.get(code, code))
    else:
        lines.append("- Desconhecido: nada registrado como lacuna nesta execução")
    lines.append("")

    lines.append("Limitações")
    if payload["limitations"]:
        for code in payload["limitations"]:
            lines.append("- %s" % PT_LIMITATIONS.get(code, code))
    else:
        lines.append("- nenhuma limitação registrada")
    lines.append("")
    lines.append("Estados possíveis: %s." % ", ".join(
        "%s (%s)" % (STATE_LABELS_PT[state], state) for state in SKILL_STATES))
    lines.append("Regra: visualizar, carregar ou mencionar uma skill nunca conta como execução.")
    return "\n".join(lines) + "\n"


def to_json(payload: dict) -> str:
    return dumps(payload) + "\n"
