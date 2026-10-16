# independent-code-review

Obtenha uma revisão de código de um **processo revisor separado**, com a menor
superfície de ferramentas disponível e sem acesso à conversa do autor,
configurado para rodar um modelo diferente do que escreveu o código. O
relatório informa se o modelo realmente foi diferente: quando o revisor
configurado roda o mesmo modelo do autor, a skill relata uma segunda opinião do
mesmo modelo, em vez de afirmar independência de modelo.

## O que é

Uma revisão feita por um processo `hermes chat` separado, com modelo próprio,
contexto limpo e sem os toolsets de plataforma (arquivo, terminal), que analisa
apenas o diff entregue no briefing. O revisor não vê o resto do repositório e,
por requisito explícito do briefing, não usa as ferramentas de plugin que ainda
restam no processo (`references/toolset-contract.md`).

O problema que ela resolve: um agente revisando a própria mudança no próprio
contexto é um revisor fraco. Ele já "sabe" o que pretendia, então relê o mesmo
diff do mesmo jeito e deixa passar os mesmos erros. Carregar uma segunda opinião
dentro da mesma sessão não resolve, porque o contexto do autor (as suposições, o
plano, os erros anteriores) continua ali e ancora o revisor.

A skill resolve isso tornando o revisor uma execução realmente separada:

- um **processo `hermes chat` separado**;
- um **modelo separado** (o configurado em `auxiliary.review`; se essa chave não
  estiver configurada, a skill para e pergunta, em vez de fingir que existe um
  revisor);
- um **contexto limpo** (`--ignore-rules`, sem conversa compartilhada);
- **a menor superfície de ferramentas entre as opções medidas** (`-t ""`, que
  resolve para os toolsets de plugin), mais um briefing que estabelece o requisito
  de não usar ferramenta alguma; nenhuma combinação de flags testada zera os
  toolsets nesta build (veja `references/toolset-contract.md`);
- uma **execução única e finita** (`--oneshot --source oneshot`), fora dos
  seletores de sessão do Desktop e do TUI.

O resultado é uma revisão que encontra o que o autor não conseguiu ver, com o
revisor obrigado pelo briefing a não alterar o repositório e verificado depois da
execução dentro da cobertura declarada.

## Quando usar

- Antes de abrir ou integrar um pull request que você mesmo escreveu.
- Depois de uma sessão longa, quando as suas próprias suposições já não valem
  como revisão.
- Para revisar mudanças em staging ou ainda não preparadas, sem commitá-las.
- Para revisar um branch contra a sua base.
- Quando você quer uma segunda opinião com um modelo diferente do autor.

Quando **não** usar:

- Para postar comentários no GitHub ou aprovar pull requests. A skill não faz
  nenhuma ação do lado do GitHub.
- Para revisar um diff que depende de contexto que não cabe em um briefing: o
  briefing é a única fonte de contexto permitida, e o revisor é instruído a não
  buscar contexto por conta própria.
- Como substituto de testes automatizados.

## Exemplo rápido de uso

```
Revise o PR 42 deste repositório de forma independente.
```

O agente lê `auxiliary.review` para obter provider e modelo, busca o diff com
`gh pr view 42 --json ...` e `gh pr diff 42`, roda o revisor isolado, confirma o
modelo pelo evento de inicialização, valida os achados e relata o resultado, sem
comentar nada no GitHub.

## Como funciona

### O fluxo

```
        ┌──────────────────────┐
        │  main agent (author) │
        └──────────┬───────────┘
                   │ 1. read auxiliary.review  -> provider + model
                   │ 2. acquire diff (read-only)
                   │    gh pr diff / git diff
                   ▼
        ┌──────────────────────────────┐
        │ separate hermes chat process │   3. brief file (--query-file)
        │  -t ""    --ignore-rules     │      diff embedded; brief asks for a
        │  --oneshot --source oneshot  │      review-only pass, no shared context
        └──────────┬───────────────────┘
                   │ 4. stream-json: init event -> actual model
                   │    review text + graded findings
                   ▼
        ┌──────────────────────┐
        │  main agent validates│  5. confirmed / rejected / inconclusive
        │  and reports         │     (does not auto-apply fixes)
        └──────────────────────┘
```

### Por que o contexto do autor não é compartilhado

Todo o valor está no isolamento. São três barreiras:

1. **Isolamento de processo**: o revisor é um `hermes chat` novo, não um
   subagente dentro da sessão do autor.
