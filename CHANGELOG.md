# Changelog

Todas as mudanças relevantes nas skills deste repositório são documentadas aqui.

O formato segue o [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e as
skills seguem o [Versionamento Semântico](https://semver.org/). A versão de cada
skill é o campo `version` do seu próprio `SKILL.md`.

## [Unreleased]

### Added

- `skill-quality-audit` (0.1.0): nova skill de auditoria de prontidão para
  publicação. Executa verificações estruturais determinísticas em modo somente
  leitura sobre uma skill (frontmatter, links relativos, coerência entre
  catálogo e documentação, plataformas declaradas, comandos citados, privacidade
  e alegações de capacidade), prova a ausência de escrita com manifesto de
  conteúdo antes e depois e entrega ao agente os itens semânticos com evidência
  citada. Cada achado separa severidade, confiança e situação de validação; o
  catálogo em `references/checks.md` é gerado a partir de `scripts/checks.py` e
  conferido por `python scripts/render_checks.py --check`. Inclui piloto
  somente leitura nas quatro skills existentes, com manifestos antes e depois.
- `skill-quality-audit`: a integridade do alvo passa a distinguir duas
  comparações independentes. `target_unchanged` responde só se o alvo mudou
  durante a execução; `matches_reference` responde se ele ainda confere com o
  manifesto de `--check-unchanged`. Divergência de referência vira achado
  SQA-026 crítico com código 1, sem reescrever a comparação antes/depois; o
  código 5 fica reservado para mudança observada durante a auditoria. Arquivo
  ilegível deixa a comparação incompleta e o resultado inconclusivo, nunca
  "inalterado". Regressões novas cobrem arquivos legíveis iguais e diferentes,
  leitura indisponível simulada de forma determinística, referência divergente
  com alvo estável, mudança no meio da execução e os três estados de
  `text_state`.

### Changed

- Documentação humana em PT-BR: o `README.md` da raiz, os `README.md` das skills
  (`independent-code-review`, `comfyui-model-manager`,
  `task-checkpoint-recovery`), o `CONTRIBUTING.md`, o `docs/development.md`, o
  `docs/installation.md` e o `docs/testing.md` foram reescritos em português
  brasileiro, com exemplos de uso em linguagem natural.
- `CONTRIBUTING.md` e `docs/development.md` passam a estabelecer a regra
  permanente de idioma: READMEs e demais documentação humana em PT-BR, com
  identificadores técnicos, comandos, caminhos e estruturas de dados
  inalterados, e `SKILL.md` livre para permanecer em inglês quando isso for
  melhor para descoberta e execução.
- Documentação do repositório: o `docs/development.md` passa a exigir que os
  identificadores que o código de uma skill emite permaneçam sincronizados com a
  sua documentação, e o `docs/testing.md` descreve como testar essa consistência.
- Nenhum script de produção ou comportamento de skill foi alterado por esta
  mudança.
- `skill-execution-report` (0.1.0): documentação da força das evidências
  explicitada, sem mudança de comportamento nem de esquema. O `README.md`, o
  `SKILL.md` e o `references/evidence-rules.md` passam a declarar que
  `artifact_file` comprova apenas a existência do arquivo referenciado no momento
  da verificação, que `execution_proven` não comprova criação, modificação nem
  autoria do artefato, e que a indisponibilidade de uma fonte pode tornar
  evidências não verificáveis sem significar que a execução não ocorreu. A
  ausência de repositório só impede a verificação de commits por esse mecanismo:
  um arquivo verificável sob uma base permitida, como o `cwd` da sessão, ainda
  sustenta `execution_proven`, e a chamada permanece `invoked` apenas quando não
  há outro artefato correlacionado verificável. Esquema, nomes de classificação e
  saída permanecem idênticos.
- Exemplos de sessão na documentação e nos testes passam a usar identificadores
  explicitamente sintéticos, no mesmo formato dos identificadores reais, e o
  `SKILL.md` rotula os identificadores dos exemplos como sintéticos; o teste de
  redação do identificador no `stderr` mantém as mesmas asserções.

### Fixed

- `skill-execution-report` (0.1.0): tratamento da revisão independente do PR #6 —
  88 achados tratados: 69 confirmados (67 integrais, 2 parciais) corrigidos, 7
  rejeitados por contrariarem o contrato aprovado e 12 informativos avaliados.
  Entre as correções: resultado de
  `skill_script` não observado deixou de virar sucesso implícito (novo motivo
  `skill_script_result_unknown`); `EXIT_OUTPUT = 5` separa `--out` não gravável de
  fonte corrompida; `SER_NOW` inválido é erro de uso (4), sem traceback;
  `parse_iso` rejeita timestamps não finitos e fora da faixa representável;
  `os.path.commonpath` não derruba mais o relatório com unidades diferentes;
  SQLite abre com `mode=ro` e `uri=True` tratando `OperationalError` e
  `sqlite3.Error`; prefixo de sessão escapa curingas de `LIKE`; truncagem de
  payload é sinalizada em vez de silenciosa; `.usage.json` estruturalmente
  inválido vira `malformed_entries`; JWT é redigido por inteiro (inclusive a
  assinatura) e o `stderr` é sanitizado também em `--json`; caminhos sob o home
  colapsam para `<home>`. A linha 7 da tabela de decisão de
  `references/evidence-rules.md` citava `edit_without_load` como `state_reason`,
  mas o classificador emite `skill_manage_only` e reserva `edit_without_load` para
  limitação: corrigido e fixado por regressão. Suíte: 142 testes.
- Documentação do `task-checkpoint-recovery` alinhada com a implementação: um
  `--expect-revision` desatualizado sai com código 1 (`EXIT_DRIFT`), não 3; o
  `verify` reprova a execução em todo achado não benigno (não apenas com
  `--strict`); o prompt de retomada informa o diretório da skill a partir do qual
  os comandos devem ser executados; a lista de arquivos do `README.md` nomeia os
  quatro módulos de teste.

### Added

- Skill `skill-execution-report` (0.1.0): reconstrói, a partir do `state.db` e do
  `.usage.json` do Hermes, o que realmente aconteceu com cada skill em uma sessão,
  separando `discovered` (nome mencionado), `loaded` (`skill_view` com sucesso
  observado), `invoked` (chamada atribuível, mesmo com resultado desconhecido ou
  falha) e `execution_proven` (sucesso com artefato verificado), com `available`
  mantido como vocabulário documental e `unverifiable` para o sinal que existe mas
  não é atribuível a um estado verificado. Somente leitura (nunca escreve no
  `state.db` nem altera contadores nativos), saída em JSON determinístico com o
  relógio fixado por `SER_NOW` (senão o relógio do sistema, indicado em
  `clock_source`) e relatório humano derivado dele, classificação
  conservadora, trechos de evidência higienizados, opção `--include-children`
  para incluir sessões filhas e `SER_NOW` para testes determinísticos. Somente
  biblioteca padrão; acompanha uma suíte `unittest` hermética (SQLite sintético e
  diretórios temporários, sem tocar sessões reais). Testada no Windows.
- Skill `task-checkpoint-recovery` (0.1.0): persiste o estado lógico e
  operacional de uma tarefa longa (objetivo, passos, decisões, bloqueios,
  próximas ações e um registro das operações que produzem efeitos) em
  `<project>/.hermes/checkpoints/<task-slug>/` e o revalida contra a realidade
  antes de retomar (`verify`, `resume-report`), para que uma tarefa interrompida
  seja retomada sem perder decisões nem repetir trabalho já feito. Inclui guarda
  de revisão do tipo compare-and-swap, lock com timeout, escritas atômicas,
  quarentena de checkpoints corrompidos e a opção `--now` para testes
  determinísticos. Somente biblioteca padrão; acompanha uma suíte `unittest`.
  Complementa as sessões nativas, os checkpoints de arquivo e a compactação de
  contexto. Testada no Windows.
- Skill `comfyui-model-manager` (0.1.0): descobre uma instalação do ComfyUI,
  inventaria os arquivos de modelo em disco, verifica um manifesto SHA-256
  contra eles e baixa apenas as entradas ausentes ou corrompidas (simulação por
  padrão). Também valida estaticamente um workflow, em formato de API ou de UI,
  contra o `/object_info` de um servidor em execução. Somente biblioteca padrão;
  acompanha uma suíte `unittest`. Complementa a skill oficial `comfyui`. Testada
  no Windows.
- `skill-execution-report` (0.1.0): teste de regressão
  `tests/test_scope_isolation.py`, comparando `include_children=false` e
  `include_children=true` sobre as mesmas entradas (fixtures determinísticas),
  com um par A×B com Git disponível e outro sem Git, verificando que apenas
  `include_children` varia entre as corridas e que eventos de sessões filhas não
  aparecem no escopo `main`. Suíte: 146 testes.
- `docs/backlog.md`: backlog do repositório com a proposta (não implementada) de
  separar invocação comprovada, existência verificada, modificação observada e
  proveniência causal da modificação em `skill-execution-report`.

## [1.0.0] - 2026-10-05

### Added

- Infraestrutura do repositório: `README.md`, `LICENSE` (MIT), `CONTRIBUTING.md`,
  `CHANGELOG.md`, `.gitignore` e o conjunto de guias em `docs/`.
- Skill `independent-code-review`: revisão de código independente e somente
  leitura, executada por um modelo revisor separado, em um processo isolado e
  finito (one-shot). Cobre pull requests do GitHub, diffs de branch contra a base,
  mudanças em staging e mudanças não preparadas. Testada no Windows.
