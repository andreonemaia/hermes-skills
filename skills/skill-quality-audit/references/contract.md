# Contrato da primeira versão

Versão da skill: 0.1.0. Este documento fixa o que a `skill-quality-audit` audita,
o que ela distingue, o que ela não faz e como um achado é declarado. A
implementação segue este contrato; divergência entre os dois é defeito.

## Objetivo

Auditar a qualidade e a prontidão de uma skill para uso e publicação, produzindo
achados rastreáveis e limites explícitos.

## O que a auditoria distingue

Toda afirmação do relatório pertence a uma destas categorias, e o relatório
registra a categoria em campo próprio:

| categoria | como aparece no relatório |
| --- | --- |
| verificação estrutural | `method: structural`, com status e motivo em `checks_run` |
| avaliação semântica pelo agente | `semantic_checklist`, julgada pelo agente com evidência |
| validação executada | `validation: executed` no achado ou no item do checklist |
| resultado apenas relatado | `validation: reported`, com a fonte que declarou o resultado |
| item não verificável com as evidências disponíveis | `validation: unverifiable` ou `not_run`, com o motivo |

Nenhuma dessas categorias é convertida em nota, selo ou percentual. Falta de
evidência não é aprovação nem prova automática de defeito.

## Cobertura inicial

As nove frentes do contrato e as verificações que as cobrem:

| frente | verificações |
| --- | --- |
| frontmatter e metadados exigidos pelo repositório | SQA-002, SQA-003, SQA-004, SQA-007, SQA-008 |
| coerência entre nome, diretório, requisitos e procedimento | SQA-005, SQA-006, SQA-101 |
| exemplos em linguagem natural e documentação humana em PT-BR | SQA-010, SQA-011, SQA-012, SQA-102, SQA-103 |
| arquivos e referências locais, incluindo links relativos | SQA-013, SQA-014 |
| presença e adequação observável dos testes | SQA-015, SQA-016, SQA-104 |
| consistência entre comandos, estados e identificadores documentados | SQA-017, SQA-023 |
| evidências para plataformas e resultados declarados | SQA-009, SQA-018 |
| possíveis dados pessoais, segredos e referências privadas | SQA-019, SQA-020, SQA-021, SQA-022 |
| limites de segurança e alegações de capacidade ou isolamento | SQA-025, SQA-105 |

Além das nove frentes, o catálogo tem verificações transversais, que não
pertencem a uma frente única: SQA-001 (SKILL.md ausente), SQA-024 (conteúdo
provisório publicado), SQA-026 (divergência do manifesto esperado), SQA-027
(construção de frontmatter fora da cobertura do leitor restrito), SQA-106
(justificativa de severidade) e SQA-107 (encaminhamento para revisão
independente).

O catálogo completo, com severidade padrão, descrição e limites de cada
verificação, está em `references/checks.md`.

### O que SQA-013 examina

A verificação de referências não trata todo texto igual, e o contrato diz qual é
qual. Os exemplos ficam em bloco cercado justamente porque é isso que os torna
literais para a auditoria; fora do bloco, um caminho relativo inexistente vira
SQA-013 na hora.

```text
| construção                                        | tratamento                                             |
| ------------------------------------------------- | ------------------------------------------------------ |
| link markdown [texto](caminho) fora de bloco      | resolvido contra o sistema de arquivos                 |
| caminho entre crases, tipo `scripts/x.py`, fora   | resolvido contra o sistema de arquivos                 |
| link ou caminho entre crases dentro de bloco      | não verificado: o bloco é lido como exemplo literal    |
| invocação de comando dentro de bloco              | não é SQA-013; SQA-016 e SQA-017 cuidam disso          |
| link em estilo de referência ([texto]: url)       | não resolvido; fica para a leitura semântica           |
| URL externa                                       | não verificada: a auditoria não usa rede               |
```

A verificação prova existência, não conteúdo: não confere se o arquivo
referenciado diz o que a documentação afirma.

## Somente leitura

A auditoria é somente leitura sobre o alvo, e isso é demonstrado, não afirmado:

- o script monta um manifesto de conteúdo (caminho, tamanho e hash) antes e
  depois das verificações e compara os dois;
- divergência observada durante a execução vira achado SQA-026, com severidade
  crítica, e código de saída 5;
- `--check-unchanged` aceita um manifesto de referência e compara o alvo com
  ele, num campo próprio (`matches_reference`), para provar que o alvo não mudou
  entre duas execuções. Divergência de referência é achado crítico SQA-026, mas
  não reescreve `target_unchanged`: ela responde a outra pergunta.

