# skill-quality-audit

Audite a qualidade e a prontidão de publicação de uma skill, com achados
rastreáveis e limites declarados. O script faz verificações estruturais
determinísticas em modo somente leitura; o agente julga os itens semânticos a
partir da evidência coletada.

## O que é

Uma skill de auditoria que separa três coisas que costumam ser confundidas:

- o que foi **executado** sobre o alvo e observado diretamente;
- o que foi apenas **relatado** pelo próprio alvo ou por terceiros;
- o que **não pôde ser verificado** com a evidência disponível.

Nenhuma dessas categorias vira nota, selo ou percentual. Falta de evidência não é
aprovação, e também não é prova automática de defeito.

O problema que ela resolve: uma skill pode ter frontmatter válido, README bonito
e link quebrado na mesma página, ou uma credencial real escondida em um arquivo
de teste. Revisar isso à mão é lento e inconsistente; revisar só o frontmatter dá
uma falsa sensação de cobertura.

## Quando usar

- Antes de publicar uma skill nova ou uma versão nova de uma existente.
- Antes de abrir um PR que mexe em `SKILL.md`, `README.md`, referências ou
  scripts de uma skill.
- Quando a pergunta é "esta skill está pronta?" ou "isto vaza dado pessoal?".
- Como porta de entrada para a `independent-code-review`, quando os achados são
  técnicos.

Não use para:

- corrigir o alvo: a auditoria relata, o autor decide;
- executar os scripts ou testes do alvo: isso exige autorização explícita e está
  fora do escopo desta versão;
- atribuir nota numérica ou selo de segurança.

## Pré-requisitos

- Python 3.11 ou superior.
- `pyyaml` para leitura completa do frontmatter. Sem ele o script usa um parser
  restrito e registra SQA-027 para construções que não consegue decidir, em vez
  de adivinhar.
- O alvo como diretório local. Nenhum acesso à rede é feito.

Plataforma: desenvolvido e testado em Windows 11 com Python 3.14 e git-bash. A
suíte roda em Linux e macOS pela stdlib, mas essas plataformas não são validadas
a cada mudança e não são declaradas no frontmatter.

## Uso

```bash
# auditoria completa, relatório legível
python scripts/audit_skill.py audit --target ../minha-skill

# relatório em JSON, gravado fora do alvo
python scripts/audit_skill.py audit --target ../minha-skill \
  --format json --out report.json

# catálogo de verificações
python scripts/audit_skill.py checks

# manifesto de conteúdo, para provar que o alvo não mudou
python scripts/audit_skill.py manifest --target ../minha-skill --out before.json
python scripts/audit_skill.py audit --target ../minha-skill \
  --check-unchanged before.json --fail-on never
```

`--fail-on` escolhe a severidade mínima que produz código de saída 1. O padrão é
`high`; `never` mantém os achados no relatório e desliga o efeito no código de
saída.

## Como ler o relatório

Cada achado traz identificador da verificação, categoria, severidade com
justificativa, arquivo e linha, evidência sanitizada, impacto, recomendação e
situação de validação. O formato completo está em
`references/finding-format.md`; um exemplo real sanitizado está em
`references/example-report.md`.

Três eixos são independentes:

| eixo | valores | o que significa |
| --- | --- | --- |
| confiança | `confirmed`, `signal` | `confirmed` foi observado diretamente; `signal` precisa de decisão humana |
| severidade | `info` a `critical` | impacto **se** o achado for confirmado; não cai por estar em arquivo de teste |
| validação | `executed`, `reported`, `unverifiable`, `not_run` | como o resultado foi obtido |

Uma credencial real dentro de `tests/` continua `critical`: o caminho de teste
reduz a confiança para `signal`, não o impacto. Quem audita confirma a origem,
mantém o achado ou o descarta com justificativa registrada.

`checks_run` mostra o que rodou e o que não rodou. Um item `not_run` é lacuna de
cobertura declarada, nunca aprovação.

## Integridade do alvo

Duas perguntas diferentes, dois campos diferentes:

- `target_unchanged` compara o manifesto tirado antes da auditoria com o tirado
  depois. Responde só isto: o alvo se moveu enquanto a auditoria rodava?
- `matches_reference` compara o alvo com o manifesto passado em
  `--check-unchanged`. Responde outra coisa: o alvo ainda é o que era naquela
  referência?

Os dois campos têm três estados, nunca dois: `true` (verificado), `false`
(divergência observada) e `null` (inconclusivo). A mesma regra vale para
`manifest_diff` e `reference_diff`, porque as duas comparações usam o mesmo
código: **ausência só prova adição ou remoção quando aquele lado foi
efetivamente observado**. Uma entrada que só um dos manifestos tem, fora da área
ilegível, continua provando divergência. Uma entrada que falta porque a
subárvore não pôde ser listada vai para `unconfirmed_added` ou
`unconfirmed_removed`, nunca para `added`/`removed`, e deixa o resultado
inconclusivo. `holes_before` e `holes_after` nomeiam as subárvores não
observadas em cada lado, incluindo `.` quando a própria raiz falhou. Conteúdo
ilegível nunca vira digest válido: ele aparece em `manifest_unreadable`, a
comparação fica incompleta e o resultado é inconclusivo. O campo
`reference_compared` diz se a comparação chegou a acontecer: sem
`--check-unchanged` ele é `false` e `matches_reference` é `null`, que é "não
comparado", não uma afirmação de igualdade.

