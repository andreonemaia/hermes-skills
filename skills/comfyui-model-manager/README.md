# comfyui-model-manager

Encontre uma instalação do ComfyUI, faça o inventário dos arquivos de modelo que
ela já tem, confira um manifesto contra eles e baixe apenas o que falta,
**sempre depois de conferir o digest SHA-256**. Também valida um workflow contra
um servidor em execução antes de gastar tempo de GPU com um prompt que não vai
carregar.

Esta skill complementa a skill oficial `comfyui`, que cobre *executar* workflows.
Esta aqui cobre as duas falhas que acontecem antes de uma execução começar:
modelos ausentes, truncados ou duplicados, e grafos que referenciam nós ou
arquivos que não existem.

## O que é

Um conjunto de scripts que responde a três perguntas **sem tocar na GPU**: *o que
está em disco*, *aquilo é realmente o que diz ser*, e *esse grafo vai carregar*.

O problema que ela resolve: uma pasta de modelos do ComfyUI é uma centena de
gigabytes de arquivos baixados de vários lugares ao longo de meses, e um workflow
é um grafo JSON que presume, em silêncio, que todos eles estão presentes e
corretos. Os dois falham tarde e caro:

- Um download interrompido deixa um arquivo com o nome certo e os bytes errados.
  O ComfyUI carrega até certo ponto e falha com um erro de tensor confuso ou,
  pior, produz resultado sem sentido.
- Um workflow é enviado, entra na fila e só então é rejeitado, porque uma classe
  de nó foi renomeada ou um checkpoint está na pasta errada.
- Checkpoints duplicados se acumulam, porque nada nunca comparou digests.

### O que a skill entrega

| Script | Finalidade | Precisa de servidor |
|--------|-----------|----------------|
| `scripts/comfy_env.py` | Descobre a instalação, a versão da API e cada raiz e pasta de modelos. | Opcional (`--base-url` para uma instância remota) |
| `scripts/comfy_models.py` | `inventory`, `verify` e `install` a partir de um manifesto JSON. | Não |
| `scripts/comfy_validate.py` | Valida estaticamente um workflow (formato API ou UI) contra `/object_info`. | Sim |
| `scripts/_common.py` | Auxiliares compartilhados: segurança de caminhos, hashing, leitura de configuração, HTTP. | - |

Somente biblioteca padrão. Sem `pip install` e sem importar o próprio ComfyUI.

## Quando usar

- Antes de baixar dezenas de gigabytes, para conferir o que já existe e evitar
  baixar de novo.
- Quando um modelo parece corrompido e você precisa saber se os bytes conferem
  com o digest esperado.
- Quando você suspeita de checkpoints duplicados ocupando espaço.
- Antes de enviar um workflow para a fila, para saber se o grafo carrega e se os
  arquivos que ele cita existem.

## Exemplo rápido de uso

```
Use comfyui-model-manager para me dizer quais modelos já estão instalados e quais checkpoints estão duplicados.
```

O agente roda `comfy_env.py --json`, depois `comfy_models.py inventory
--duplicates`, e relata as pastas, o tamanho total e os grupos de duplicatas.

## Como funciona

```
comfy_env.py  --json          # where is ComfyUI, what folders exist
        │
        ▼
comfy_models.py inventory     # what is already on disk (--duplicates, --hash)
        │
        ▼
comfy_models.py verify        # manifest vs disk -> ok / missing / partial / conflict
        │
        ▼
comfy_models.py install       # dry run by default; --yes to transfer
        │
        ▼
comfy_validate.py workflow.json   # will this graph load?
```

### Decisões de projeto

**Digest ausente é erro grave.** Um download que não pode ser verificado é
indistinguível de um corrompido, então uma entrada de manifesto sem um `sha256`
válido de 64 caracteres é rejeitada, em vez de baixada na confiança.

**Os downloads caem em um arquivo `.part` ao lado e só são renomeados depois que
o digest confere.** Assim, uma transferência interrompida nunca deixa um arquivo
truncado atrás de um nome válido. Divergência de digest apaga o sidecar;
divergência de tamanho o mantém, para a próxima execução continuar de onde parou.

