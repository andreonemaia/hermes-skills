# Testes e validação

O que verificar antes de publicar uma skill e como este repositório valida uma
mudança.

## Checklist de pré-publicação

Execute isto em toda skill que você adicionar ou alterar.

### 1. Frontmatter

Faça o parse do YAML e confirme que as chaves obrigatórias existem e estão bem
formadas:

```bash
python - <<'PY'
import re, yaml, pathlib, sys
for p in pathlib.Path("skills").rglob("SKILL.md"):
    c = p.read_text(encoding="utf-8")
    assert c.startswith("---\n"), f"{p}: missing frontmatter"
    m = re.search(r"\n---\s*\n", c[3:])
    fm = yaml.safe_load(c[3:m.start() + 3])
    for key in ("name", "description", "version", "platforms"):
        assert key in fm, f"{p}: missing {key}"
    assert fm["name"] == p.parent.name, f"{p}: name != directory"
    hermes = (fm.get("metadata") or {}).get("hermes") or {}
    assert hermes.get("tags"), f"{p}: metadata.hermes.tags missing"
    assert hermes.get("category"), f"{p}: metadata.hermes.category missing"
    print(f"OK {p.parent.name} v{fm['version']} platforms={fm['platforms']}")
PY
```

Verificações realizadas: `name`, `description`, `version`, `platforms`,
`metadata.hermes.tags`, `metadata.hermes.category` e `name == directory`.

### 2. Markdown

- Os títulos estão em ordem e nenhum nível foi pulado.
- Os blocos de código estão balanceados e identificados com a linguagem.
- Os alvos de links relativos resolvem para um arquivo que existe no repositório.
- As tabelas renderizam (número de colunas consistente em cada linha).

### 3. Segredos e dados pessoais

Faça uma busca em toda a skill por qualquer coisa que não deva ser publicada:

```bash
# Personal paths and usernames (adapt the patterns to your environment)
rg -n -i 'C:\\\\Users\\\\|/home/|/Users/|<your-username>|@gmail\.com' skills/
# Credential-shaped strings
rg -n -i 'api[_-]?key|token|secret|password|BEGIN [A-Z ]*PRIVATE KEY|ghp_|gho_|sk-' skills/
# Session identifiers and private infra
rg -n -i 'session[_-]?id|internal\.|\.corp|10\.|192\.168\.' skills/
```

Todo resultado é removido ou convertido em um exemplo claramente genérico. Um
segredo nunca é "provavelmente aceitável".

### 4. Honestidade sobre plataformas

Para cada plataforma em `platforms`, confirme que a skill foi realmente executada
nela. Remova qualquer plataforma que não foi testada. Faça também uma leitura do
corpo e do changelog procurando frases que insinuem compatibilidade não testada.

### 5. Generalidade

Leia a skill como se você fosse outro usuário do Hermes em uma máquina limpa.
Qualquer passo que só funciona por causa da sua configuração local deve virar um
pré-requisito documentado.

### 6. Consistência entre `README.md` e `SKILL.md`

O README da skill explica o mesmo comportamento que o `SKILL.md`, sem duplicá-lo
por inteiro. Se a versão ou os comandos divergirem entre os dois arquivos, a
skill está errada.

### 7. Testes automatizados

Uma skill que entrega scripts deve entregar testes para eles, no diretório
`tests/` da própria skill, usando a biblioteca padrão (`unittest`), de modo que
executá-los não exija instalar nenhuma dependência:

```bash
cd skills/<skill-name>
python -m unittest discover -s tests -t tests
```

Requisitos:

- **Sem rede e sem serviço ativo.** Qualquer coisa que fosse acessar a rede é
  simulada ou ignorada. Um teste que precisa de um servidor em execução não é um
  teste, é um passo manual, e o lugar dele é o checklist de verificação da skill.
- **Isolado da máquina hospedeira.** Os testes não devem ler a configuração
  real, o diretório pessoal nem os aplicativos instalados da máquina. Se o
  código testado descobre caminhos, fixe essa descoberta em um diretório
  temporário na preparação do teste. Uma suíte que passa só por causa da máquina
  do autor não é evidência.