Um alvo pode ficar estável durante a execução e ainda divergir de uma referência
antiga. Esse caso sai como `target_unchanged: true`, `matches_reference: false`,
achado SQA-026 crítico e código 1. O código 5 fica reservado para a divergência
observada durante a própria auditoria.

## Códigos de saída

| código | quando |
| --- | --- |
| 0 | auditoria concluída sem achado na severidade mínima pedida |
| 1 | há achado na severidade mínima pedida |
| 2 | alvo inexistente ou não é diretório |
| 3 | uso incorreto da linha de comando |
| 4 | falha ao gravar a saída pedida |
| 5 | o alvo mudou durante a auditoria (diferença observada entre os manifestos de antes e depois) |
| 6 | falha interna inesperada do auditor: não há relatório em que se possa confiar |

Os códigos 1 e 5 são resultado da auditoria: o relatório existe e traz o motivo.
Os códigos 2, 3, 4 e 6 são falha operacional: não há relatório. Uma pipeline que
trata 1 como "achado" e 2 a 4 como "erro" não precisa interpretar o texto.
Pedir `--out` dentro do alvo é recusa de uso (3), não falha de gravação (4):
o arquivo não chega a ser criado. `manifest` sem alvo válido também sai com 2.

## Cobertura e limites

O catálogo em `references/checks.md` é a fonte única de verdade para códigos,
método, severidade padrão e limites de cada verificação. Ele é gerado a partir de
`scripts/checks.py`:

```bash
python scripts/render_checks.py          # reescreve o arquivo
python scripts/render_checks.py --check  # falha se o arquivo commitado estiver desatualizado
```

A regeneração depende apenas do catálogo e do próprio script: sem relógio, sem
conteúdo do diretório de trabalho e sem área de rascunho. Os testes comparam o
arquivo commitado com uma renderização nova, byte a byte.

Limites que valem para toda a versão:

- somente leitura sobre o alvo, comprovado por manifesto de conteúdo antes e
  depois; isso cobre o diretório auditado, não efeitos externos;
- o frontmatter é lido por `pyyaml` quando disponível e por um parser restrito
  caso contrário, que declara o que não decide;
- verificações de idioma e de alegação de capacidade são heurísticas e sempre
  `signal`;
- os testes do alvo não são executados: a auditoria observa que `tests/` existe,
  segue o padrão `test_*.py` e menciona `unittest` ou `pytest`; ela não prova
  descoberta nem execução, e uma suíte verde não é evidência de qualidade dos
  testes;
- nomes de repositório ou serviço privado sem formato reconhecível não são
  detectados automaticamente e ficam para a avaliação semântica.

## Exportação e privacidade

Os achados SQA-019 a SQA-022 informam localização, classe e comprimento. O valor
nunca é reproduzido.

O relatório (`audit`, JSON e Markdown) é mascarado na exportação, mas o
mascaramento é **limitado por formato**: só as classes reconhecidas são
substituídas, e texto livre, nomes privados sem formato e caminhos relativos do
alvo permanecem como estão. Por isso `publication.report_safe_to_attach` é
`false`: o relatório exige revisão antes de ser anexado a PR, issue ou chat. O
bloco `publication` viaja no próprio relatório para que o artefato declare seu
status em vez de depender de prosa externa.

O manifesto escrito por `manifest --out` é técnico e não mascarado, para que a
comparação de integridade seja exata. Ele é privado e não deve ser publicado:
`publication.manifest_private` registra isso. Use-o apenas como entrada de
`--check-unchanged`.

A detecção é por formato fixo; ela não determina se o valor é real, se a
credencial está ativa, se o endereço pertence a uma pessoa real ou se um nome
privado é sensível sem formato próprio.

## Testes

```bash
python -m unittest discover -s tests
```

A suíte cobre frontmatter válido e ausente, referência quebrada, divergência
entre documentação e catálogo, declaração de plataforma sem evidência, exemplos
sintéticos, sinais de privacidade em arquivo normal e de teste, entradas
inválidas dos parsers, formato dos achados e códigos de saída, além da
regeneração determinística de `references/checks.md`.

Casos contrapostos ficam lado a lado no mesmo arquivo de teste, para que uma
heurística frouxa não passe despercebida: documentação descritiva versus
afirmação excessiva que cita um teste, cabeçalho de geração versus alegação
problemática no mesmo arquivo, caminho explicitamente ilustrativo versus
referência real quebrada com nome parecido, placeholder versus expressão de
código, e marcador de pendência citado versus marcador efetivo. Onde a análise
estática não decide, o teste espera `signal` e não aprovação.

