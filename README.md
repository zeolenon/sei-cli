# sei-cli

CLI Python para operar o SEI-RN por HTTP puro, sem automação de navegador. A
superfície atual combina leitura contextual, resumo/histórico, criação e edição
de rascunhos e ações administrativas com `preview`/`confirm` quando há efeito
oficial.

> Para contribuir, leia [CONTRIBUTING.md](CONTRIBUTING.md). Use branch + PR
> conforme a política do repositório.

## Versão e instalação

A versão atual é **0.7.1**.

Com `uv`:

```bash
uv sync
uv run --project . sei --version
```

Instalação editável alternativa:

```bash
python -m pip install -e .
sei --version
```

A saída esperada da versão é equivalente a `sei, version 0.7.1`. O projeto
publica wheel e source distribution com a mesma versão declarada em
`pyproject.toml` e `sei_cli.__version__`.

## Uso canônico pela CLI

Prefira `--json` quando a saída for consumida por um agente ou script. Todos os
resultados canônicos usam um envelope versionado com `schema_version`, `ok`,
`operation`, `context`, `resolved_ids`, `data`, `next_actions`, `warnings` e
`error`.

### Sessão e unidade

```bash
sei login
sei status --json
sei units --json
sei switch "CMDO PABM APODI" --json
sei inbox-snapshot --json
```

A visibilidade de processos, marcadores, acompanhamentos e blocos é relativa à
unidade atual. Não conclua que um objeto inexiste globalmente apenas porque não
aparece no contexto atual.

### Processos, documentos e histórico

```bash
sei process-open <numero-ou-id> --json
sei process-read <numero-ou-id> --mode contextual --json
sei process-read <numero-ou-id> --mode all --json
sei process-summary <numero-ou-id> --json
sei process-summary <numero-ou-id> --include-history --history-limit 20 --json
sei process-history <numero-ou-id> --json
sei process-history <numero-ou-id> --full --json
sei document-read <numero-ou-id> --process-id <processo> --json
sei relatorio-read <numero-ou-id> --process-id <processo> --json
```

`process-history --full` acompanha a paginação do histórico do SEI e devolve
`total`, `returned`, `has_more`, `entries`, `latest`, `open_units`,
`latest_process_transition` e `latest_document_activity`.

`process-summary --include-history` busca o histórico completo e aplica o
limite localmente. Isso permite que `--history-limit 20` represente os 20
registros mais recentes, mesmo quando a tela resumida do SEI mostra menos
linhas.

### Leitura parcial e contexto de unidade

A árvore do SEI pode conter documentos de outras unidades sem URL acessível no
contexto atual. O `process-read` não interrompe a operação ao encontrar um
documento assim: continua lendo os demais e informa a cobertura.

No JSON, consulte:

- `data.read_summary.documents_succeeded_total`;
- `data.read_summary.documents_failed_total`;
- `data.read_summary.documents_read_total`;
- `data.read_summary.documents_restricted_total`;
- `data.read_summary.read_status`: `complete`, `partial` ou `tree_only`;
- `data.read_summary.partial_read`;
- `data.read_summary.partial_visibility`;
- `data.documents_restricted`;
- `data.warning_details`.

`origin_unit` e `origin_description` são metadados. A unidade autora não é um
bloqueio automático quando a árvore contextual oferece URL ou ação utilizável.
Quando a árvore estiver parcialmente visível, o resumo deve ser tratado como
contextualização, não como prova de que documentos ausentes não existem.

### Marcadores e acompanhamento especial

```bash
sei marker-catalog --json
sei process-marker-preview <processo> --json
sei process-marker-read <processo> --json
sei process-marker-history <processo> --json
sei process-marker-set-preview <processo> --marker <nome-ou-id> --json
sei process-marker-set-confirm <processo> --marker <nome-ou-id> --confirm --json
sei process-watch-read <processo> --json
sei process-watch-preview <processo> --group <nome-ou-id> --json
sei process-watch-confirm <processo> --group <nome-ou-id> --confirm --json
```

Marcadores e acompanhamento especial são por unidade. Se o processo está em
`recebidos` ou `gerados` da unidade atual, uma falha de leitura profunda não
impede a validação da permissão de marcação/acompanhamento.

### Blocos de assinatura

```bash
sei signature-block-list --json
sei signature-block-read <bloco> --json
sei signature-block-review <bloco> --json
sei block <bloco> --json
sei signature-block-add-document-preview <bloco> <documento> --json
sei signature-block-add-document-confirm <bloco> <documento> --confirm --json
sei signature-block-recall-preview <bloco> --json
sei signature-block-recall-confirm <bloco> --confirm --json
sei signature-block-refresh-preview <bloco> --json
sei signature-block-sign-preview <bloco> --json
sei signature-block-sign-confirm <bloco> --confirm --json
```