O campo `reference_compared` separa "não comparado" de "inconclusivo": numa
execução sem `--check-unchanged` ele é `false` e `matches_reference` é `null` sem
que nenhum limite seja acrescentado.

Três estados, nunca dois. `target_unchanged` e `matches_reference` podem ser
`true`, `false` ou `null`:

- `true`: todos os arquivos foram lidos e os hashes coincidem;
- `false`: houve diferença observada, com evidência em `manifest_diff` ou
  `reference_diff`;
- `null`: o conteúdo de algum arquivo não pôde ser lido, ou uma subárvore não
  pôde ser listada. Nesse caso o relatório traz `manifest_unreadable` e/ou
  `walk_errors`, um item de limite e a frase "inconclusivo"; a auditoria não
  afirma estabilidade nem alteração sem prova.

A comparação é feita por lado e usa uma regra única para execução e referência:
**ausência só prova adição ou remoção quando aquele lado foi efetivamente
observado**. Diferença já observada em arquivos lidos vence o buraco: um conteúdo
alterado, ou uma entrada que só um dos lados tem fora da subárvore ilegível,
continua sendo `false`. Uma entrada ausente porque a subárvore não pôde ser
listada vai para `unconfirmed_added` ou `unconfirmed_removed`, nunca para
`added`/`removed`, e torna o resultado inconclusivo em vez de afirmar remoção ou
adição. `holes_before` e `holes_after` nomeiam as subárvores não observadas de
cada lado, incluindo `.` quando a falha atinge a raiz e `(caminho desconhecido)`
quando a travessia falha antes de nomear o diretório; os dois deixam a árvore
inteira não observada. Cada registro de `walk_errors` é lido pela mesma
fronteira, aceite como o objeto `{rel, reason}` que o manifesto gera ou como a
string que nomeia o diretório. O campo é uma lista, mas um registro único
(string ou objeto) também é aceito e lido como lista de um item: quem escreve o
manifesto à mão não precisa saber que a forma canônica é a lista. Um registro sem
motivo declarado traz
`(motivo desconhecido)` em vez de um motivo vazio ou inventado. `proven_same`
indica se a
comparação dos hashes comuns foi completa. `identical` responde a uma pergunta
menor: diz apenas que nenhuma diferença foi comprovada entre as entradas
comparadas, e por isso pode ser `true` com `complete: false`. Quem decide se a
comparação concluiu é `proven_same`, não `identical`.

O código de saída 5 só é emitido quando `target_unchanged` é `false`, isto é,
quando a comparação antes/depois da própria execução observou diferença. Um
arquivo ilegível nunca é tratado como hash válido. A comparação com o manifesto
de referência é reportada separadamente: um alvo estável durante a execução pode
divergir de uma referência antiga, e esse caso aparece como
`matches_reference: false` com achado SQA-026 crítico e código de saída 1
(achados), nunca como alteração durante a auditoria.

`--fail-on` governa apenas a passagem de achados para o código de saída 1. A
matriz completa, incluindo `never`, é:

| situação | `matches_reference` | registro | código |
| --- | --- | --- | --- |
| achado no limiar ou acima | qualquer | o achado | 1 |
| o mesmo achado com `--fail-on never` | qualquer | o achado, ainda no relatório | 0 |
| divergência de referência | `false` | achado SQA-026 crítico | 1 |
| divergência de referência com `never` | `false` | achado SQA-026 crítico | 0 |
| mudança observada durante a execução | qualquer | não é achado: `target_unchanged: false` | 5 |
| mudança observada durante a execução com `never` | qualquer | idem | 5 |
| comparação inconclusiva | `null` | limite SQA-026, com `holes_*` explicando a lacuna | 0 |

Ou seja: `never` desliga o efeito dos achados no código de saída, e nada mais.
Ele não esconde `matches_reference: false`, não transforma comparação
inconclusiva em igualdade e não suprime o código 5, que reporta integridade
observada durante a execução, não severidade de achado.
Limites (`limits`) não passam pelo limiar de `--fail-on`: só achados decidem o
código 1. É por isso que a linha de comparação inconclusiva termina em 0 mesmo
com o limite SQA-026 registrado, enquanto a divergência de referência, que é um
achado, segue a coluna do limiar.

O backend de frontmatter também tem contrato de uso: `--backend auto` usa
`pyyaml` quando importável e cai para o parser restrito caso contrário,
registrando SQA-027 para o que não decide. `--backend pyyaml` explícito sem a
dependência importável é erro de uso (código 3), com mensagem que aponta
`auto`/`restricted`: é uma condição do ambiente do auditor, não um defeito de
frontmatter do alvo, e o script não instala nada para contorná-la.

