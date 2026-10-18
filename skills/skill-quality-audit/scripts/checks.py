"""Catalogue of skill-quality-audit checks: the single source of truth for codes.

Standard library only. This module performs no I/O and never inspects a target:
it only declares which checks exist, how each one is decided and how severe a
finding defaults to. ``scripts/audit_skill.py`` emits findings by looking codes up
here, so ``tests/test_catalogue_docs.py`` can prove that no code can reach a
report without being documented in ``references/checks.md`` and ``README.md``
with the same method and the same default severity.

Two methods are kept strictly apart:

* ``structural`` checks are deterministic and machine-decided. The auditor runs
  them and reports a finding, a pass or an explicit ``unverifiable`` limit.
* ``semantic`` checks require agent judgement. The script never decides them: it
  may emit a *signal* (``confidence: "signal"``, ``needs_agent_confirmation``)
  that the agent either confirms as a finding or drops, and the full checklist is
  always printed in the report so a partially automated run cannot look complete.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple

SCHEMA_ID = "skill-quality-audit/v1"
SCHEMA_VERSION = 1

#: Ordered severity ladder. Severity is never derived from a category or from a
#: numeric score: every finding carries a ``severity_justification`` written by
#: the check (structural) or by the agent (semantic).
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITIES = ("info", "low", "medium", "high", "critical")

METHODS = ("structural", "semantic")

#: How the evidence behind a finding was obtained. ``reported`` marks a claim
#: that comes from the audited documents only, ``unverifiable`` marks an item the
#: available evidence cannot settle. Neither is an approval nor a defect.
VALIDATION_STATES = ("executed", "reported", "unverifiable", "not_run")

CATEGORIES = (
    "structure",
    "metadata",
    "documentation",
    "references",
    "tests",
    "consistency",
    "privacy",
    "claims",
    "integrity",
    "semantic",
)


class Check(NamedTuple):
    """One auditable check.

    ``default_severity`` is the severity a finding of this code carries unless
    the check overrides it with a written justification. ``how`` names the
    implementing function for structural checks and states the judgement the
    agent has to make for semantic ones. ``limits`` records what the check
    cannot decide, so the coverage of an audit stays explicit.
    """

    code: str
    title: str
    category: str
    method: str
    default_severity: str
    summary: str
    how: str
    limits: str = ""


CATALOGUE: tuple[Check, ...] = (
    # ------------------------------------------------------------------ structure
    Check(
        code="SQA-001",
        title="SKILL.md ausente",
        category="structure",
        method="structural",
        default_severity="critical",
        summary="O diretório do alvo não contém SKILL.md, portanto não é uma skill publicável.",
        how="_check_skill_md em scripts/audit_skill.py: presença de SKILL.md na raiz do alvo.",
        limits="Um SKILL.md presente não prova que a skill funciona; apenas que o ponto de entrada existe.",
    ),
    Check(
        code="SQA-002",
        title="Frontmatter ausente",
        category="metadata",
        method="structural",
        default_severity="critical",
        summary="SKILL.md não começa com um bloco de frontmatter delimitado por ---.",
        how="_check_frontmatter: procura o bloco delimitado na primeira linha de SKILL.md.",
        limits="Não avalia o conteúdo semântico do frontmatter, apenas a existência do bloco.",
    ),
    Check(
        code="SQA-003",
        title="Frontmatter ilegível",
        category="metadata",
        method="structural",
        default_severity="high",
        summary="O bloco de frontmatter existe mas não pôde ser decodificado pelo backend ativo.",
        how="_check_frontmatter: erro de parse com o backend pyyaml, ou bloco sem par de delimitadores.",
        limits="Com o backend restrito, construções fora da cobertura declarada viram SQA-027 em vez de erro.",
    ),
    Check(
        code="SQA-004",
        title="Chave obrigatória ausente",
        category="metadata",
        method="structural",
        default_severity="critical",
        summary="Falta name, description, version ou platforms no frontmatter.",
        how="_check_required_keys: compara as chaves lidas com a lista exigida pelo repositório.",
        limits="name e description ausentes são critical; version e platforms ausentes são high.",
    ),
    Check(
        code="SQA-005",
        title="Nome divergente do diretório",
        category="consistency",
        method="structural",
        default_severity="critical",
        summary="O campo name do frontmatter não corresponde ao nome do diretório da skill.",
        how="_check_name_directory: compara name com o basename do alvo.",
        limits="Não julga se o nome é bom, apenas se ele é o mesmo identificador usado no diretório.",
    ),
    Check(
        code="SQA-006",
        title="Descrição sem gatilho utilizável",
        category="metadata",
        method="structural",
        default_severity="medium",
        summary="A description não começa com um gatilho autocontido (padrão 'Use when ...').",
        how="_check_description: mede o prefixo de 57 caracteres e procura o prefixo convencional.",
        limits="Um gatilho presente não garante que a descrição descreva o comportamento correto.",
    ),
    Check(
        code="SQA-007",
        title="Metadados do repositório ausentes",
        category="metadata",
        method="structural",
        default_severity="medium",
        summary="metadata.hermes.tags ou metadata.hermes.category não foram encontrados.",
        how="_check_metadata_block: lê metadata.hermes na árvore do frontmatter.",
        limits="Depende da cobertura do backend; com o parser restrito a leitura é limitada a três níveis.",
    ),
    Check(
        code="SQA-008",
        title="Versão fora do formato semântico",
        category="metadata",
        method="structural",
        default_severity="medium",
        summary="O campo version não segue MAJOR.MINOR.PATCH.",
        how="_check_version: expressão regular sobre o valor de version.",
        limits="Valida o formato, não se a versão corresponde ao histórico do repositório.",
    ),
    Check(
        code="SQA-009",
        title="Plataformas inválidas ou vazias",
        category="metadata",
        method="structural",
        default_severity="high",
        summary="platforms não é uma lista não vazia de tokens reconhecidos.",
        how="_check_platforms: tipo do valor mais conjunto de tokens aceitos.",
        limits="Declarar uma plataforma não prova que a skill foi exercitada nela: ver SQA-018.",
    ),
    Check(
        code="SQA-010",
        title="README.md ausente",
        category="documentation",
        method="structural",
        default_severity="medium",
        summary="A skill não traz documentação humana em README.md.",
        how="_check_readme: presença de README.md na raiz do alvo.",
        limits="Não avalia a qualidade do texto, apenas a existência do arquivo.",
    ),
    Check(
        code="SQA-011",
        title="Sinal de idioma da documentação humana",
        category="documentation",
        method="structural",
        default_severity="medium",
        summary="Sinal automático de que o README.md pode não estar em PT-BR, como o repositório exige.",
        how="_check_readme_language: proporção de palavras funcionais portuguesas; o resultado sai como sinal e o agente confirma em SQA-102.",
        limits="Heurística: emite sinal (confiança 'signal'), nunca decide sozinha o idioma do texto.",
    ),
    Check(
        code="SQA-012",
        title="Exemplo em linguagem natural ausente",
        category="documentation",
        method="structural",
        default_severity="medium",
        summary="Nenhum exemplo de pedido em linguagem natural foi encontrado na documentação.",
        how="_check_examples: procura blocos cercados e marcadores de exemplo em SKILL.md e README.md.",
        limits="A presença de um exemplo não prova que ele é executável ou fiel ao procedimento.",
    ),
    Check(
        code="SQA-013",
        title="Referência local quebrada",
        category="references",
        method="structural",
        default_severity="high",
        summary="Um link relativo ou um caminho citado na documentação não existe no alvo.",
        how="_check_references: resolve links markdown e caminhos entre crases contra o sistema de arquivos.",
        limits="Só verifica existência; não confere se o conteúdo referenciado diz o que a documentação afirma. Links em estilo de referência ([texto]: url) não são resolvidos: ficam para a leitura semântica.",
    ),
    Check(
        code="SQA-014",
        title="Caminho absoluto ou que escapa do alvo",
        category="references",
        method="structural",
        default_severity="high",
        summary="A documentação cita caminho absoluto ou caminho que sai da raiz da skill.",
        how="_check_escaping_paths: normaliza cada caminho citado e compara com a raiz do alvo.",
        limits="Placeholders entre <>, % % ou ${ } são tratados como genéricos e não geram achado.",
    ),
    Check(
        code="SQA-015",
        title="Scripts sem testes",
        category="tests",
        method="structural",
        default_severity="high",
        summary="A skill entrega scripts/ mas não tem tests/.",
        how="_check_tests: presença de scripts/ comparada com a de tests/.",
        limits="Não julga a cobertura dos testes existentes: ver SQA-016 e SQA-104.",
    ),
    Check(
        code="SQA-016",
        title="Nenhum arquivo de teste reconhecível em tests/",
        category="tests",
        method="structural",
        default_severity="medium",
        summary="tests/ existe mas nenhum arquivo test_*.py cita unittest ou pytest.",
        how="_check_tests: padrão de nomes em tests/ mais menção textual a unittest/pytest no arquivo.",
        limits=(
            "Sinal textual, não prova de descoberta nem de execução: nenhum teste do alvo "
            "é executado, e um arquivo que apenas menciona o framework conta como menção."
        ),
    ),
    Check(
        code="SQA-017",
        title="Comando documentado inexistente",
        category="consistency",
        method="structural",
        default_severity="high",
        summary="Um comando documentado usa um script do alvo que não existe ou um subcomando que o parser não oferece.",
        how="""_check_documented_commands: cruza caminhos scripts/ usados em invocações com o sistema de arquivos e com o parser.

