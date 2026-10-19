# Catálogo de verificações

<!-- Gerado por `python scripts/render_checks.py` a partir de `scripts/checks.py`.
     Não editar à mão: regenere o arquivo e confira o diff. -->

34 verificações: 27 estruturais executadas pelo script e 7 semânticas conduzidas pelo agente.

| código | método | severidade padrão | título |
| --- | --- | --- | --- |
| SQA-001 | structural | critical | O diretório do alvo não contém SKILL.md, portanto não é uma skill publicável. |
| SQA-002 | structural | critical | SKILL.md não começa com a linha --- que abre o bloco de frontmatter. |
| SQA-003 | structural | high | O bloco de frontmatter existe mas não pôde ser decodificado pelo backend ativo. |
| SQA-004 | structural | critical | Falta name, description, version ou platforms no frontmatter. |
| SQA-005 | structural | critical | O campo name do frontmatter não corresponde ao nome do diretório da skill. |
| SQA-006 | structural | medium | A description não começa com um gatilho autocontido (padrão 'Use when ...'). |
| SQA-007 | structural | medium | metadata.hermes.tags ou metadata.hermes.category não foram encontrados. |
| SQA-008 | structural | medium | O campo version não segue MAJOR.MINOR.PATCH. |
| SQA-009 | structural | high | platforms não é uma lista não vazia de tokens reconhecidos. |
| SQA-010 | structural | medium | A skill não traz documentação humana em README.md. |
| SQA-011 | structural | medium | Sinal automático de que o README.md pode não estar em PT-BR, como o repositório exige. |
| SQA-012 | structural | medium | Nenhum exemplo de pedido em linguagem natural foi encontrado na documentação. |
| SQA-013 | structural | high | Um link relativo ou um caminho citado na documentação não existe no alvo. |
| SQA-014 | structural | high | A documentação cita caminho absoluto ou caminho que sai da raiz da skill. |
| SQA-015 | structural | high | A skill entrega scripts/ mas não tem tests/. |
| SQA-016 | structural | medium | tests/ existe mas nenhum arquivo test_*.py cita unittest ou pytest. |
| SQA-017 | structural | high | Um comando documentado usa um script do alvo que não existe ou um subcomando que o parser não oferece. |
| SQA-018 | structural | medium | Uma plataforma declarada no frontmatter não tem nenhuma evidência de exercício na documentação. |
| SQA-019 | structural | critical | Um texto do alvo contém algo com formato de credencial. |
| SQA-020 | structural | high | Um texto do alvo contém caminho de usuário real, e-mail ou identificador pessoal. |
| SQA-021 | structural | high | Um texto do alvo cita identificador de sessão no formato do Hermes. |
| SQA-022 | structural | medium | Um texto do alvo cita host, domínio ou faixa de rede não pública. |
| SQA-023 | structural | medium | A versão citada no README diverge da versão do frontmatter. |
| SQA-024 | structural | low | A documentação contém marcadores de trabalho inacabado (TODO, TBD, FIXME). |
| SQA-025 | structural | medium | A skill afirma isolamento, somente leitura, ausência de rede ou outra garantia forte. |
| SQA-026 | structural | critical | O manifesto do alvo difere do esperado: mudou entre o início e o fim da auditoria, ou divergiu do manifesto de referência passado em --check-unchanged. |
| SQA-027 | structural | info | O frontmatter usa construção que o backend restrito não decide (bloco literal, âncora, mapa em fluxo) ou tem indentação inconsistente. |
| SQA-101 | semantic | medium | Nome, descrição, requisitos declarados e procedimento contam a mesma história. |
| SQA-102 | semantic | medium | O README descreve o comportamento real sem exagerar capacidades. |
| SQA-103 | semantic | medium | Exemplos parecem ilustrativos e não dados reais do autor. |
| SQA-104 | semantic | medium | Os testes exercitam comportamento em vez de repetir a implementação. |
| SQA-105 | semantic | high | Garantias de segurança afirmadas pela skill correspondem ao que o código faz. |
| SQA-106 | semantic | low | Cada severidade atribuída se justifica pelo impacto e pelo alcance, não pela categoria. |
| SQA-107 | semantic | info | Avalia se a independent-code-review é pertinente e o que deve ser entregue a ela. |

## Verificações estruturais

Executadas por `python scripts/audit_skill.py audit --target <skill>`; cada resultado traz status executado, motivo e achados.