`block` é alias de compatibilidade para `signature-block-read`; ambos usam o
mesmo contrato operacional. Se o bloco não aparecer, o erro `block_not_found`
indica que ele não foi localizado na lista da unidade atual. Consulte
`error.details.lookup_scope` e `error.details.visibility`: ausência local não
prova inexistência global.

Assinar, disponibilizar, devolver, recolher ou encaminhar tem efeito oficial.
Use a sequência `preview` → revisão humana → `confirm` somente quando houver
autorização explícita para a ação.

### Criação, edição, PDF e tramitação

```bash
sei process-create-preview ... --json
sei process-create-confirm ... --confirm --json
sei document-create-preview ... --json
sei document-create-confirm ... --confirm --json
sei document-edit-preview ... --json
sei document-edit-confirm ... --confirm --json
sei document-quality-check ... --json
sei process-pdf-preview <processo> --json
sei process-pdf-confirm <processo> --confirm --json
sei document-pdf-preview <documento> --process-id <processo> --json
sei document-pdf-confirm <documento> --process-id <processo> --confirm --json
sei process-forward-preview <processo> <destinos...> --json
sei process-forward-confirm <processo> <destinos...> --confirm --json
sei process-conclude-preview <processo> --json
sei process-conclude-confirm <processo> --confirm --json
```

Para copiar conteúdo de um documento existente, use `--documento-modelo` em
vez de tratar `--texto-inicial T` como conteúdo. `N`, `T` e `D` são modos de
texto inicial do SEI; a edição do corpo ocorre depois com
`document-edit-preview/confirm`.

Para referências internas em conteúdo HTML, use âncoras nativas `ancora_sei`
com o `id_documento` ou `id_procedimento` resolvido. Não use `href` externo
para documentos internos.

## Credenciais e segurança

O cliente aceita configuração por variáveis de ambiente ou por arquivo local em
`~/.config/sei/credentials.json`, conforme a configuração do ambiente. Nunca
inclua valores reais de usuário, senha, token, cookie, `infra_hash` ou sessão em
commits, issues, fixtures, logs ou mensagens.

- Prefira um gerenciador de segredos que injete as variáveis somente no processo.
- Se usar arquivo local, mantenha permissões restritas e fora do repositório.
- Não imprima o ambiente nem o conteúdo do arquivo de credenciais.
- Em qualquer artefato ou relatório, credenciais encontradas devem aparecer
  somente como `[REDACTED]`.
- Requisições reais devem usar User-Agent compatível com uma sessão de navegador;
  a suíte automatizada não faz requisições reais.

## API Python

```python
from sei_cli.client import SEIClient

with SEIClient() as client:
    client.login()
    status = client.status()
    tree = client.get_full_document_tree("48348237")
    history = client.get_process_history("48348237", full=True)
    block = client.get_block("614662")
```

Para automações orientadas a intenção, prefira as operações em
`sei_cli.operations`, que devolvem o contrato JSON normalizado, por exemplo
`process_read`, `process_summary`, `process_history`, `document_read` e
`signature_block_read`.

## Desenvolvimento e verificação

A suíte usa fixtures offline e não deve acessar o SEI real:

```bash
uv run --project . pytest tests/ -q
uv build
uv run --project . sei --version
git diff --check
```

Smoke tests reais, quando autorizados, devem ser separados da suíte offline e
executados somente em processos adequados para consulta. Sempre confira a
resposta JSON, os identificadores resolvidos, a unidade/contexto e os avisos de
visibilidade antes de reportar sucesso.

## Arquitetura

- `sei_cli/client.py` — sessão HTTP, navegação, hashes, formulários e parsing técnico;
- `sei_cli/operations/` — operações canônicas, preflight, contratos e próximos passos;
- `sei_cli/cli.py` — comandos Click e serialização humana/JSON;
- `sei_cli/models.py` — dataclasses normalizadas;
- `sei_cli/parsers.py` — parsers da árvore, histórico, documentos e blocos;
- `skills/openclaw/SKILL.md` — orientação operacional para agentes;
- `docs/operations.md` — contratos e roadmap detalhados.

Comandos legados como `read-doc`, `read-relatorio`, `encaminhar`, `concluir`,
`reabrir` e os antigos `block-*` permanecem por compatibilidade, mas não são o
fluxo recomendado quando existir uma operação canônica equivalente.
