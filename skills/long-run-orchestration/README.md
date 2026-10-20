# Long-Run Orchestration

Skill para conduzir um objetivo longo como um contrato versionado e um grafo
Kanban com gates: spec, trabalho, revisão independente, consolidação e aprovação
humana.

## Por que existe

Um agente não deve manter um objetivo de horas apenas no contexto da conversa.
Esta skill materializa o objetivo em cartões e torna cada etapa retomável:

- o contrato (JSON) é a fonte de verdade;
- os cartões carregam um marcador com `objective_id`, `step` e `spec_sha256`;
- a aplicação é idempotente: reexecutar encontra e reutiliza cartões; vínculos
  ausentes só são refeitos enquanto o grafo continua consistente com o contrato;
- divergência de marcador, pai inesperado ou cartão já liberado antes dos pais
  interrompe a aplicação com diagnóstico, sem reparo automático;
- o spec e o gate de aprovação nascem bloqueados; o gate usa o sentinela
  `lro-human-approval`, que não é perfil e por isso não é despachado;
- nenhum filho executável é liberado antes de o grafo estar completo;
- a conclusão é verificada contra critérios e evidências declarados.

## Instalação

Copie `long-run-orchestration/` para o diretório de skills do seu perfil Hermes.
A skill usa apenas a CLI `hermes kanban` e a biblioteca padrão do Python.

## Uso rápido

Copie `examples/objective.example.json` para `objective.json` e substitua os
placeholders `your-profile`, `your-provider`, `executor-model-placeholder` e
`reviewer-model-placeholder`. O exemplo é ilustrativo: não o aplique sem revisar
o executor e o revisor do seu ambiente.

```bash
# 1. valide e veja o plano
python scripts/lro_objective.py apply --spec objective.json --dry-run

# 2. crie ou retome o grafo
python scripts/lro_objective.py apply --spec objective.json --json

# 3. acompanhe (uma leitura por chamada)
python scripts/lro_watchdog.py --spec objective.json --json

# 4. verifique evidências
python scripts/lro_objective.py verify --spec objective.json --json
```

O estado de retomada fica em `.lro/objective.json`, ao lado do objetivo.

## Códigos de saída

| Código | Significado |
|---|---|
| `0` | sucesso / tudo concluído |
| `1` | critério ou evidência reprovada |
| `2` | aguardando aprovação humana |
| `3` | erro de contrato |
| `4` | erro do adaptador ou da CLI nativa |

O watchdog usa `0` para um tick que conseguiu ler o board; `2` indica gateway
indisponível; `3`/`4` indicam erro de contrato ou do adaptador. O veredito
operacional está no campo `state` do JSON, não apenas no código de saída.

## Segurança

- Não conclua o cartão `approval` por código. Ele é um gate humano.
- Não faça merge, release ou instalação como parte desta skill.
- Não edite cartões para pular etapas: corrija o objetivo e reaplique.
- O perfil Hermes ativo não é alterado; a skill opera no board selecionado.

## Documentação

- `SKILL.md`: procedimento para o agente.
- `references/schema.md`: schema completo e regras de validação.
- `references/kanban-contract.md`: contrato nativo observado e evidências.
- `references/operations.md`: estado local, sinais do watchdog e limites.

## Testes

```bash
python -m unittest discover -s tests -t tests
```