2. **Isolamento de contexto**: `--ignore-rules` impede que memória,
   `AGENTS.md`/`SOUL.md` e skills pré-carregadas sejam injetados, então o
   revisor vê apenas o briefing e o diff.
3. **Isolamento de capacidade (parcial, medido)**: nesta build nenhuma combinação
   de flags testada deixa o revisor com zero toolsets. `-t none` é recusado no
   lançamento (`invalid_params`, exit 1) antes de qualquer resposta do modelo (0 tokens),
   `--safe-mode` falhou antes de qualquer resposta do modelo (0 tokens) e `-t ""`
   resolve para os toolsets de plugin, a menor superfície utilizável entre as
   opções medidas.
   O briefing estabelece um **requisito**: revisar apenas o diff entregue, sem
   editar, executar ou publicar nada. As ferramentas de plugin continuam
   carregadas no processo, e é por isso que a skill confere depois da execução,
   com limites declarados: classifica o registro de chamadas (chamadas
   detectadas, ausência observada com cobertura declarada, ou inconclusivo) e
   compara `git status --short`. Essas verificações são posteriores ao fato e
   valem dentro da cobertura declarada: não impedem uma chamada, não provam
   ausência de efeitos externos e não tornam o processo um processo sem
   ferramentas. Um diretório separado não é sandbox.
   Medições e reprodução: `references/toolset-contract.md`.

Qualquer coisa além do diff de que o revisor precise (requisitos, critérios de
aceite, arquitetura) é acrescentada ao briefing de propósito, pelo autor, de
forma explícita.

### Como o diff é obtido

| Fonte | Comandos |
|--------|----------|
| PR do GitHub | `gh pr view <N> --json number,title,state,baseRefName,headRefName,url,mergeable,mergedAt,changedFiles,additions,deletions` e depois `gh pr diff <N>` |
| Branch contra a base | `git diff --stat <base>...HEAD` e depois `git diff <base>...HEAD` |
| Staging | `git diff --cached` |
| Não preparadas | `git diff` |
| Intervalo | `git diff <A>..<B>` |

A obtenção do diff é estritamente somente leitura: nenhum `add`, `commit`,
`checkout`, `stash` ou `reset` acontece durante a coleta.

### Modo somente leitura

O revisor é somente leitura por contrato: o briefing estabelece o requisito de não
editar, executar ou publicar qualquer coisa, e a skill confere depois que nada
mudou. Isso **não** vem de o processo estar sem ferramentas: nesta build nenhuma
combinação de flags testada zera os toolsets (veja
`references/toolset-contract.md`). A barreira é o requisito explícito no briefing
mais as verificações posteriores: classificar o registro de chamadas do stream
(chamadas detectadas, ausência observada com cobertura declarada, ou inconclusivo)
e confirmar que `git status --short` continua igual.

As duas verificações olham a execução depois do fato, dentro da cobertura
declarada. Elas não impedem uma chamada, e um `git status` inalterado cobre apenas
o estado observado da árvore de trabalho: uma chamada a um serviço remoto, ou uma
escrita feita do lado do plugin, deixa a árvore intacta.

A execução ainda é delimitada com `--in <scratch-dir>` e `--ignore-rules`, como
**defesa em profundidade, não como sandbox**.

### Confirmando qual modelo realmente rodou

O revisor pode acabar caindo em um modelo diferente. A skill faz o parse do
evento `system/init` da saída de `--format stream-json`:

```json
{"type":"system","subtype":"init","model":"<actual-model>"}
```

Se `<actual-model>` for diferente do modelo configurado, isso é reportado como
um achado e o autor **não** afirma que o revisor configurado foi usado. O evento
expõe apenas o modelo; o provider é lido da configuração.

### Classificando o registro de chamadas

A skill não trata "nenhum evento de chamada" como prova de que nenhuma ferramenta
foi usada. Ela classifica o registro em três resultados e reporta a cobertura
junto:

- **chamadas detectadas**: ao menos um evento `tool_use`/`tool_result` no stream,
  ou uma linha `tool` / `tool_call_count` diferente de zero no armazenamento de
  sessões;
- **ausência observada com cobertura declarada**: todas as linhas foram lidas como
  objetos JSON, os eventos de moldura estavam em posição (o `system`+`init` como
  primeiro evento e um único `result` como último) e nenhum evento de chamada
  apareceu;