**Ações destrutivas exigem uma flag explícita.** Sem `--yes`, `install` é uma
simulação que imprime o plano e não transfere nada. Substituir um arquivo cujo
digest difere exige também `--allow-overwrite`. O espaço livre é conferido contra
os bytes ainda necessários antes de a transferência começar.

**O manifesto é entrada não confiável.** `folder` e `filename` são provados
dentro do diretório de modelos antes de qualquer coisa tocar o sistema de
arquivos, então um manifesto contendo `..` ou um caminho absoluto é rejeitado.

**A validação é estática e diz isso.** Uma execução limpa significa que o grafo é
carregável e que os arquivos que ele nomeia existem. Não significa que o
resultado da inferência está correto, e nada aqui executa um prompt.

## Exemplos adicionais

### Conferir uma pasta antes de um download grande

```
Confira meu manifest.json contra a pasta de modelos e diga quanto ainda
precisa ser baixado.
```

`verify` imprime um status por entrada e os bytes necessários; o código de saída
`5` significa que pelo menos uma entrada precisa de atenção.

### Baixar apenas o que falta

```
Instale os modelos que faltam a partir do manifest.json.
```

O agente roda a simulação primeiro e mostra o plano. A transferência só acontece
depois que a forma com `--yes` é executada de propósito.

### Validar um workflow antes de enfileirá-lo

```
Valide o workflow.json contra o meu ComfyUI em execução antes de eu rodá-lo.
```

Reporta classes de nó desconhecidas, links pendurados, entradas obrigatórias
ausentes, incompatibilidades de tipo e nomes de arquivos de modelo citados pelo
grafo que não estão em nenhuma pasta configurada.

## Instalação e requisitos

Copie este diretório para a pasta de categoria `creative/` do seu diretório de
skills do Hermes, para que ele fique ao lado da skill oficial `comfyui`:

```bash
# Linux / macOS
cp -r skills/comfyui-model-manager ~/.hermes/skills/creative/
```

```powershell
# Windows (PowerShell)
Copy-Item -Recurse skills\comfyui-model-manager "$env:LOCALAPPDATA\hermes\skills\creative\"
```

Depois confirme com `hermes skills list`. Veja
[`../../docs/installation.md`](../../docs/installation.md).

Requisitos: Python 3.8+ (somente biblioteca padrão). Para validar um workflow é
necessário um servidor ComfyUI em execução, acessível em `--base-url` ou na
configuração local.

## Testes

```bash
cd skills/comfyui-model-manager
python -m unittest discover -s tests -t tests
```

`unittest` da biblioteca padrão. Sem rede, sem servidor ComfyUI ativo, e a
descoberta de caminhos é fixada em um diretório temporário, para que a suíte
nunca leia a configuração real da máquina hospedeira.

## Limitações

- **Testada apenas no Windows.** `platforms: [windows]`. Os scripts usam
  `os.path` e `pathlib` e devem funcionar em outros lugares, mas isso não foi
  executado, então não é afirmado.
- **`--hash` lê cada byte.** Em uma pasta de modelos grande, isso leva minutos.
  Serve para provar identidade, não para uma listagem rápida.
- **A validação precisa de um servidor em execução.** `comfy_env.py` e
  `comfy_models.py` funcionam offline; `comfy_validate.py` não, porque as
  definições de nó vêm de `/object_info`.
- **Disponibilidade de modelo é aviso, não erro.** Um workflow que cita um
  checkpoint não instalado ainda carrega; a skill reporta e deixa você decidir.
- **Sem retomada de download quando o digest muda.** Se o arquivo de origem
  mudar, o `.part` antigo é descartado na próxima verificação de tamanho.
- **Sem integração com o ComfyUI Manager.** Instalação de nós personalizados
  está fora do escopo; esta skill gerencia arquivos de modelo e valida grafos.

## Licença

MIT, como o resto deste repositório.
