# Desenvolvimento

Como escrever, estruturar e versionar uma skill que se encaixa neste
repositório.

## Anatomia de uma skill

```
skills/<skill-name>/
├── SKILL.md      # required
├── README.md     # optional, human-facing
├── scripts/      # optional, helpers the skill runs
└── tests/        # optional, automated tests for scripts/
```

Se a skill entrega scripts, eles ficam em `scripts/` e os testes deles em
`tests/`, usando a biblioteca padrão, para que qualquer leitor possa executá-los
sem instalar nada. Veja `docs/testing.md` para saber o que esses testes precisam
satisfazer.

### Frontmatter do `SKILL.md`

```yaml
---
name: <skill-name>                 # must match the directory name
description: "Use when <trigger>. <one-line behavior>."
version: 1.0.0                     # semantic versioning, per skill
author: <author or project>
license: MIT
platforms: [windows]               # only platforms actually tested
metadata:
  hermes:
    tags: [tag-a, tag-b]
    category: <category>
    related_skills: [other-skill]
---
```

Regras que importam:

- **`name`** deve ser igual ao nome do diretório. Uma divergência quebra a
  descoberta da skill.
- **`description`** é a única coisa que o agente lê antes de decidir carregar a
  skill. Mantenha os primeiros 57 caracteres autossuficientes: `Use when
  <trigger>.` seguido de uma frase curta sobre o comportamento.
- **`platforms`** lista o que você testou. Omita o que não foi testado. É melhor
  declarar menos do que reivindicar um suporte que você não consegue reproduzir.
- **`version`** é independente por skill. Incremente sempre que o comportamento
  mudar.

### Estrutura do corpo

Um corpo que funciona bem na prática tem estas seções, nesta ordem:

1. **Resumo em um parágrafo** do que a skill faz e do seu princípio central.
2. **Quando usar** e **quando não usar**.
3. **Pré-requisitos**.
4. **Referência rápida**: os poucos comandos que alguém realmente executa.
5. **Procedimento**: passos numerados, cada um terminando em um critério de
   conclusão.
6. **Armadilhas**: erros que foram realmente observados, escritos como uma regra
   mais o motivo.
7. **Verificação**: um checklist para confirmar que a skill fez o trabalho dela.

## Regras de escrita

- **Escreva lições, não registros.** Declare a regra e o motivo. Não narre uma
  sessão de depuração nem cite números de issue e datas no corpo.
- **Prefira comandos exatos a descrições.** Um comando que foi executado e
  funcionou vale mais do que um parágrafo explicando a ideia geral.
- **Declare o critério de conclusão de cada passo.** Se um passo não pode
  falhar, ele não é um passo.
- **Documente comportamento dependente de versão como dependente de versão.** Se
  um comportamento foi validado contra uma versão específica da CLI do Hermes,
  diga isso e instrua a revalidação quando a CLI mudar. Nunca apresente um
  comportamento validado como garantia permanente de API.
- **Não afirme compatibilidade não testada.** Nem no corpo, nem no changelog,
  nem em `platforms`.
- **Nenhum dado pessoal ou privado.** Nada de caminhos pessoais, nomes de
  usuário, nomes de máquina, IDs de sessão, tokens ou nomes de repositórios
  privados. Converta qualquer coisa específica em um exemplo claramente
  genérico.
- **Mantenha tudo genérico.** Outro usuário do Hermes precisa conseguir executar
  a skill como ela está. Se um passo depende do seu ambiente, transforme isso em
  pré-requisito, não em suposição.
- **Mantenha os identificadores emitidos documentados.** Quando o código de uma
  skill emite identificadores que precisam aparecer na documentação (IDs de
  achado, códigos de saída, estados, tipos de evento), mantenha os dois em
  sincronia e considere adicionar um teste automatizado de consistência, para
  que a divergência não passe despercebida. Uma tabela mantida à mão parece
  completa e se desatualiza em silêncio: a skill `task-checkpoint-recovery`
  emitia `op_planned_landed` enquanto nem a tabela do schema nem o README o
  listavam. Veja `docs/testing.md` para saber como escrever esse teste.
- **Mantenha os exemplos de uso atualizados.** Toda skill apresenta, na sua
  documentação humana, pelo menos um exemplo simples de pedido em linguagem
  natural, e uma mudança de comportamento atualiza os exemplos no mesmo pull
  request.

## Idioma da documentação

Regra permanente do repositório. Ela vale para toda contribuição nova ou
existente.

- O `README.md` da raiz é mantido em **português brasileiro (PT-BR)**.
- Todo `README.md` de skill é escrito e atualizado em PT-BR.
- Toda nova skill deve apresentar pelo menos um **exemplo simples de uso em
  linguagem natural**, curto e pronto para copiar e colar no Hermes.
- Mudanças de funcionalidade atualizam os exemplos correspondentes no mesmo pull
  request.
- Os textos explicativos priorizam clareza e acessibilidade, sem sacrificar a
  precisão técnica.
- Nomes de comandos, APIs, parâmetros, caminhos, estruturas JSON e
  identificadores técnicos permanecem **inalterados**. Não traduza código nem
  identificadores.
- URLs, âncoras, links relativos, comandos válidos e informações sobre
  plataformas realmente testadas são preservados.
- Os arquivos `SKILL.md` e as demais instruções internas dos agentes podem
  continuar em inglês quando isso for mais apropriado para compatibilidade,
  descoberta e execução. A obrigação de PT-BR vale para a **documentação
  humana**: o `README.md` da raiz, os `README.md` das skills e os guias em
  `docs/`.

## Suporte a Windows

Uma skill pode ser declarada exclusiva para `windows`. Se uma skill funciona em
várias plataformas, diga isso apenas depois de executá-la em cada uma. Quando um
procedimento difere por shell, mostre a forma POSIX e a forma PowerShell, em vez
de presumir Bash.

## Versionamento e changelog

- Use [Semantic Versioning](https://semver.org/): `MAJOR` para uma mudança que
  quebra o procedimento ou a interface, `MINOR` para capacidade nova, `PATCH`
  para correções que não mudam o comportamento.
- Adicione uma entrada ao `CHANGELOG.md` do repositório em `Unreleased` e mova-a
  para uma versão publicada quando a mudança for integrada.
- Uma skill, um diretório, uma versão. Não versione um grupo de skills em
  conjunto.

## Branch e pull request

```bash
git checkout -b feat/<skill-name>
# edit skills/<skill-name>/...
git add skills/<skill-name>
git commit -m "feat: add <skill-name> skill"
git push -u origin HEAD
gh pr create --title "Add <skill-name> skill" --fill
```

Nunca faça push direto na `main`. Toda mudança em uma skill é proposta por um
pull request e revisada de forma independente antes do merge (veja
`docs/testing.md`).
