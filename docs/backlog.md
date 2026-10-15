# Backlog

Propostas aceitas e ainda **não implementadas**. Nada aqui descreve o
comportamento atual das skills: cada item precisa passar pelo fluxo normal
(branch, testes, revisão) antes de sair do papel.

## skill-execution-report: separar a força da evidência em quatro níveis

**Status:** proposto, não implementado (a v0.1.0 mantém o comportamento atual).
**Origem:** fechamento da investigação A×B sobre `include_children`.

### Problema

`execution_proven` empacota duas afirmações diferentes em um único estado: "houve
invocação com sucesso observado" e "existe um artefato correlacionado e
verificável". `artifact_file` só prova que o arquivo existia no momento da
verificação e `artifact_commit` só prova que o commit existe no repositório
local. Nenhum dos dois prova que a execução **criou** ou **modificou** o
artefato, nem que a modificação tem proveniência causal ligada àquela execução.
Um script que apenas imprime o caminho de um arquivo existente produz a mesma
evidência de um que o escreveu.

### Proposta

Separar quatro afirmações, hoje colapsadas. São quatro afirmações a separar
futuramente: **atualmente apenas as duas primeiras são combinadas**, e só elas,
em `execution_proven`.

1. **Invocação comprovada de ferramenta:** uma chamada atribuível à skill foi
   observada (hoje coberto por `invoked`). O resultado pode ser sucesso, falha ou
   desconhecido: observar o resultado não é obrigatório para reconhecer a
   invocação.
2. **Existência verificada de artefato:** o arquivo ou commit referenciado existe
   no momento da verificação (hoje coberto por `artifact_file` e
   `artifact_commit`).
3. **Modificação observada de artefato:** uma mudança foi detectada entre
   observações do artefato, sem atribuição causal automática (novo).
4. **Proveniência causal comprovada da modificação:** há evidências que sustentam
   atribuir essa mudança àquela execução específica, e não a outra origem (novo).

Os níveis 3 e 4 são independentes: detectar uma mudança não diz quem a causou.
Diferenças de conteúdo, hashes ou snapshots, isoladamente, não comprovam quem
alterou o artefato. Eles sustentam o nível 3 e só passam a sustentar o nível 4
quando acompanhados de correlação com a execução específica.

Evidências candidatas:

- **Nível 3 (mudança observada):** diff do arquivo antes e depois, quando houver
  cópia ou histórico disponível; snapshots antes e depois, capturados em torno da
  execução; hashes (tamanho, mtime, SHA-256) comparados entre antes e depois.
- **Nível 4 (proveniência causal):** registros de escrita emitidos pela própria
  ferramenta, por exemplo um caminho de arquivo na saída acompanhado de um evento
  de escrita; correlação com IDs de execução (por exemplo `tool_call_id`), ligando
  a alteração à chamada que a produziu.

### Restrições de compatibilidade

- Não alterar o esquema `skill-execution-report/v1` nem os nomes de classificação
  existentes nesta linha de versão: os consumidores atuais dependem deles.
- O comportamento novo, se implementado, entra como informação adicional (campos
  ou estados novos) em uma versão que os consumidores possam adotar
  explicitamente, com os estados atuais preservados.
- A separação não pode afrouxar a regra atual: nada é promovido sem evidência que
  sustente a afirmação mais forte.

### Fora de escopo

- Não implementar nesta etapa.
- Não alterar `classify_scope` nem a ordem de decisão sem um defeito
  reproduzível que o justifique.