- **inconclusivo**: stream ausente, vazio, ilegível, não decodificável (UTF-8
  inválido), truncado (só uma truncagem que quebra a moldura é visível) ou sem
  moldura (fora de posição), ou com um tipo de evento
  que o verificador desta skill não foi validado para reconhecer (um valor de
  `type` que não seja string conta como um desses casos). Nunca é reportado como
  "zero ferramentas usadas".

O texto final do revisor pode *mencionar* `tool_use` em prosa, então a skill não
usa `grep` no JSONL cru: ela faz o parse dos eventos e conta por `type`
(`references/toolset-contract.md`).

### Validação dos achados

A saída do revisor é uma alegação, não um fato. O agente principal classifica
cada achado como **confirmado**, **rejeitado** ou **inconclusivo**,
reproduzindo os importantes quando possível. As correções não são aplicadas
automaticamente, a menos que a tarefa peça isso.

## Exemplos adicionais

### Revisar mudanças em staging

```
Rode uma revisão independente no que eu tenho em staging.
```

Fonte do diff: `git diff --cached`. Nada é commitado.

### Revisar mudanças não preparadas

```
Revisão independente das minhas mudanças não preparadas.
```

Fonte do diff: `git diff`.

### Revisar um branch contra a base

```
Revise de forma independente o diff deste branch contra a main.
```

Fonte do diff: `git diff --stat main...HEAD` e `git diff main...HEAD`.

## Instalação e requisitos

Veja [`../../docs/installation.md`](../../docs/installation.md). Versão curta:
copie este diretório para a pasta de categoria `software-development/` do seu
diretório de skills do Hermes.

```
<hermes-data-dir>/skills/software-development/independent-code-review/
```

Depois confirme com `hermes skills list`.

A skill precisa de um revisor configurado em `auxiliary.review` (provider,
modelo e, opcionalmente, `reasoning_effort`). Sem essa configuração, a skill
informa a ausência em vez de fingir ser independente.

## Limitações

- **Testada apenas no Windows.** Esta versão declara `platforms: [windows]`.
  Não presuma comportamento em Linux ou macOS que não foi testado.
- **Comportamento dependente de versão, configuração e plugins.** A resolução de
  toolsets (veja `references/toolset-contract.md`) e os achados sobre
  `--source oneshot` foram medidos em uma versão do Hermes, um provider, um
  modelo, uma configuração com o seu conjunto de plugins e o caminho de lançamento
  da CLI. Revalide depois de atualizar a CLI, depois de mudar `config.yaml` ou os
  plugins relevantes e antes de reusar os números em outro perfil: não presuma que
  a lista de plugins será a mesma em outros perfis.
- **A verificação de chamadas tem cobertura declarada, não é uma prova de
  impossibilidade.** Stream ausente, vazio, ilegível, não decodificável, truncado
  (só uma truncagem que quebra a moldura é visível) ou sem moldura (fora de
  posição) resulta em **inconclusivo**, não em "zero
  ferramentas usadas". E `git status`
  inalterado comprova apenas o estado observado da árvore de trabalho: não
  comprova ausência de efeitos externos, como uma chamada a um serviço remoto ou
  uma escrita do lado do plugin.
- **Oculta do seletor não é oculta do armazenamento.** As execuções one-shot
  ficam fora dos seletores de sessão do Desktop, mas `hermes sessions list`
  continua mostrando as sessões, e `hermes -c` pode continuar a última delas. A
  skill nunca apaga sessões para contornar isso.
- **O diff é persistido em dois lugares.** O diff viaja dentro do briefing, logo
  é gravado no armazenamento de sessões do Hermes (veja o item acima) e em
  `<scratch-dir>/review.jsonl`. Se o diff puder conter segredos, trate os dois
  como sensíveis: limpe o diretório de scratch depois da revisão e decida
  explicitamente se a sessão deve ser removida
  (`hermes sessions delete <id> --yes`). Nada é apagado automaticamente.
- **Somente leitura é um requisito do briefing.** O revisor é instruído a não
  buscar contexto extra: o que ele precisa tem que estar no briefing. Como os
  toolsets de plugin seguem carregados no processo, a skill confere depois da
  execução se ele os usou (veja acima).
- **Nenhuma ação do lado do GitHub.** A skill não publica comentários de revisão
  nem aprova pull requests. Para isso, use uma skill dedicada de revisão do
  GitHub, com autorização explícita.
- **A qualidade é limitada pelo briefing.** Um briefing vago produz uma revisão
  vaga.

## Licença

MIT, como o resto deste repositório.
