# Hermes Skills

Uma coleção pública de skills para o **Hermes Agent** (da Nous Research), feita
com e para a comunidade. Cada skill é um procedimento autocontido e reutilizável
que o Hermes carrega sob demanda para executar uma tarefa específica sempre do
mesmo jeito.

Este repositório também é um experimento aberto: as skills são desenvolvidas
aqui, revisadas de forma independente, versionadas com Git e publicadas por meio
de pull requests.

## O que é uma skill do Hermes Agent?

Uma skill é um diretório que contém um arquivo `SKILL.md` com frontmatter YAML e
um corpo em Markdown. O frontmatter declara o `name` da skill, a `description`
(que diz ao agente *quando* carregá-la), a `version`, as `platforms` suportadas
e metadados opcionais, como tags e categoria. O corpo traz o procedimento em si:
pré-requisitos, comandos exatos, armadilhas conhecidas e um checklist de
verificação.

As skills funcionam por divulgação progressiva: o agente lê apenas o `name` e a
`description` na inicialização e carrega o corpo completo somente quando a skill
é relevante para a tarefa em andamento. Ou seja, uma boa `description` vale
tanto quanto um bom corpo.

## Estrutura do repositório

```
hermes-skills/
├── README.md              # este arquivo
├── LICENSE                # MIT
├── CONTRIBUTING.md        # como contribuir com uma skill
├── CHANGELOG.md           # no estilo Keep a Changelog
├── .gitignore
├── docs/
│   ├── installation.md    # como instalar as skills no Hermes
│   ├── development.md     # como escrever e versionar uma skill
│   └── testing.md         # como validar uma skill antes de publicar
└── skills/
    └── <skill-name>/
        ├── SKILL.md       # obrigatório: a própria skill
        ├── README.md      # opcional: explicação para pessoas
        ├── scripts/       # opcional: scripts auxiliares que a skill executa
        └── tests/         # opcional: testes automatizados para esses scripts
```

## Primeiros passos

1. **Instale uma skill.** Copie o diretório da skill para a pasta de skills do
   seu perfil do Hermes (veja "Instalando uma skill manualmente", mais abaixo).
2. **Peça em linguagem natural.** Não é preciso decorar comandos: descreva o que
   você quer e o Hermes carrega a skill cuja `description` combina com a tarefa.
   Por exemplo: `Revise o PR 42 deste repositório de forma independente.`
3. **Leia o README da skill.** Cada skill tem um README em português com o que
   ela faz, quando usar, exemplos prontos para copiar e limitações.

## Skills disponíveis

| Skill | O que faz | Plataformas | Versão |
|-------|-----------|-------------|--------|
| [`independent-code-review`](skills/independent-code-review/) | Faz a revisão de código com um processo revisor separado, isolado e estritamente somente leitura. Cobre pull requests do GitHub, diffs de branch, mudanças em staging e mudanças não preparadas. | windows | 1.0.0 |
| [`comfyui-model-manager`](skills/comfyui-model-manager/) | Inventaria, verifica e baixa arquivos de modelo do ComfyUI a partir de um manifesto SHA-256, e valida workflows estaticamente antes de executá-los. Complementa a skill oficial `comfyui`. | windows | 0.1.0 |
| [`task-checkpoint-recovery`](skills/task-checkpoint-recovery/) | Persiste e revalida o estado lógico e operacional de uma tarefa longa (objetivo, passos, decisões, bloqueios, próximas ações e um registro das operações com efeito colateral), para que ela possa ser interrompida e retomada sem perder decisões nem repetir trabalho já feito. Complementa as sessões nativas, os checkpoints de arquivo e a compactação de contexto. | windows | 0.1.0 |
| [`skill-execution-report`](skills/skill-execution-report/) | Reconstrói, a partir do `state.db` e do `.usage.json` do Hermes, **o que realmente aconteceu com cada skill** em uma sessão: qual foi apenas mencionada (`discovered`), qual foi carregada (`loaded`), qual foi invocada mesmo sem resultado observado (`invoked`), qual teve execução comprovada por artefato verificado (`execution_proven`) e qual não tem evidência atribuível (`unverifiable`). Estritamente somente leitura, com classificação conservadora e evidência citada. | windows | 0.1.0 |
| [`skill-quality-audit`](skills/skill-quality-audit/) | Audita a prontidão de publicação de uma skill: verificações estruturais determinísticas e somente leitura sobre frontmatter, links, catálogo, plataformas, identificação de comandos, privacidade e alegações de capacidade. Separa o que foi executado e observado do que é apenas sinal, e entrega a avaliação semântica ao agente com evidência citada. | windows | 0.1.0 |

### `independent-code-review`

**Finalidade:** obter uma revisão de código de um **processo revisor separado**,
sem ferramentas e sem acesso à conversa de quem escreveu o código, configurado
para usar um modelo diferente do autor. Quando o modelo revisor acaba sendo o
mesmo do autor, a skill relata isso em vez de fingir independência.

**Exemplo de pedido:**

```
Revise o PR 42 deste repositório de forma independente.
```

**Detalhes:** [README da skill](skills/independent-code-review/README.md)

### `comfyui-model-manager`

**Finalidade:** descobrir uma instalação do ComfyUI, inventariar os arquivos de
modelo em disco, conferir um manifesto contra eles e baixar apenas o que falta,
**sempre depois de conferir o digest SHA-256**. Também valida um workflow contra
um servidor em execução antes de gastar tempo de GPU.

