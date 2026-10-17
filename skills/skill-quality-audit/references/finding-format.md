# Formato de achado

Um achado é o resultado de uma verificação sobre um alvo. Ele é rastreável: diz
qual verificação o produziu, onde, com que evidência, com que severidade
justificada e em que estado de validação.

## Campos

| campo | tipo | sempre presente | significado |
| --- | --- | --- | --- |
| `id` | texto | sim | código da verificação que produziu o achado (por exemplo `SQA-013`) |
| `check` | texto | sim | título da verificação, para leitura humana |
| `category` | texto | sim | categoria da verificação: `structure`, `documentation`, `consistency`, `security`, `privacy`, `semantic` |
| `method` | texto | sim | `structural` (executada pelo script) ou `semantic` (julgada pelo agente) |
| `severity` | texto | sim | `info`, `low`, `medium`, `high` ou `critical` |
| `severity_justification` | texto | sim | por que esta severidade, e não a padrão da verificação |
| `file` | texto | sim | caminho relativo ao alvo |
| `line` | número ou nulo | sim | linha, quando a evidência tem localização pontual |
| `location` | texto | sim | `arquivo:linha` ou só o arquivo |
| `evidence` | texto | sim | o que foi observado, já sanitizado e limitado a 200 caracteres |
| `impact` | texto | sim | o que a ocorrência custa a quem usa ou publica a skill |
| `recommendation` | texto | sim | ação sugerida, sempre revisável por uma pessoa |
| `validation` | texto | sim | `executed`, `reported`, `unverifiable` ou `not_run` |
| `confidence` | texto | sim | `confirmed` (observado no alvo) ou `signal` (precisa de confirmação do agente) |
| `needs_agent_confirmation` | booleano | sim | verdadeiro quando `confidence` é `signal` |
| `target` | texto ou nulo | sim | subcaminho do alvo, quando o achado aponta para um item específico |

## Severidades

Da menor para a maior: `info`, `low`, `medium`, `high`, `critical`.

- `info` registra algo que o auditor precisa saber, sem defeito atribuído;
- `critical` fica para o que impede a publicação ou contamina o alvo;
- severidade diferente da padrão da verificação exige justificativa explícita no
  campo `severity_justification`.

A opção `--fail-on` escolhe a severidade mínima que produz código de saída 1.
O padrão é `high`; `never` desliga o efeito no código de saída sem esconder
achados do relatório.

## Estados de validação

| estado | significado |
| --- | --- |
| `executed` | uma verificação foi executada sobre o alvo e observou o que o achado descreve |
| `reported` | o resultado vem de declaração do próprio alvo ou de terceiro, e não foi reproduzido |
| `unverifiable` | não há evidência suficiente para decidir com o material disponível |
| `not_run` | a verificação não se aplica ou não pôde ser executada, com motivo registrado |

Falta de evidência não é aprovação nem prova automática de defeito: um item sem
verificação aparece como `not_run` ou `unverifiable`, nunca como ausência de
achado.

## Confiança

- `confirmed`: o script observou diretamente o fato no alvo.
- `signal`: a heurística apontou uma possibilidade que depende de confirmação
  humana. Achados de idioma, de privacidade em arquivo de teste e de alegação de
  capacidade são sinais, não veredictos.

## Exemplo de achado

Exemplo sanitizado, produzido pela verificação SQA-013 em um alvo de teste. O
alvo não é reproduzido aqui e nenhum valor do alvo aparece:

```json
{
  "id": "SQA-013",
  "check": "Referência local quebrada",
  "category": "documentation",
  "method": "structural",
  "severity": "high",
  "severity_justification": "referência citada na documentação que não existe no alvo",
  "file": "README.md",
  "line": 42,
  "location": "README.md:42",
  "evidence": "referência citada não existe no alvo: references/inexistente.md",
  "impact": "quem segue a documentação não encontra o arquivo e perde a instrução",
  "recommendation": "corrigir a referência ou criar o arquivo citado",
  "validation": "executed",
  "confidence": "confirmed",
  "needs_agent_confirmation": false,
  "target": null
}
```

## Códigos de saída

| código | quando |
| --- | --- |
| 0 | auditoria concluída sem achado na severidade mínima pedida |
| 1 | há achado na severidade mínima pedida |
| 2 | alvo inexistente ou não é diretório |
| 3 | uso incorreto da linha de comando |
| 4 | falha ao gravar a saída pedida |
| 5 | o alvo mudou durante a auditoria, ou divergiu do manifesto de referência |
| 6 | falha inesperada do próprio auditor; nenhum relatório foi produzido |

O código de saída resume o efeito combinado com `--fail-on`; ele não substitui a
leitura do relatório.
