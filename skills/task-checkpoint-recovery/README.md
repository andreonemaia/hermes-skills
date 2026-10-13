# task-checkpoint-recovery

Checkpoints estruturados e revalidados para tarefas longas de agente.

Uma tarefa longa de agente é interrompida: compactação de contexto, sessão nova,
hand-off, loop interrompido. O que costuma se perder não são os arquivos (o git
tem) nem a conversa (a sessão tem), mas o *estado operacional*: quais passos
estão prontos, quais decisões foram tomadas e por quê, o que está bloqueado e
quais efeitos colaterais já aconteceram no mundo externo. O custo dessa perda é
refazer trabalho e, pior, repetir um efeito como um push, um merge ou um
download.

Esta skill guarda esse estado em um pequeno artefato JSON dentro do projeto e,
principalmente, **revalida cada afirmação contra o projeto antes de agir com base
nela**.

## O que é

Um checkpoint é uma alegação sobre o passado. O comando `verify` revalida essa
alegação contra o git e o sistema de arquivos e reporta divergências, em vez de
confiar no arquivo. O registro de operações é append-only e guarda *alegações*
sobre efeitos colaterais; `check-op` é o portão que decide se uma operação com
efeito pode rodar, para que um agente retomado não faça push duas vezes. Nada é
escrito fora de `<project>/.hermes/checkpoints/<slug>/`, nenhum segredo é
persistido, e um checkpoint corrompido só é colocado em quarentena depois que
existe um snapshot válido do histórico.

## Quando usar

- Antes de encerrar uma sessão no meio de uma tarefa com vários passos.
- Antes de uma operação com efeito colateral (push, merge, download, deploy) que
  não pode ser repetida por engano.
- Ao retomar uma tarefa depois de compactação de contexto, hand-off ou sessão
  nova.
- Quando você precisa registrar por que uma decisão foi tomada, para não
  rediscutir isso depois.
- Quando quer um relatório legível do que já foi feito, do que está bloqueado e
  do que vem a seguir.

## Exemplo rápido de uso

```
Salve um checkpoint desta tarefa antes de eu encerrar a sessão.
```

O agente grava um checkpoint com o objetivo, os passos e o status atual, e depois
pode revalidá-lo e retomar a partir dele em outra sessão.

## Como funciona

### Fluxo básico

```bash
cd skills/task-checkpoint-recovery

# 1. open the checkpoint
python scripts/checkpoint.py --project /path/to/repo save --task my-task \
  --title "Ship feature X" --objective "feature X merged with tests green" \
  --step s1="implement" --step s2="test" --step s3="open PR"

# 2. work, then record progress
python scripts/checkpoint.py --project /path/to/repo save --task my-task \
  --step-status s1=done --step-evidence s1="pytest -q -> 41 passed"

# 3. revalidate and report before resuming
python scripts/checkpoint.py --project /path/to/repo verify --task my-task --json
python scripts/checkpoint.py --project /path/to/repo resume-report --task my-task

# 4. guard a side effect
python scripts/checkpoint.py --project /path/to/repo check-op --task my-task --kind push --target origin/main
```

### Comandos

| Comando | Finalidade |
|---|---|
| `save` | grava ou atualiza o checkpoint (aditivo, com guarda de revisão) |
| `verify` | revalida contra o projeto e reporta achados |
| `resume-report` | relatório legível para retomar a tarefa |
| `record-op` | acrescenta uma alegação de efeito colateral ao registro |
| `check-op` | decide se uma operação com efeito pode rodar |
| `show` / `list` | imprime um checkpoint / lista todos os do projeto |
| `prune` | reduz `history/` (`--dry-run` disponível) |

### Códigos de saída

| Código | Nome | Significado |
|---|---|---|
| 0 | `EXIT_OK` | sucesso; em `verify`, nenhum achado acionável (achados benignos ainda podem ser reportados) |
| 1 | `EXIT_DRIFT` | achados (divergência, obsolescência), qualquer achado sob `--strict`, um `--expect-revision` desatualizado, ou "não repita" vindo de `check-op` |
| 2 | `EXIT_CORRUPT` | checkpoint corrompido ou ilegível, ou um estado que não pode ser confirmado |
| 3 | `EXIT_MISSING` | alvo ausente, ou uma falha não resolvida que precisa de decisão |
| 4 | `EXIT_USAGE` | uso inválido: tipo ou estado desconhecido, segredo recusado, caminho inseguro |

### Achados

`verify` reporta achados por id e severidade (`critical`, `warning`, `info`):

| id | Severidade |
|---|---|
| `history_rewritten` | critical |
| `op_stale_claim` | critical |
| `unsafe_path` | critical |
| `head_diverged` | warning |
| `op_unresolved` | warning |
| `op_planned_landed` | warning |
| `op_unknown` | warning |
| `branch_mismatch` | warning |
| `dirty_tree` | warning |
| `file_missing` | warning |
| `file_drift` | warning |
| `op_planned` | info |
| `op_completed_unrecorded` | info |
| `git_unavailable` | info |
| `head_advanced` | info |
| `stale_checkpoint` | info |

### Arquivos

```
SKILL.md                          instruções voltadas ao agente
README.md                         este arquivo
references/schema.md              schema do checkpoint, campos, códigos de saída, achados
references/hermes-integration.md  como ligar a skill a um fluxo do Hermes
scripts/checkpoint.py             CLI
scripts/_common.py                armazenamento, validação, locking, busca de segredos
scripts/validate.py               validador somente leitura para CI e pre-commit
tests/test_task_checkpoint_recovery.py
tests/test_hardening.py
tests/test_now_flag.py
tests/test_review_regressions.py
```

## Instalação e requisitos

Copie ou crie um link simbólico de `skills/task-checkpoint-recovery/` para o
diretório `skills/` do seu perfil do Hermes, ou mantenha a skill neste
repositório e execute pelo caminho.

Requisitos: Python 3.10+ (somente biblioteca padrão) e, opcionalmente, `git` no
`PATH`.

## Testes

```bash
cd skills/task-checkpoint-recovery
python -m unittest discover -s tests -t tests -v
python scripts/validate.py --project /path/to/repo --digests
```

`unittest` da biblioteca padrão, sem rede e sem `git` obrigatório.

`scripts/validate.py` é um validador somente leitura, pensado para CI e
pre-commit: ele compara os checkpoints existentes em
`<projeto>/.hermes/checkpoints/` com o estado atual do projeto. Por isso, um
checkpoint antigo aparece como divergência, inclusive quando a única diferença é
o fim de linha (por exemplo, com `core.autocrlf=true`). Nesse caso, atualize o
checkpoint com `save` ou rode o validador em um projeto sem checkpoints
obsoletos.

## Limitações

- Escopo de uma única tarefa por vez.
- A revalidação do repositório é feita via git; projetos sem git têm fatos de
  repositório indisponíveis.
- Efeitos que não deixam rastro local continuam sendo apenas alegações.
- Não existe evento de pré-compactação nem de orçamento de iteração no Hermes,
  então a interrupção é tratada com checkpoints preventivos, e não por detecção.
- A skill registra e revalida estado; ela não retoma a tarefa por você nem
  desfaz efeitos colaterais que já aconteceram.

## Licença

MIT.
