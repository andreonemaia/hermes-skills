# Exemplo de uso e de resultado

Exemplo sanitizado, produzido a partir de uma execução real da skill em modo
somente leitura sobre um alvo local. Nenhum valor do alvo é reproduzido: nomes
de arquivos foram mantidos apenas quando genéricos, e os achados de privacidade
aparecem com classe e comprimento, como o scanner os emite.

## Comando

```bash
python scripts/audit_skill.py manifest --target ../alvo --out before.json
python scripts/audit_skill.py audit --target ../alvo \
  --format json --out report.json --check-unchanged before.json
```

## Resultado

```json
{
  "target": "../alvo",
  "exit_code": 1,
  "frontmatter_backend": "pyyaml",
  "target_unchanged": true,
  "matches_reference": null,
  "manifest_unreadable": [],
  "manifest_diff": {
    "identical": true,
    "complete": true,
    "added": [],
    "changed": [],
    "removed": [],
    "unverifiable": [],
    "digest_before": "<sha256 do manifesto inicial>",
    "digest_after": "<sha256 do manifesto final>"
  },
  "coverage": {
    "entry_count": 14,
    "skipped": [],
    "text_files": [
      "README.md",
      "SKILL.md",
      "references/schema.md",
      "scripts/_common.py",
      "scripts/checkpoint.py",
      "tests/test_hardening.py"
    ]
  },
  "summary": {
    "total": 7,
    "worst": "critical",
    "by_severity": {"critical": 2, "high": 2, "medium": 3, "low": 0, "info": 0}
  },
  "findings": [
    {
      "id": "SQA-019",
      "check": "Credencial ou segredo em potencial",
      "category": "privacy",
      "method": "structural",
      "severity": "critical",
      "severity_justification": "credencial com formato reconhecível; o caminho de teste reduz a confiança, não o impacto",
      "file": "tests/test_exemplo.py",
      "line": 88,
      "location": "tests/test_exemplo.py:88",
      "evidence": "classe credential_assignment; valor suprimido (comprimento 29)",
      "impact": "se o valor for real, a credencial publicada junto com a skill pode ser usada por terceiros",
      "recommendation": "confirmar a origem do valor; se for real, revogar e substituir por placeholder",
      "validation": "executed",
      "confidence": "signal",
      "needs_agent_confirmation": true,
      "target": null
    },
    {
      "id": "SQA-018",
      "check": "Plataforma declarada sem evidência",
      "category": "structure",
      "method": "structural",
      "severity": "medium",
      "severity_justification": "declaração de plataforma sem nenhuma menção a validação nessa plataforma",
      "file": "SKILL.md",
      "line": 6,
      "location": "SKILL.md:6",
      "evidence": "platforms declara 'windows' e nenhum documento menciona validação nessa plataforma",
      "impact": "quem instala em outra plataforma assume suporte que não foi demonstrado",
      "recommendation": "registrar onde a skill foi exercitada ou restringir a declaração",
      "validation": "executed",
      "confidence": "confirmed",
      "needs_agent_confirmation": false,
      "target": null
    }
  ],
  "checks_run": [
    {"code": "SQA-019", "status": "executed", "findings": 2},
    {"code": "SQA-023", "status": "not_run", "reason": "README.md não cita versão: divergência não avaliada", "findings": 0}
  ],
  "semantic_checklist": [
    {"code": "SQA-103", "question": "Exemplos parecem ilustrativos e não dados reais do autor?", "validation": "unverifiable", "evidence": null}
  ]
}
```

## Como ler este resultado

- `target_unchanged` e `manifest_diff` provam que a janela de auditoria não
  alterou o alvo. Não provam ausência de efeitos externos.
- `matches_reference` é `null` porque esta execução não usou
  `--check-unchanged`: "não comparado" não é "igual". `unverifiable` lista
  arquivos cujo conteúdo não pôde ser lido; quando não está vazio,
  `target_unchanged` fica `null` (inconclusivo), nunca `true`.
- O achado `SQA-019` é `signal` porque o arquivo está em `tests/`, mas a
  severidade continua `critical`: o caminho muda a confiança, não o impacto. Ele
  só fecha depois que uma pessoa confirma se o valor é sintético.
- `SQA-018` é `confirmed` porque o script observou diretamente a declaração sem
  evidência correspondente.
- `SQA-023` aparece como `not_run`: a verificação não pôde ser feita e isso é
  registrado como lacuna, não como aprovação.
- O item semântico fica `unverifiable` quando a evidência lida não decide a
  pergunta. Quem audita declara o motivo em vez de marcar como aprovado.
