# Instalação

Este guia explica onde o Hermes Agent procura por skills e como instalar uma
skill deste repositório.

## Onde as skills ficam

O Hermes carrega skills a partir do diretório `skills/` dentro do seu diretório
de dados. O local depende da plataforma:

| Plataforma | Diretório de skills |
|----------|------------------|
| Windows | `%LOCALAPPDATA%\hermes\skills` (normalmente `C:\Users\<seu-usuário>\AppData\Local\hermes\skills`) |
| Linux / macOS | `${HERMES_HOME:-$HOME/.hermes}/skills` |

Dentro desse diretório, as skills são agrupadas por categoria, por exemplo
`skills/software-development/`, `skills/github/`. A pasta de categoria é uma
convenção de organização; o que faz um diretório ser uma skill é a presença de um
arquivo `SKILL.md` nele.

```
<skills-dir>/
└── software-development/
    └── independent-code-review/
        ├── SKILL.md
        └── README.md
```

Se você usa um perfil que não é o padrão, o diretório de dados dele é separado,
por exemplo `<hermes-data-dir>/profiles/<profile>/skills`. Execute
`hermes config path` para ver o diretório de dados ativo na sua instalação.

## Instalando uma skill manualmente

1. Clone este repositório (ou baixe apenas o diretório de uma skill):

   ```bash
   git clone https://github.com/andreonemaia/hermes-skills.git
   ```

2. Copie a skill para a pasta de categoria do seu diretório de skills:

   ```bash
   # Linux / macOS
   mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/software-development"
   cp -r hermes-skills/skills/independent-code-review \
         "${HERMES_HOME:-$HOME/.hermes}/skills/software-development/"
   ```

   ```powershell
   # Windows (PowerShell)
   $dest = Join-Path $env:LOCALAPPDATA 'hermes\skills\software-development'
   New-Item -ItemType Directory -Force -Path $dest | Out-Null
   Copy-Item -Recurse hermes-skills\skills\independent-code-review $dest
   ```

3. Verifique que o Hermes enxerga a skill:

   ```bash
   hermes skills list
   ```

   A skill deve aparecer com a sua categoria e o status `enabled`.

## Atualizando uma skill

Substitua o diretório da skill pela versão mais nova deste repositório e execute
`hermes skills list` novamente. Mantenha o nome do diretório idêntico ao `name`
do frontmatter.

## Desinstalando uma skill

Apague o diretório da skill da sua árvore de skills:

```bash
rm -rf "${HERMES_HOME:-$HOME/.hermes}/skills/software-development/independent-code-review"
```

```powershell
Remove-Item -Recurse "$env:LOCALAPPDATA\hermes\skills\software-development\independent-code-review"
```

## Observações

- As skills são lidas no início da sessão. Se você instalar uma skill com uma
  sessão aberta, inicie uma sessão nova (ou recarregue) antes de esperar que o
  comando de barra dela esteja registrado.
- Nenhuma skill deste repositório exige alteração de configuração global. Tudo o
  que uma skill precisa da sua instalação está declarado na seção de
  pré-requisitos dela.

## Próximos passos

Depois de instalar, veja o [README principal](../README.md) para os exemplos de
uso de cada skill e `docs/development.md` se você pretende escrever uma skill
própria.
