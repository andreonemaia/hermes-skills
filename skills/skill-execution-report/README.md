# skill-execution-report

Descubra **quais skills uma sessão do Hermes realmente usou**, com base em
evidências somente leitura, e não em menções ou contadores soltos.

O relatório usa seis estados, na mesma escada de classificação:

| Estado | O que significa | Evidência mínima |
|---|---|---|
| `available` | a skill existe no índice, mas não há registro de uso em escopo | vocabulário documental: **nunca é emitido como achado**; aparece em `available_skills` e `counts.skills_available` |
| `discovered` | o nome aparece nos registros analisados | menção textual, `skill_view` com erro, edição via `skill_manage` |
| `loaded` | o conteúdo da skill foi recuperado | chamada `skill_view` com sucesso observado |
| `invoked` | houve uma chamada atribuível à skill, qualquer que tenha sido o resultado | script da skill ou `skill_manage` observado; falha ou resultado desconhecido continuam sendo `invoked`, nunca sucesso |
| `execution_proven` | execução bem-sucedida **com** artefato verificável | sucesso observado **mais** `artifact_file` ou `artifact_commit` verificado; inalcançável com `--no-artifact-check` |
| `unverifiable` | existe um sinal, mas ele não é atribuível a um estado verificado | contadores agregados sem vínculo com a sessão analisada; nunca apresentado como prova |

Além disso, o relatório nunca inventa o que não pode ver: custo, duração e
resultado aparecem como **desconhecidos** quando as fontes não os trazem, e
contadores de fontes diferentes (`.usage.json` e `state.db`) **nunca são somados**.

## Quando usar

- Auditar uma sessão antes de publicar um transcript ou escrever um post-mortem.
- Verificar se uma skill foi realmente exercitada ou apenas carregada.
- Checar uma afirmação do tipo "usei a skill X" sem confiar na afirmação.
- Encontrar skills carregadas que nunca foram executadas, ou editadas sem
  carregamento.
- Conferir se os contadores agregados de `.usage.json` batem com o que uma sessão
  específica mostra.

## Quando não usar

- Monitoramento em tempo real de uma sessão em andamento.
- Quando você só quer contagens (use os contadores nativos do Hermes).
- Quando o `state.db` ou a máquina de origem não podem ser lidos.

## Instalação

Copie o diretório da skill para a sua árvore de skills, preservando a pasta de
categoria quando houver uma:

```bash
cp -r skills/skill-execution-report "$LOCALAPPDATA/hermes/skills/software-development/"
```

O destino fica `<skills-dir>/software-development/skill-execution-report`: a pasta
de categoria vem do `category` do frontmatter e é obrigatória.

Requisito único: Python 3.10 ou superior, apenas biblioteca padrão.

## Exemplo pronto para copiar e colar no Hermes

```
Use a skill skill-execution-report para me dizer quais skills a sessão atual realmente usou. Mostre o estado de cada uma (carregada, invocada ou execução comprovada) e liste o que não pôde ser verificado.
```

Variações úteis:

```
Rode skill-execution-report na sessão latest com --include-children e me explique as divergências entre .usage.json e state.db.
```

```
Verifique se a skill independent-code-review foi apenas carregada ou de fato executada na sessão mais recente.
```

```
Gere o relatório da sessão 20261012_080613 em JSON, sem verificar artefatos, e não inclua trechos privados na resposta.
```

## Uso direto da CLI

Execute a partir do diretório da skill por conveniência de caminhos. Os imports
entre os módulos funcionam porque o Python coloca o diretório do próprio script
(`scripts/`) no `sys.path` — não por causa do diretório de trabalho:

```bash
cd "$LOCALAPPDATA/hermes/skills/software-development/skill-execution-report"

python scripts/report.py --help
python scripts/report.py --session latest
python scripts/report.py --session latest --json
python scripts/report.py --session latest --include-children --repo C:/caminho/do/repo
python scripts/report.py --session latest --json --out relatorio.json
python scripts/report.py --session latest --no-usage --no-artifact-check
```

| Opção | Efeito | Padrão |
|---|---|---|
| `--session REF` | id completo, prefixo único ou `latest` | `latest` |
| `--since` / `--until` | janela de análise (ISO-8601 ou epoch) | sessão inteira |
| `--include-children` | inclui sessões filhas em escopo separado | desligado |
| `--db PATH` | caminho do `state.db` | `HERMES_HOME/state.db` |
| `--usage PATH` / `--no-usage` | caminho do `skills/.usage.json` / não consultá-lo | `HERMES_HOME/skills/.usage.json` |
| `--skills-root PATH` | raiz extra de skills para o índice (repetível; se dois roots tiverem a mesma skill, o primeiro vence) | `HERMES_HOME/skills` |
| `--repo PATH` | repositório git local para verificar commits | `git_repo_root` da sessão |
| `--no-artifact-check` | não verifica arquivos produzidos nem commits | verificação ligada |
| `--json` | saída JSON estruturada | texto em PT-BR |
| `--out PATH` | grava o relatório também em arquivo | apenas stdout |
| `--max-skills N` | limite da lista de skills disponíveis no JSON | `200` |