## Piloto em skills existentes

O piloto foi executado em modo somente leitura sobre as quatro skills que já
existiam no repositório, com o manifesto de conteúdo do próprio script gerado
antes e outro depois de cada auditoria. Os digests abaixo são iguais antes e
depois, e o campo `target_unchanged` do relatório é `true` nas quatro execuções:
a auditoria não alterou nenhum arquivo dos alvos. Todos os artefatos foram
gravados fora dos alvos.

| Alvo | Arquivos | Digest do manifesto | Achados | Código de saída |
|---|---|---|---|---|
| `independent-code-review` | 3 | `e7dd6f9f6b19…` | 7 (SQA-006, SQA-014, SQA-025 ×5) | 0 |
| `comfyui-model-manager` | 7 | `4fb12699ce5f…` | 3 (SQA-006, SQA-014, SQA-025) | 0 |
| `task-checkpoint-recovery` | 11 | `bbab28ec402b…` | 7 (SQA-018, SQA-019 ×2, SQA-020 ×2, SQA-025 ×2) | 1 |
| `skill-execution-report` | 14 | `fdc8481e0aed…` | 34 (SQA-014, SQA-019 ×2, SQA-020 ×2, SQA-021 ×24, SQA-025 ×5) | 1 |

Cada alvo também foi reauditado com `--check-unchanged` contra um manifesto
gerado antes: nenhuma execução produziu achado de integridade, e os códigos de
saída ficaram em 0, 0, 1 e 1, os mesmos da auditoria sem manifesto. O 1 vem do
`--fail-on high` padrão, não da checagem de integridade. Os dois alvos com
código 1 têm achado em severidade `high` ou maior; os dois com código 0 têm
apenas `medium`, abaixo do padrão de `--fail-on`.

O piloto não corrigiu nenhuma das quatro skills. Os achados ficam registrados
como estão, para que cada autora decida o que fazer com eles.

O que o piloto mostrou de concreto:

- SQA-006 (descrição que não começa com o gatilho `Use when`) apareceu em duas
  das quatro skills e SQA-014 (link relativo que sai da raiz do alvo, como
  `../../docs/installation.md`) em três: são convenções do repositório que valem
  para todas.
- SQA-018 pegou `task-checkpoint-recovery` declarando `windows` no frontmatter
  sem nenhum documento que registre validação nessa plataforma.
- SQA-019 e SQA-020 em `tests/` vieram todos com confiança `signal`, como o
  contrato manda: a severidade não cai pelo caminho, mas o achado pede
  confirmação do agente. Nos quatro alvos, os valores eram sintéticos e
  declarados como tal pelos próprios testes.

Limitações observadas no piloto, registradas sem contorno automático:

- **Identificador sintético de sessão.** `skill-execution-report` documenta o
  formato de `session_id` com um exemplo sintético (data, hora e sufixo
  aleatório) e recebeu dois achados SQA-021 `confirmed` por isso. O contrato já
  declara esse caso como falso positivo conhecido: o scanner reconhece o
  formato, não a intenção. A avaliação do agente é o que separa exemplo de
  vazamento.
- **Duas ocorrências na mesma linha.** Quando a mesma classe aparece duas vezes
  na mesma linha, saem dois achados com a mesma localização e a mesma evidência
  (classe e comprimento). A ordem dos achados é a ordem de leitura da linha, mas
  o relatório não numera as ocorrências.
- **Volume em alvo grande.** Um alvo com muitos testes que exercitam privacidade
  de propósito gera muitos achados `signal` (24 no maior alvo do piloto). Isso é
  consequência de não reduzir severidade pelo caminho, não de um defeito do
  alvo; a triagem é do agente.
- **Referência em prosa a script inexistente.** SQA-017 julga invocações
  documentadas, não menções descritivas: a referência em prosa que apareceu no
  piloto anterior deixou de ser achado, e o caso fica para a avaliação
  semântica. A verificação não tenta adivinhar se a menção é comando ou
  ilustração.
- **Exclusões de SQA-025 são estreitas e declaradas.** Ficam fora da varredura
  apenas títulos de seção, listas de metadados e blocos cercados (exemplo de
  texto, não promessa da skill). Cabeçalho de geração e frases que citam testes,
  manifestos ou checksums continuam sendo varridos: nomear uma evidência não
  comprova a alegação, e o achado permanece `signal` até o agente conferir o que
  foi citado.

## Relação com a independent-code-review

Esta skill coordena a avaliação e encaminha a revisão técnica independente
quando pertinente (SQA-107). O procedimento da revisão não é duplicado aqui: a
`independent-code-review` executa a revisão em processo e contexto separados, com
modelo próprio.