Formatos reconhecidos como invocação: span de código inline, linha de bloco cercado e linha com prompt, desde que iniciados por interpretador (`python`, `python3`, `py`, `bash`, `sh`, `node`, `npx`, `uv`, `pwsh`, `powershell`) ou por `./`, `$` e `>`. Citação em prosa, mesmo entre crases, não é invocação: fica com SQA-013.""",
        limits="A leitura do parser é textual; subcomandos montados em tempo de execução aparecem como unverifiable. Um caminho inexistente é sinal mesmo que o nome pareça ilustrativo (foo, bar): o nome não decide se o caminho existe. Comandos encontrados no alvo nunca são executados.",
    ),
    Check(
        code="SQA-018",
        title="Plataforma declarada sem evidência",
        category="claims",
        method="structural",
        default_severity="medium",
        summary="Uma plataforma declarada no frontmatter não tem nenhuma evidência de exercício na documentação.",
        how="_check_platform_evidence: procura menções de validação por plataforma nos documentos do alvo.",
        limits="Resultado reportado, não executado: declaração de plataforma não é prova de funcionamento. A evidência é textual: menção negada (não testado, never verified) não conta, e um arquivo de teste que cita a plataforma conta como menção, não como execução.",
    ),
    Check(
        code="SQA-019",
        title="Valor com formato de segredo",
        category="privacy",
        method="structural",
        default_severity="critical",
        summary="Um texto do alvo contém algo com formato de credencial.",
        how="_check_privacy: expressões de chaves, tokens e cabeçalhos de autorização, com valor suprimido. Placeholders de template (`{TOKEN}`), variáveis de ambiente e expressões de código não contam como segredo.",
        limits="Detecção por formato: o achado informa a classe e a localização, nunca o valor. Em tests/ e fixtures/ a confiança cai para sinal e o auditor confirma a origem; a severidade não é reduzida pelo caminho.",
    ),
    Check(
        code="SQA-020",
        title="Dado pessoal com formato reconhecível",
        category="privacy",
        method="structural",
        default_severity="high",
        summary="Um texto do alvo contém caminho de usuário real, e-mail ou identificador pessoal.",
        how="_check_privacy: padrões de caminho de usuário, e-mail e documento, com valor suprimido.",
        limits="Placeholders genéricos são ignorados; o achado informa classe e localização, nunca o valor. Em tests/ e fixtures/ a confiança cai para sinal: o auditor confirma se o dado é real, sem reduzir a severidade pelo caminho.",
    ),
    Check(
        code="SQA-021",
        title="Identificador de sessão exposto",
        category="privacy",
        method="structural",
        default_severity="high",
        summary="Um texto do alvo cita identificador de sessão no formato do Hermes.",
        how="_check_privacy: padrão de identificador de sessão, com valor suprimido.",
        limits="O identificador é suprimido no relatório, mesmo quando aparece em documentação pública. Em tests/ e fixtures/ a confiança cai para sinal: o auditor confirma se o identificador é real, sem reduzir a severidade pelo caminho.",
    ),
    Check(
        code="SQA-022",
        title="Referência privada ou de infraestrutura interna",
        category="privacy",
        method="structural",
        default_severity="medium",
        summary="Um texto do alvo cita host, domínio ou faixa de rede não pública.",
        how="_check_privacy: padrões de domínio interno e de faixas privadas IPv4.",
        limits="Nomes de repositório privado sem formato próprio não são detectáveis: cobrir na revisão semântica.",
    ),
    Check(
        code="SQA-023",
        title="Divergência de versão entre documentos",
        category="consistency",
        method="structural",
        default_severity="medium",
        summary="A versão citada no README diverge da versão do frontmatter.",
        how="_check_version_divergence: compara version do frontmatter com as versões citadas no README.",
        limits="Só compara versões em formato MAJOR.MINOR.PATCH presentes nos textos.",
    ),
    Check(
        code="SQA-024",
        title="Conteúdo provisório publicado",
        category="documentation",
        method="structural",
        default_severity="low",
        summary="A documentação contém marcadores de trabalho inacabado (TODO, TBD, FIXME).",
        how="""_check_placeholders: procura marcadores em texto fora de blocos de código.

