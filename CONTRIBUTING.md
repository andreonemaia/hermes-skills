# Como contribuir

Obrigado por considerar uma contribuição. Estas regras são intencionalmente
curtas. Elas existem para que toda skill publicada seja segura de carregar,
reproduzível em uma máquina limpa e honesta sobre o que suporta.

## Regras do repositório

1. **Uma skill por diretório.** Uma skill fica em
   `skills/<skill-name>/SKILL.md`. Não junte procedimentos sem relação em uma
   única skill.
2. **`SKILL.md` é obrigatório.** Ele precisa ter frontmatter YAML válido com
   `name`, `description`, `version` e `platforms`, além de um corpo em Markdown
   que descreva o procedimento.
3. **A `description` é um gatilho.** Os primeiros 57 caracteres devem funcionar
   sozinhos como `Use when <trigger>. <one-line behavior>.`, porque é isso que o
   agente lê ao decidir se carrega a skill.
4. **A documentação precisa ser clara.** Informe pré-requisitos, comandos
   exatos, o critério de conclusão, as armadilhas conhecidas e um checklist de
   verificação.
5. **Nenhum segredo.** Nunca inclua chaves de API, tokens, senhas, chaves
   privadas, cookies ou conteúdo de `.env`.
6. **Nenhum dado pessoal.** Nada de caminhos pessoais, nomes de usuário, nomes
   de máquina, IDs de sessão ou endereços de e-mail.
7. **Nenhuma referência privada.** Nada de nomes de repositórios privados,
   hostnames internos, URLs internas ou referências a infraestrutura não
   pública. Exemplos genéricos são bem-vindos; deixe a generalidade explícita.
8. **Declare apenas plataformas testadas.** Liste uma plataforma em `platforms`
   somente se a skill foi realmente executada nela. Não afirme compatibilidade
   que você não testou.
9. **Toda mudança passa por branch e pull request.** Nunca faça push direto na
   `main`.
10. **Revisão independente antes do merge.** Um pull request que adiciona ou
    altera uma skill precisa passar por uma revisão independente (modelo e
    contexto diferentes dos do autor) antes do merge.
11. **Não afirme compatibilidade não testada.** O mesmo princípio da regra 8,
    aplicado às afirmações no corpo da skill e no changelog.

## Idioma da documentação

Esta é uma regra permanente do repositório, e vale para toda contribuição nova
ou existente.

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
  descoberta e execução. A obrigação de PT-BR vale para a **documentação humana**
  (o `README.md` da raiz, os `README.md` das skills e os guias em `docs/`).

## Adicionando uma skill

```bash
git checkout -b feat/<skill-name>
mkdir -p skills/<skill-name>
# add SKILL.md (and README.md if the skill needs a human-facing explanation)
git add skills/<skill-name>
git commit -m "feat: add <skill-name> skill"
git push -u origin HEAD
gh pr create --title "Add <skill-name> skill" --fill
```

A descrição do pull request deve cobrir: motivação, como a skill funciona,
plataforma testada, considerações de segurança, limitações, validações
realizadas e o checklist de publicação.

## Checklist de contribuição e revisão

Antes de abrir o pull request, confirme:

- [ ] o frontmatter faz parse e `name`, `description`, `version`, `platforms` estão presentes
- [ ] `metadata.hermes.tags` e `metadata.hermes.category` estão definidos
- [ ] nenhum segredo, nenhum caminho pessoal, nenhum nome de usuário, nenhum ID de sessão
- [ ] nenhuma referência a repositórios ou infraestrutura privada
- [ ] o corpo é genérico o suficiente para outro usuário do Hermes executar como está
- [ ] o `README.md` (da skill, se existir) é consistente com o `SKILL.md`
- [ ] as plataformas declaradas foram realmente testadas
- [ ] a documentação humana está em PT-BR e mantém identificadores técnicos inalterados
- [ ] a skill apresenta pelo menos um exemplo simples de uso em linguagem natural
- [ ] os exemplos foram atualizados se o comportamento da skill mudou

## Licença das contribuições

Ao contribuir, você concorda que o seu trabalho é licenciado sob a licença MIT
deste repositório.