## Como ler a saída

1. **Código de saída primeiro**: `0` relatório com achados, `1` nenhum achado (a
   análise correu, mas não há atividade de skill no escopo), `2` fonte ilegível
   ou corrompida (também usado para falha interna, sempre com mensagem em stderr
   e sem traceback), `3` fonte ausente (inclusive prefixo de sessão ambíguo, que
   nunca é adivinhado), `4` erro de uso (argumentos inválidos, incluindo `SER_NOW`
   inválido), `5` análise ok mas `--out` não pôde ser gravado. Os códigos `2`, `3`
   e `4` são erros: nada é impresso em stdout.
2. **`counts`** resume os estados e nunca soma fontes diferentes.
3. **`findings`** traz um item por skill e por escopo, com `state`,
   `state_rank`, `state_reason`, a lista de `evidence` e as `limitations`. O
   `state_reason` diz por que o estado foi escolhido: `skill_script_ok` (script
   executado com sucesso observado, sem artefato correlacionado),
   `skill_script_ok_verified_artifact` (sucesso com artefato verificado) e
   `skill_script_result_unknown` (invocação observada, resultado desconhecido —
   nunca sucesso implícito), entre outros.
4. **`divergences`** mostra cada campo em que `.usage.json` e `state.db`
   discordam, para que ninguém produza um total errado somando os dois.
5. **`unknowns`** e **`limitations`** explicam o que não foi possível saber.
   Nos `unknowns`: `cost_unavailable`, `duration_unavailable`,
   `session_in_progress`, `usage_counters_are_global`, `children_excluded`,
   `unattributed_tool_results`, `skill_events_without_call` e
   `compaction_hides_detail`. Nas `limitations`: `no_transcript_evidence`,
   `no_correlated_artifact`, `artifact_check_disabled`, `out_of_transcript_load`,
   `edit_without_load`, `compacted_messages_present`, `window_filtered`,
   `child_scope` e `result_not_observable`.
6. **Saída sempre em UTF-8.** Tanto o JSON quanto o texto em PT-BR são emitidos
   como UTF-8 com `errors="replace"`, independentemente do locale da máquina:
   um caractere que o encoding local não representa nunca derruba o relatório
   (ele é substituído por `?`) e a saída redirecionada para arquivo ou pipe tem
   os mesmos bytes em qualquer plataforma. Por isso, ao ler a saída por pipe,
   interprete-a como UTF-8.

## Garantias

- **Somente leitura.** O `state.db` é aberto com `mode=ro` e `PRAGMA
  query_only`; `.usage.json` é apenas lido; nenhum contador nativo é alterado.
- **Sem rede.** Nenhuma chamada externa é feita e nada é enviado para fora.
- **Sem execução.** Comandos encontrados em um transcript não são executados.
- **Sem vazamento.** Trechos são truncados e redigidos; caminhos absolutos com o
  nome da conta e identificadores de sessão citados em trechos viram `<home>` e
  `<session>`. A única exceção é o identificador da sessão analisada, exibido no
  cabeçalho porque é o próprio objeto do relatório.
- **Determinístico com o relógio fixado.** A ordenação é sempre estável; com
  `SER_NOW` definido (usado nos testes), timestamps e `generated_at` também são
  reproduzíveis, e o relatório humano é derivado do mesmo JSON. Sem `SER_NOW`,
  `generated_at` vem do relógio do sistema e `clock_source` informa qual dos dois
  foi usado.

## Testes

Suíte hermética, sem rede e sem acessar sessões reais:

```bash
cd "$LOCALAPPDATA/hermes/skills/software-development/skill-execution-report"
python -m unittest discover -s tests -v
```

## Limitações conhecidas

- Voltada para Windows: os padrões POSIX existem no código, mas são testados
  apenas no Windows.
- Depende do esquema do `state.db` do Hermes (tabelas `sessions` e `messages` e o
  vínculo de sessão filha). Esquema incompatível é relatado como problema de
  fonte, nunca adivinhado.
- O estado `available` existe no vocabulário, mas a v0.1 não emite achados
  `available` por skill: a disponibilidade aparece em `available_skills` e
  `counts.skills_available`.
- Custo e duração por skill não existem nas fontes e são sempre relatados como
  desconhecidos.
- Metadados públicos de repositório (nome de usuário/organização do GitHub em
  URLs de PR, commits e mensagens) não são redigidos: são identidade pública do
  projeto, não dado local da máquina. Caminhos absolutos e credenciais, sim.

## Referências técnicas

- [`references/schema.md`](references/schema.md): contrato do JSON `skill-execution-report/v1`.
- [`references/evidence-rules.md`](references/evidence-rules.md): regras de classificação, atribuição de evidência, redação e desempenho.