Duas formas contam como citação do vocabulário, não como pendência: o marcador sozinho dentro de span inline ou aspas (`TODO`, 'TBD'), e lista entre parênteses com dois ou mais marcadores em linha que nomeia a classe ("marcadores (TODO, TBD, FIXME)").""",
        limits="Citação em prosa, sem crases nem aspas, continua sinalizada: o auditor confirma. Citação em bloco cercado não é avaliada.",
    ),
    Check(
        code="SQA-025",
        title="Alegação de capacidade ou isolamento sem evidência",
        category="claims",
        method="structural",
        default_severity="medium",
        summary="A skill afirma isolamento, somente leitura, ausência de rede ou outra garantia forte.",
        how="_check_capability_claims: sinal automático sobre a alegação; o agente confirma ou descarta em SQA-105. Ficam fora da varredura apenas títulos de seção (a alegação está na frase abaixo) e listas de metadados (tags, keywords, category). Documentos gerados e frases que citam testes, manifestos ou checksums continuam na varredura: nomear uma evidência não comprova a alegação.",
        limits="O sinal não decide se a alegação é verdadeira, apenas onde olhar. Uma linha apoiada em manifesto ou teste permanece reported até que o agente confira a evidência. O auditor não executa o alvo: alegações não executadas permanecem reported ou unverifiable. Blocos cercados ficam fora da varredura: são exemplo de texto, não promessa da skill.",
    ),
    Check(
        code="SQA-026",
        title="Alvo divergiu do manifesto esperado",
        category="integrity",
        method="structural",
        default_severity="critical",
        summary=(
            "O manifesto do alvo difere do esperado: mudou entre o início e o fim da "
            "auditoria, ou divergiu do manifesto de referência passado em "
            "--check-unchanged."
        ),
        how=(
            "audit_skill.py compara o manifesto antes e depois de rodar as verificações "
            "e, com --check-unchanged, compara o manifesto final com a referência."
        ),
        limits=(
            "Cobre a janela desta execução e a referência informada; não detecta "
            "alterações anteriores à auditoria nem divergências contra referências que "
            "não foram passadas."
        ),
    ),
    Check(
        code="SQA-027",
        title="Construção de frontmatter fora da cobertura",
        category="metadata",
        method="structural",
        default_severity="info",
        summary="O frontmatter usa construção que o backend restrito não decide (bloco literal, âncora, mapa em fluxo).",
        how="_parse_restricted: registra a construção e a linha em vez de adivinhar o valor.",
        limits="Item não verificável: exige conferência manual ou o backend pyyaml. Não é aprovação nem defeito.",
    ),
    # ------------------------------------------------------------------- semantic
    Check(
        code="SQA-101",
        title="Coerência entre nome, requisitos e procedimento",
        category="semantic",
        method="semantic",
        default_severity="medium",
        summary="Nome, descrição, requisitos declarados e procedimento contam a mesma história.",
        how="Agente: lê SKILL.md e o README e confronta promessa, pré-requisitos e passos.",
        limits="Julgamento humano; o script não emite este código, apenas o lista no checklist.",
    ),
    Check(
        code="SQA-102",
        title="Fidelidade da documentação humana",
        category="semantic",
        method="semantic",
        default_severity="medium",
        summary="O README descreve o comportamento real sem exagerar capacidades.",
        how="Agente: compara o README com o procedimento e com o código entregue.",
        limits="Julgamento humano; depende da leitura integral dos arquivos.",
    ),
    Check(
        code="SQA-103",
        title="Exemplos genéricos contra dados reais",
        category="semantic",
        method="semantic",
        default_severity="medium",
        summary="Exemplos parecem ilustrativos e não dados reais do autor.",
        how="Agente: avalia se caminhos, nomes e saídas dos exemplos são claramente fictícios.",
        limits="Julgamento humano; os padrões automáticos cobrem apenas formatos reconhecíveis.",
    ),
    Check(
        code="SQA-104",
        title="Adequação observável dos testes",
        category="semantic",
        method="semantic",
        default_severity="medium",
        summary="Os testes exercitam comportamento em vez de repetir a implementação.",
        how="Agente: lê os testes e verifica se as asserções têm poder de falha.",
        limits="Julgamento humano; executar os testes do alvo exige autorização explícita.",
    ),
    Check(
        code="SQA-105",
        title="Isolamento alegado contra implementação",
        category="semantic",
        method="semantic",
        default_severity="high",
        summary="Garantias de segurança afirmadas pela skill correspondem ao que o código faz.",
        how="Agente: confronta cada alegação de isolamento com as chamadas de escrita, rede e processo.",
        limits="Julgamento humano; sem execução, a conclusão é reported ou unverifiable.",
    ),
    Check(
        code="SQA-106",
        title="Justificativa de severidade",
        category="semantic",
        method="semantic",
        default_severity="low",
        summary="Cada severidade atribuída se justifica pelo impacto e pelo alcance, não pela categoria.",
        how="Agente: revisa severity_justification dos achados e corrige os que não se sustentam.",
        limits="Julgamento humano; não existe nota numérica universal nem selo automático de segurança.",
    ),
    Check(
        code="SQA-107",
        title="Encaminhamento para revisão independente",
        category="semantic",
        method="semantic",
        default_severity="info",
        summary="Avalia se a independent-code-review é pertinente e o que deve ser entregue a ela.",
        how="Agente: decide o encaminhamento conforme o risco e o tamanho do delta auditado.",
        limits="A auditoria não substitui a revisão independente nem a declara como feita.",
    ),
)

_BY_CODE = {check.code: check for check in CATALOGUE}


def by_code(code: str) -> Check:
    """Return the catalogue entry for ``code`` or raise ``KeyError``."""
    return _BY_CODE[code]


def codes() -> tuple[str, ...]:
    """Every documented code, in catalogue order."""
    return tuple(check.code for check in CATALOGUE)


def structural_codes() -> tuple[str, ...]:
    """Codes the script may emit as findings or as explicit limits."""
    return tuple(c.code for c in CATALOGUE if c.method == "structural")


def semantic_codes() -> tuple[str, ...]:
    """Codes only the agent may emit, after reading the target."""
    return tuple(c.code for c in CATALOGUE if c.method == "semantic")


def worst_severity(severities: Iterable[str]) -> str | None:
    """Highest severity in ``severities``, or ``None`` for an empty sequence.

    An unknown severity is a programming error, not a value to ignore: silently
    dropping it would let a mistyped severity lower the reported worst case.
    """
    ranked = list(severities)
    unknown = sorted({s for s in ranked if s not in SEVERITY_ORDER})
    if unknown:
        raise ValueError(f"unknown severity: {', '.join(unknown)}")
    if not ranked:
        return None
    return max(ranked, key=lambda s: SEVERITY_ORDER[s])


def at_least(severity: str, threshold: str) -> bool:
    """True when ``severity`` ranks at or above ``threshold``."""
    return SEVERITY_ORDER[severity] >= SEVERITY_ORDER[threshold]