### SQA-001 — O diretório do alvo não contém SKILL.md, portanto não é uma skill publicável.

- categoria: structure
- severidade padrão: critical
- o que verifica: O diretório do alvo não contém SKILL.md, portanto não é uma skill publicável.
- como: _check_skill_md em scripts/audit_skill.py: presença de SKILL.md na raiz do alvo.
- limites: Um SKILL.md presente não prova que a skill funciona; apenas que o ponto de entrada existe.

### SQA-002 — SKILL.md não começa com a linha --- que abre o bloco de frontmatter.

- categoria: metadata
- severidade padrão: critical
- o que verifica: SKILL.md não começa com a linha --- que abre o bloco de frontmatter.
- como: _check_frontmatter: procura a linha de abertura do bloco na primeira linha de SKILL.md.
- limites: Não avalia o conteúdo semântico do frontmatter, apenas a existência do bloco.

### SQA-003 — O bloco de frontmatter existe mas não pôde ser decodificado pelo backend ativo.

- categoria: metadata
- severidade padrão: high
- o que verifica: O bloco de frontmatter existe mas não pôde ser decodificado pelo backend ativo.
- como: _check_frontmatter: abertura presente sem linha de fechamento, ou erro de parse com o backend pyyaml.
- limites: Com o backend restrito, construções fora da cobertura declarada viram SQA-027 em vez de erro.

### SQA-004 — Falta name, description, version ou platforms no frontmatter.

- categoria: metadata
- severidade padrão: critical
- o que verifica: Falta name, description, version ou platforms no frontmatter.
- como: _check_required_keys: compara as chaves lidas com a lista exigida pelo repositório.
- limites: O default é critical e vale para name e description; o emissor aplica high a version e platforms e registra a severidade efetiva com justificativa no próprio achado (severity_justification). A política é deliberada: sem name/description a skill não é descoberta, sem version/platforms o consumo fica indefinido.

### SQA-005 — O campo name do frontmatter não corresponde ao nome do diretório da skill.

- categoria: consistency
- severidade padrão: critical
- o que verifica: O campo name do frontmatter não corresponde ao nome do diretório da skill.
- como: _check_name_directory: compara name com o basename do alvo.
- limites: Não julga se o nome é bom, apenas se ele é o mesmo identificador usado no diretório.

### SQA-006 — A description não começa com um gatilho autocontido (padrão 'Use when ...').

- categoria: metadata
- severidade padrão: medium
- o que verifica: A description não começa com um gatilho autocontido (padrão 'Use when ...').
- como: _check_description: mede o prefixo de 57 caracteres e procura o prefixo convencional.
- limites: Um gatilho presente não garante que a descrição descreva o comportamento correto.

### SQA-007 — metadata.hermes.tags ou metadata.hermes.category não foram encontrados.

- categoria: metadata
- severidade padrão: medium
- o que verifica: metadata.hermes.tags ou metadata.hermes.category não foram encontrados.
- como: _check_metadata_block: lê metadata.hermes na árvore do frontmatter.
- limites: Depende da cobertura do backend; com o parser restrito a leitura é limitada a três níveis.

### SQA-008 — O campo version não segue MAJOR.MINOR.PATCH.

- categoria: metadata
- severidade padrão: medium
- o que verifica: O campo version não segue MAJOR.MINOR.PATCH.
- como: _check_version: expressão regular sobre o valor de version.
- limites: Valida o formato, não se a versão corresponde ao histórico do repositório.

### SQA-009 — platforms não é uma lista não vazia de tokens reconhecidos.

- categoria: metadata
- severidade padrão: high
- o que verifica: platforms não é uma lista não vazia de tokens reconhecidos.
- como: _check_platforms: tipo do valor mais conjunto de tokens aceitos.
- limites: Declarar uma plataforma não prova que a skill foi exercitada nela: ver SQA-018.

### SQA-010 — A skill não traz documentação humana em README.md.

- categoria: documentation
- severidade padrão: medium
- o que verifica: A skill não traz documentação humana em README.md.
- como: _check_readme: presença de README.md na raiz do alvo.
- limites: Não avalia a qualidade do texto, apenas a existência do arquivo.

### SQA-011 — Sinal automático de que o README.md pode não estar em PT-BR, como o repositório exige.