**Exemplo de pedido:**

```
Use comfyui-model-manager para me dizer quais modelos já estão instalados e quais checkpoints estão duplicados.
```

**Detalhes:** [README da skill](skills/comfyui-model-manager/README.md)

### `task-checkpoint-recovery`

**Finalidade:** guardar o estado de uma tarefa longa (o que já foi feito, quais
decisões foram tomadas e por quê, o que está bloqueado e quais efeitos colaterais
já aconteceram) em um pequeno arquivo JSON dentro do projeto, e **revalidar cada
afirmação contra o projeto** antes de agir com base nela. É o que evita repetir
um `push`, um merge ou um download depois de uma interrupção.

**Exemplo de pedido:**

```
Salve um checkpoint desta tarefa antes de eu encerrar a sessão.
```

**Detalhes:** [README da skill](skills/task-checkpoint-recovery/README.md)

### `skill-execution-report`

**Finalidade:** responder, com evidência em mãos, à pergunta "essa skill foi
mesmo executada?" Lendo o `state.db` (transcrições) e o `.usage.json`
(contadores) do Hermes em modo **somente leitura**, a skill reconstrói o que
aconteceu com cada skill em uma sessão e separa as situações que costumam ser
confundidas: `discovered` (o nome aparece nos registros), `loaded`
(`skill_view` com sucesso observado), `invoked` (chamada atribuível à skill,
mesmo com resultado desconhecido ou falha) e `execution_proven` (sucesso com
artefato verificado). `available` é vocabulário documental — nunca aparece como
achado — e `unverifiable` marca o sinal que existe mas não é atribuível a um
estado verificado. Nada é inferido sem evidência atribuível: resultado
desconhecido nunca vira sucesso implícito.

**Exemplo de pedido:**

```
Rode skill-execution-report na sessão atual e me diga quais skills foram realmente executadas, com as evidências.
```

**Detalhes:** [README da skill](skills/skill-execution-report/README.md)

### `skill-quality-audit`

**Finalidade:** responder "esta skill está pronta para publicar?" com achados
rastreáveis em vez de impressão geral. O script roda verificações estruturais
determinísticas em modo **somente leitura** sobre o alvo (frontmatter, links,
coerência entre catálogo e documentação, plataformas declaradas, comandos
citados, privacidade e alegações de capacidade), prova que não alterou nada
comparando um manifesto de conteúdo antes e depois, e entrega ao agente os itens
que exigem julgamento semântico. Cada achado traz severidade justificada,
localização, evidência e situação de validação: o que foi observado, o que é
apenas sinal e o que não pôde ser verificado ficam em categorias separadas.

**Exemplo de pedido:**

```
Audite a skill task-checkpoint-recovery e me diga o que impede a publicação dela.
```

**Detalhes:** [README da skill](skills/skill-quality-audit/README.md)

## Instalando uma skill manualmente

As skills ficam em um diretório `skills/` dentro do diretório de dados do
Hermes.

1. Copie o diretório da skill para a sua árvore de skills, mantendo a pasta de
   categoria:

   ```bash
   # Linux / macOS
   cp -r skills/independent-code-review ~/.hermes/skills/software-development/
   ```

   ```powershell
   # Windows (PowerShell)
   Copy-Item -Recurse skills\independent-code-review "$env:LOCALAPPDATA\hermes\skills\software-development\"
   ```

2. Confirme que o Hermes encontrou a skill:

   ```bash
   hermes skills list
   ```

3. A skill já está disponível. Veja `docs/installation.md` para os detalhes,
   incluindo os caminhos exatos do perfil em cada plataforma.

## Usando uma skill

As skills são carregadas automaticamente quando a `description` combina com a
tarefa, ou podem ser invocadas explicitamente como comando de barra, usando o
nome da skill, por exemplo:

```
/independent-code-review
```

Leia o `README.md` da própria skill para uma explicação voltada a pessoas, com
exemplos práticos.

## Compatibilidade e versionamento

- Cada skill declara as `platforms` em que foi realmente testada. **Nenhuma
  skill deste repositório afirma suporte a uma plataforma em que não foi
  testada**: uma plataforma não testada é deliberadamente omitida, nunca
  adivinhada.
- Cada skill é versionada de forma independente com [Semantic Versioning](https://semver.org/).
  O campo `version` no `SKILL.md` é a fonte da verdade.
- Mudanças em uma skill são registradas no `CHANGELOG.md` do repositório.
- Algumas skills documentam o comportamento de uma **versão específica da CLI do
  Hermes**. Nesses casos, a skill informa a versão testada e pede revalidação
  quando a CLI mudar. Um comportamento validado em uma versão nunca é
  apresentado como garantia permanente de API.

## Contribuindo

Contribuições são bem-vindas. Leia o [CONTRIBUTING.md](CONTRIBUTING.md) antes de
começar. Em resumo: uma skill por diretório, `SKILL.md` obrigatório com
frontmatter válido, nenhum segredo e nenhum dado pessoal, apenas plataformas
realmente testadas, documentação humana em português brasileiro (PT-BR) e
mudanças propostas por branch e pull request que passa por uma revisão
independente antes do merge.

## Licença

[MIT](LICENSE).
