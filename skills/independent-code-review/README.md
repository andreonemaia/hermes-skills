# independent-code-review

Obtenha uma revisão de código de um **processo revisor separado**, sem
ferramentas e sem acesso à conversa do autor, configurado para rodar um modelo
diferente do que escreveu o código. O relatório informa se o modelo realmente
foi diferente: quando o revisor configurado roda o mesmo modelo do autor, a
skill relata uma segunda opinião do mesmo modelo, em vez de afirmar
independência de modelo.

## O que é

Uma revisão feita por um processo `hermes chat` separado, com modelo próprio,
contexto limpo e nenhuma ferramenta, que analisa apenas o diff entregue no
briefing. O revisor não vê o resto do repositório, não executa comandos e não
escreve nada.

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
- **zero ferramentas** (`-t none`), então ele só pode raciocinar sobre o diff
  que você entrega;
- uma **execução única e finita** (`--oneshot --source oneshot`), fora dos
  seletores de sessão do Desktop e do TUI.

O resultado é uma revisão que encontra o que o autor não conseguiu ver e que não
tem como tocar no repositório.

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
- Para revisar um diff que depende de contexto que não cabe em um briefing, já
  que o revisor não pode buscar contexto por conta própria.
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
        │  -t none  --ignore-rules     │      diff embedded, no tools
        │  --oneshot --source oneshot  │      no shared context
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
3. **Isolamento de capacidade**: `-t none` não carrega nenhuma ferramenta, então
   o revisor não pode ler o resto do repositório, não pode executar comandos e
   não pode escrever nada. Ele analisa o diff entregue dentro do briefing e nada
   mais. Isso é ausência de capacidade no processo revisor, não um sandbox de
   kernel: a garantia depende de a CLI honrar `-t none` (revalide isso a cada
   atualização da CLI).

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

O revisor é somente leitura por construção: `-t none` o deixa sem nenhuma
ferramenta capaz de escrever em qualquer lugar. A execução ainda é delimitada
com `--in <scratch-dir>` e `--ignore-rules`, como **defesa em profundidade, não
como sandbox**. Depois da execução, o autor verifica que `git status --short`
continua igual.

### Confirmando qual modelo realmente rodou

O revisor pode acabar caindo em um modelo diferente. A skill faz o parse do
evento `system/init` da saída de `--format stream-json`:

```json
{"type":"system","subtype":"init","model":"<actual-model>"}
```

Se `<actual-model>` for diferente do modelo configurado, isso é reportado como
um achado e o autor **não** afirma que o revisor configurado foi usado. O evento
expõe apenas o modelo; o provider é lido da configuração.

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
- **Comportamento dependente de versão.** Os achados sobre `-t none` e
  `--source oneshot` foram validados em uma versão do Hermes. Revalide depois de
  atualizar a CLI.
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
- **Somente leitura significa somente leitura.** O revisor não consegue buscar
  contexto extra sozinho; tudo o que ele precisa tem que estar no briefing.
- **Nenhuma ação do lado do GitHub.** A skill não publica comentários de revisão
  nem aprova pull requests. Para isso, use uma skill dedicada de revisão do
  GitHub, com autorização explícita.
- **A qualidade é limitada pelo briefing.** Um briefing vago produz uma revisão
  vaga.

## Licença

MIT, como o resto deste repositório.