- categoria: documentation
- severidade padrão: medium
- o que verifica: Sinal automático de que o README.md pode não estar em PT-BR, como o repositório exige.
- como: _check_readme_language: proporção de palavras funcionais portuguesas; o resultado sai como sinal e o agente confirma em SQA-102.
- limites: Heurística: emite sinal (confiança 'signal'), nunca decide sozinha o idioma do texto.

### SQA-012 — Nenhum exemplo de pedido em linguagem natural foi encontrado na documentação.

- categoria: documentation
- severidade padrão: medium
- o que verifica: Nenhum exemplo de pedido em linguagem natural foi encontrado na documentação.
- como: _check_examples: procura blocos cercados e marcadores de exemplo em SKILL.md e README.md.
- limites: A presença de um exemplo não prova que ele é executável ou fiel ao procedimento.

### SQA-013 — Um link relativo ou um caminho citado na documentação não existe no alvo.

- categoria: references
- severidade padrão: high
- o que verifica: Um link relativo ou um caminho citado na documentação não existe no alvo.
- como: _check_references: resolve links markdown e caminhos entre crases contra o sistema de arquivos.
- limites: Só verifica existência; não confere se o conteúdo referenciado diz o que a documentação afirma. Links markdown e caminhos entre crases dentro de blocos cercados não são verificados: o bloco é lido como exemplo literal. Invocações em bloco cercado são tratadas por SQA-016 e SQA-017, não por esta verificação. Links em estilo de referência ([texto]: url) não são resolvidos: ficam para a leitura semântica.

### SQA-014 — A documentação cita caminho absoluto ou caminho que sai da raiz da skill.

- categoria: references
- severidade padrão: high
- o que verifica: A documentação cita caminho absoluto ou caminho que sai da raiz da skill.
- como: _check_escaping_paths: normaliza cada caminho citado e compara com a raiz do alvo.
- limites: Placeholders entre <>, % % ou ${ } são tratados como genéricos e não geram achado.

### SQA-015 — A skill entrega scripts/ mas não tem tests/.

- categoria: tests
- severidade padrão: high
- o que verifica: A skill entrega scripts/ mas não tem tests/.
- como: _check_tests: presença de scripts/ comparada com a de tests/.
- limites: Não julga a cobertura dos testes existentes: ver SQA-016 e SQA-104.

### SQA-016 — tests/ existe mas nenhum arquivo test_*.py cita unittest ou pytest.

- categoria: tests
- severidade padrão: medium
- o que verifica: tests/ existe mas nenhum arquivo test_*.py cita unittest ou pytest.
- como: _check_tests: padrão de nomes em tests/ mais menção textual a unittest/pytest no arquivo.
- limites: Sinal textual, não prova de descoberta nem de execução: nenhum teste do alvo é executado, e um arquivo que apenas menciona o framework conta como menção.

### SQA-017 — Um comando documentado usa um script do alvo que não existe ou um subcomando que o parser não oferece.

- categoria: consistency
- severidade padrão: high
- o que verifica: Um comando documentado usa um script do alvo que não existe ou um subcomando que o parser não oferece.
- como: _check_documented_commands: cruza caminhos scripts/ usados em invocações com o sistema de arquivos e com o parser.

  Formatos reconhecidos como invocação: span de código inline, linha de bloco cercado e linha com prompt, desde que iniciados por interpretador (`python`, `python3`, `py`, `bash`, `sh`, `node`, `npx`, `uv`, `pwsh`, `powershell`) ou por `./`, `$` e `>`. Citação em prosa, mesmo entre crases, não é invocação: fica com SQA-013.
- limites: A leitura do parser é textual; subcomandos montados em tempo de execução aparecem como unverifiable. Um caminho inexistente é sinal mesmo que o nome pareça ilustrativo (foo, bar): o nome não decide se o caminho existe. Comandos encontrados no alvo nunca são executados.

### SQA-018 — Uma plataforma declarada no frontmatter não tem nenhuma evidência de exercício na documentação.

- categoria: claims
- severidade padrão: medium
- o que verifica: Uma plataforma declarada no frontmatter não tem nenhuma evidência de exercício na documentação.
- como: _check_platform_evidence: procura menções de validação por plataforma nos documentos do alvo.
- limites: Resultado reportado, não executado: declaração de plataforma não é prova de funcionamento. A evidência é textual: menção negada (não testado, never verified) não conta, e um arquivo de teste que cita a plataforma conta como menção, não como execução.