O conteúdo auditado é material de análise, não autorização: o script não segue
instruções encontradas no alvo, não executa nenhum script dele, não instala
dependências e não corrige arquivos. Testes do alvo só podem ser executados
quando fizerem parte de uma validação explicitamente autorizada; nesta versão a
skill não executa testes do alvo e declara isso como limite.

## Privacidade

- Achados de privacidade indicam localização e categoria, nunca o valor: a
  evidência traz a classe detectada e o comprimento, com o valor suprimido.
- A detecção é por formato, com expressões fixas para: atribuição de
  credencial, chave AWS, token GitHub (`ghp_`/`github_pat_`), chave no estilo
  `sk-`, token Slack, chave Google, JWT, `Bearer` e bloco de chave privada
  (SQA-019); caminho de usuário Windows/POSIX, e-mail e documento no formato
  `000.000.000-00` (SQA-020); identificador de sessão no formato do Hermes
  (SQA-021); domínio `.corp`/`.internal`/`.intranet`/`.lan`, URL interna e IPv4
  privado (SQA-022).
- Três eixos ficam separados e não se substituem:
  - confiança do sinal: `confirmed` quando o formato aparece fora de caminho de
    teste; `signal` quando aparece em `tests/`, `fixtures/` ou arquivo `test_*`;
  - severidade: é o impacto **se o achado for confirmado** e não é reduzida
    pelo caminho. Credencial real em arquivo de teste mantém o impacto de
    credencial real;
  - avaliação pelo agente: `needs_agent_confirmation` é `true` para `signal`. O
    auditor confirma a origem do valor, mantém o achado ou o descarta com
    justificativa registrada.
- O que o scanner não determina: se o valor é real ou sintético, se a
  credencial está ativa, se o e-mail ou caminho pertence a pessoa real, e se um
  nome de repositório ou serviço privado é sensível sem formato próprio. Esses
  casos ficam para a revisão semântica (SQA-103, SQA-105) ou para uma
  declaração de limite.
- Falsos positivos conhecidos: valores de exemplo em documentação que não
  seguem os placeholders genéricos reconhecidos (`<...>`, `%...%`, `${...}`,
  `{{...}}`, `user`, `example`, `sample`), e IPv4 privado citado como exemplo
  de faixa em texto explicativo.
- Nomes de repositório ou serviço privado sem formato reconhecível não são
  detectáveis por padrão e ficam para a avaliação semântica (SQA-103, SQA-105).

## Exportação

Dois artefatos saem do auditor, e eles não são o mesmo tipo de arquivo:

- o **relatório** (`audit`, JSON e Markdown) é o artefato feito para circular.
  Todos os campos lidos do alvo são mascarados na exportação, mas o mascaramento
  é limitado por formato: só as classes de `PRIVACY_PATTERNS` são reconhecidas.
  Texto livre, nomes privados sem formato e caminhos relativos do alvo
  permanecem. Por isso `publication.report_safe_to_attach` é `false`: o
  relatório exige revisão antes de ser anexado a PR, issue ou chat;
- o **manifesto** (`manifest --out`) é técnico e não mascarado, para que a
  comparação de integridade seja exata. Ele é privado e não é publicável:
  `publication.manifest_raw` e `publication.manifest_private` registram isso.
  Use-o apenas como entrada de `--check-unchanged`.

O bloco `publication` acompanha o relatório para que o artefato declare seu
próprio status, em vez de depender de documentação externa.

## Formato mínimo de achado

Cada achado traz identificador, categoria, severidade justificada,
arquivo/localização, evidência, impacto, recomendação e situação de validação.
Os campos exatos estão em `references/finding-format.md`.

## Relação com a independent-code-review

A `skill-quality-audit` coordena a avaliação e encaminha a revisão técnica
independente quando pertinente (SQA-107). O procedimento da revisão não é
duplicado aqui: a skill de auditoria descreve o que encaminhar e como tratar o
retorno, e a `independent-code-review` executa a revisão com processo e contexto
separados.

## Não objetivos desta versão

- não é um framework geral de linters nem um novo runner de agentes;
- não corrige o alvo, não instala dependências e não executa o que audita;
- não mede desempenho, corretude de execução nem compatibilidade real com as
  plataformas declaradas;
- não emite nota numérica universal nem selo automático de segurança;
- não substitui revisão humana nem a decisão de publicar.