- **Determinístico.** Sem esperas, sem dependência de relógio, sem suposições de
  ordem.
- **A suíte passa antes de a mudança ser proposta.** Relate o comando exato e o
  resultado observado no pull request.

### 8. Consistência entre código e documentação

Quando o código emite identificadores que precisam ser documentados (IDs de
achado, códigos de saída, estados, tipos de evento), adicione um teste que
garanta que todo identificador emitido está documentado, com a mesma severidade
ou o mesmo significado. A regra de escrita está em `docs/development.md`.

- **Derive os identificadores do código**, não de uma segunda lista escrita à
  mão, para que emitir um identificador sem documentá-lo quebre a suíte.
- **Garanta que a varredura encontrou um conjunto não vazio**, para que uma
  reformatação da documentação não transforme o teste em algo que passa para
  sempre sem verificar nada.
- **Faça o parse da estrutura que você precisa ler, não de linhas inteiras.** Uma
  expressão regular baseada em linhas tanto deixou passar identificadores
  agrupados em uma única célula de tabela quanto casou com tabelas não
  relacionadas, falhando em documentação correta. Leia as células da tabela.

Implementação de referência: o teste de consistência da suíte da skill
`task-checkpoint-recovery`, que varre `scripts/` em busca dos IDs de achado
emitidos e exige cada um deles, com a severidade real, em
`references/schema.md` e em `README.md`.

### 9. Documentação em PT-BR e exemplos de uso

A regra de idioma é canônica em `CONTRIBUTING.md`, com a versão detalhada para
autores de skill em `docs/development.md`. Antes de publicar, confirme que a
mudança a respeita:

- a documentação humana alterada está em **português brasileiro (PT-BR)**;
- toda skill tocada traz pelo menos um **exemplo simples de uso em linguagem
  natural**, curto e pronto para copiar e colar no Hermes;
- se o comportamento da skill mudou, os exemplos foram atualizados no mesmo pull
  request;
- comandos, APIs, parâmetros, caminhos, estruturas JSON e demais identificadores
  técnicos continuam inalterados dentro do texto em português;
- os links relativos continuam resolvendo depois da tradução (veja o item 2) e os
  exemplos citados correspondem à implementação atual.

## Revisão independente

Uma mudança em uma skill precisa passar por uma revisão independente, feita por
um modelo e um contexto diferentes, antes do merge. A própria skill
`independent-code-review` deste repositório existe exatamente para isso: ela
executa o revisor em um processo separado e isolado, sem ferramentas e sem
contexto compartilhado, e produz achados classificados por severidade.

A revisão deve cobrir: segurança; vazamento de informação privada; generalidade;
correção técnica; documentação; instruções potencialmente perigosas;
compatibilidade declarada; falsa sensação de somente leitura; dependências de
versão; excesso de engenharia; e prontidão para publicação em código aberto.

## Validação dos achados

A saída do revisor não é verdade. Para cada achado, o autor deve:

- analisá-lo em relação ao código e aos requisitos reais;
- reproduzi-lo quando possível (executar testes é permitido; editar durante a
  revisão não é);
- classificá-lo como **confirmado**, **rejeitado** ou **inconclusivo**.

Achados confirmados de severidade `critical`, `high` ou `medium` são corrigidos e
a mudança é revalidada e revisada de novo. Achados de severidade baixa ou
informativos podem ser aceitos deliberadamente, quando documentados.

## Ciclo de verificação manual

```bash
# 1. install the changed skill into a scratch profile or a test Hermes home
# 2. run the procedure the skill describes, end to end
# 3. confirm the verification checklist in SKILL.md actually passes
# 4. confirm nothing outside the intended scope was changed
git status --short
```

Se a skill mexe com Git ou GitHub, confirme depois que nenhum branch, commit,
arquivo ou remote mudou de forma inesperada.