### SQA-019 — Um texto do alvo contém algo com formato de credencial.

- categoria: privacy
- severidade padrão: critical
- o que verifica: Um texto do alvo contém algo com formato de credencial.
- como: _check_privacy: expressões de chaves, tokens e cabeçalhos de autorização, com valor suprimido. Placeholders de template (`{TOKEN}`), variáveis de ambiente e expressões de código não contam como segredo.
- limites: Detecção por formato: o achado informa a classe e a localização, nunca o valor. Em tests/ e fixtures/ a confiança cai para sinal e o auditor confirma a origem; a severidade não é reduzida pelo caminho.

### SQA-020 — Um texto do alvo contém caminho de usuário real, e-mail ou identificador pessoal.

- categoria: privacy
- severidade padrão: high
- o que verifica: Um texto do alvo contém caminho de usuário real, e-mail ou identificador pessoal.
- como: _check_privacy: padrões de caminho de usuário, e-mail e documento, com valor suprimido.
- limites: Placeholders genéricos são ignorados; o achado informa classe e localização, nunca o valor. Em tests/ e fixtures/ a confiança cai para sinal: o auditor confirma se o dado é real, sem reduzir a severidade pelo caminho.

### SQA-021 — Um texto do alvo cita identificador de sessão no formato do Hermes.

- categoria: privacy
- severidade padrão: high
- o que verifica: Um texto do alvo cita identificador de sessão no formato do Hermes.
- como: _check_privacy: padrão de identificador de sessão, com valor suprimido.
- limites: O identificador é suprimido no relatório, mesmo quando aparece em documentação pública. Em tests/ e fixtures/ a confiança cai para sinal: o auditor confirma se o identificador é real, sem reduzir a severidade pelo caminho.

### SQA-022 — Um texto do alvo cita host, domínio ou faixa de rede não pública.

- categoria: privacy
- severidade padrão: medium
- o que verifica: Um texto do alvo cita host, domínio ou faixa de rede não pública.
- como: _check_privacy: padrões de domínio interno e de faixas privadas IPv4.
- limites: Nomes de repositório privado sem formato próprio não são detectáveis: cobrir na revisão semântica.

### SQA-023 — A versão citada no README diverge da versão do frontmatter.

- categoria: consistency
- severidade padrão: medium
- o que verifica: A versão citada no README diverge da versão do frontmatter.
- como: _check_version_divergence: compara version do frontmatter com as versões citadas no README.
- limites: Só compara versões em formato MAJOR.MINOR.PATCH presentes nos textos.

### SQA-024 — A documentação contém marcadores de trabalho inacabado (TODO, TBD, FIXME).

- categoria: documentation
- severidade padrão: low
- o que verifica: A documentação contém marcadores de trabalho inacabado (TODO, TBD, FIXME).
- como: _check_placeholders: procura marcadores em texto fora de blocos de código.

  Duas formas contam como citação do vocabulário, não como pendência: o marcador sozinho dentro de span inline ou aspas (`TODO`, 'TBD'), e lista entre parênteses com dois ou mais marcadores em linha que nomeia a classe ("marcadores (TODO, TBD, FIXME)").
- limites: Citação em prosa, sem crases nem aspas, continua sinalizada: o auditor confirma. Citação em bloco cercado não é avaliada. Documento ilegível não sustenta ausência de marcador: entra como limite por arquivo, e a contagem no motivo cobre só os documentos legíveis.

### SQA-025 — A skill afirma isolamento, somente leitura, ausência de rede ou outra garantia forte.

- categoria: claims
- severidade padrão: medium
- o que verifica: A skill afirma isolamento, somente leitura, ausência de rede ou outra garantia forte.
- como: _check_capability_claims: sinal automático sobre a alegação; o agente confirma ou descarta em SQA-105. Ficam fora da varredura apenas títulos de seção (a alegação está na frase abaixo) e listas de metadados (tags, keywords, category). Documentos gerados e frases que citam testes, manifestos ou checksums continuam na varredura: nomear uma evidência não comprova a alegação.
- limites: O sinal não decide se a alegação é verdadeira, apenas onde olhar. Uma linha apoiada em manifesto ou teste permanece reported até que o agente confira a evidência. O auditor não executa o alvo: alegações não executadas permanecem reported ou unverifiable. Blocos cercados ficam fora da varredura: são exemplo de texto, não promessa da skill. Documento ilegível entra como limite por arquivo: ausência de alegação só é afirmada sobre os documentos legíveis.

