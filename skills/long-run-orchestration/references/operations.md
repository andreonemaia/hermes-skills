# Operação e retomada

## Estado local

`apply` grava `.lro/objective.json` ao lado do spec. O arquivo contém:

- `schema_version`;
- `objective_id`;
- `spec_sha256` do contrato normalizado;
- `cards`: mapa `step -> id` nativo;
- `applied_at`.

O estado é um **ledger de retomada**, não a fonte de verdade. A fonte é o spec.
Reexecutar `apply` com o mesmo conteúdo reutiliza os cartões. Se o conteúdo do
spec mudou para o mesmo `objective_id`, o hash diverge e o comando interrompe.

O watchdog lê esse ledger. Sem ele, todos os passos aparecem como `missing` e o
estado geral é `drift`, com alerta; o watchdog não procura cartões por conta
própria. Um ledger presente mas ilegível (JSON inválido, versão desconhecida ou
campos obrigatórios ausentes) também é `drift`, nunca "objetivo não aplicado".

## Arquivos do watchdog

| Arquivo | Conteúdo |
|---|---|
| `.lro/watchdog.jsonl` | Um registro por tick, acrescentado ao final |
| `.lro/alerts.jsonl` | Alertas deduplicados por chave |
| `.lro/watchdog-latest.json` | Último registro completo |

Cada tick avalia o board de novo. Digest constante não congela o tick: regras de
tempo, como `runtime_exhausted`, continuam sendo calculadas.

## Separação de sinais

O relatório distingue:

- `heartbeat`: última batida observada, ou `unobservable`;
- `digest` / `digest_changed`: progresso material, não batida;
- `stall_ticks`: ticks consecutivos com o mesmo digest;
- `elapsed_seconds` e `runtime_exhausted`: tempo decorrido da execução;
- `state`: interpretação do passo (`running`, `running_no_progress`,
  `runtime_exhausted`, `blocked`, `review`, `idle`, `done`, `unknown`).

O watchdog **não** mata processos, não bloqueia cartões e não redespacha trabalho
por estagnação. Ele apenas registra e alerta. Uma ação corretiva é decisão
humana ou de outro componente.

## Gateway indisponível e cartão ausente

Se `show` falha por erro de CLI, o tick marca `gateway_ok=false`, o passo fica
`unknown` e o estado geral vira `gateway_unavailable` (código de saída `5`).
Uma resposta ilegível, sem os campos exigidos, é erro de contrato e não
indisponibilidade: o processo respondeu, mas violou o contrato.

Um `show` que responde `no such task:` é classificado como `not_found`, não como
gateway fora do ar: o board respondeu, mas o cartão não existe mais. Nesse caso o
passo fica `unknown`, o estado geral vira `drift` e o tick alerta. O watchdog não
recria o cartão nem reescreve o ledger.

O próprio watchdog não tem como garantir que o gateway detecte a
indisponibilidade; ele apenas relata o que a CLI respondeu naquele tick.

## Drift

O tick compara marcador, hash, pais e liberação precoce. Um cartão `ready` ou
`running` com pai não concluído é drift, mesmo que o restante do grafo pareça
consistente. O adaptador não repara esse estado: ele interrompe a continuação
com diagnóstico. Não tente "consertar" um cartão `running` editando o board nem
mate processos; reconcilie manualmente ou use outro `objective_id`.

## Limites declarados

- A atomicidade vale para um `create` com seus pais, não para a preparação
  inteira do objetivo, que usa várias chamadas à CLI.
- A garantia de "nenhum filho executável antes do grafo completo" depende de o
  spec permanecer bloqueado até a última criação. Falhas externas depois da
  liberação não podem ser desfeitas pelo adaptador.
- O exemplo em `examples/objective.example.json` é ilustrativo e usa
  placeholders; não o execute sem substituir perfil, provider e modelos.