### SQA-026 — O manifesto do alvo difere do esperado: mudou entre o início e o fim da auditoria, ou divergiu do manifesto de referência passado em --check-unchanged.

- categoria: integrity
- severidade padrão: critical
- o que verifica: O manifesto do alvo difere do esperado: mudou entre o início e o fim da auditoria, ou divergiu do manifesto de referência passado em --check-unchanged.
- como: audit_skill.py compara o manifesto antes e depois de rodar as verificações e, com --check-unchanged, compara o manifesto final com a referência.
- limites: Cobre a janela desta execução e a referência informada; não detecta alterações anteriores à auditoria nem divergências contra referências que não foram passadas.

### SQA-027 — O frontmatter usa construção que o backend restrito não decide (bloco literal, âncora, mapa em fluxo) ou tem indentação inconsistente.

- categoria: metadata
- severidade padrão: info
- o que verifica: O frontmatter usa construção que o backend restrito não decide (bloco literal, âncora, mapa em fluxo) ou tem indentação inconsistente.
- como: _parse_restricted: registra a construção e a linha em vez de adivinhar o valor; indentação que não encaixa em nenhum nível aberto é registrada como invalid_indentation, sem anexar o valor ao nó errado.
- limites: Item não verificável: exige conferência manual ou o backend pyyaml. Não é aprovação nem defeito.

## Verificações semânticas

Conduzidas pelo agente a partir das evidências coletadas; o script entrega o checklist e não as executa. A conclusão de cada item registra a evidência usada.

### SQA-101 — Nome, descrição, requisitos declarados e procedimento contam a mesma história.

- categoria: semantic
- severidade padrão: medium
- o que verifica: Nome, descrição, requisitos declarados e procedimento contam a mesma história.
- como: Agente: lê SKILL.md e o README e confronta promessa, pré-requisitos e passos.
- limites: Julgamento humano; o script não emite este código, apenas o lista no checklist.

### SQA-102 — O README descreve o comportamento real sem exagerar capacidades.

- categoria: semantic
- severidade padrão: medium
- o que verifica: O README descreve o comportamento real sem exagerar capacidades.
- como: Agente: compara o README com o procedimento e com o código entregue.
- limites: Julgamento humano; depende da leitura integral dos arquivos.

### SQA-103 — Exemplos parecem ilustrativos e não dados reais do autor.

- categoria: semantic
- severidade padrão: medium
- o que verifica: Exemplos parecem ilustrativos e não dados reais do autor.
- como: Agente: avalia se caminhos, nomes e saídas dos exemplos são claramente fictícios.
- limites: Julgamento humano; os padrões automáticos cobrem apenas formatos reconhecíveis.

### SQA-104 — Os testes exercitam comportamento em vez de repetir a implementação.

- categoria: semantic
- severidade padrão: medium
- o que verifica: Os testes exercitam comportamento em vez de repetir a implementação.
- como: Agente: lê os testes e verifica se as asserções têm poder de falha.
- limites: Julgamento humano; executar os testes do alvo exige autorização explícita.

### SQA-105 — Garantias de segurança afirmadas pela skill correspondem ao que o código faz.

- categoria: semantic
- severidade padrão: high
- o que verifica: Garantias de segurança afirmadas pela skill correspondem ao que o código faz.
- como: Agente: confronta cada alegação de isolamento com as chamadas de escrita, rede e processo.
- limites: Julgamento humano; sem execução, a conclusão é reported ou unverifiable.

### SQA-106 — Cada severidade atribuída se justifica pelo impacto e pelo alcance, não pela categoria.

- categoria: semantic
- severidade padrão: low
- o que verifica: Cada severidade atribuída se justifica pelo impacto e pelo alcance, não pela categoria.
- como: Agente: revisa severity_justification dos achados e corrige os que não se sustentam.
- limites: Julgamento humano; não existe nota numérica universal nem selo automático de segurança.

### SQA-107 — Avalia se a independent-code-review é pertinente e o que deve ser entregue a ela.

- categoria: semantic
- severidade padrão: info
- o que verifica: Avalia se a independent-code-review é pertinente e o que deve ser entregue a ela.
- como: Agente: decide o encaminhamento conforme o risco e o tamanho do delta auditado.
- limites: A auditoria não substitui a revisão independente nem a declara como feita.
