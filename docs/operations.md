# Operacoes Canonicas e Roadmap de Evolucao

## Objetivo

Transformar o `sei-cli` em uma camada operacional previsivel para uso local
com agentes como Claude Code e OpenClaw, reduzindo improviso da LLM e
centralizando os fluxos corretos no codigo.

A estrategia adotada e:

1. Consolidar **comandos de alto nivel** para as intencoes mais comuns.
2. Padronizar **contratos JSON** estaveis para consumo por skills/agentes.
3. Mapear **workflows declarativos** para orientar o proximo passo valido.
4. Evoluir para MCP apenas depois que a interface operacional estiver madura.

## Principios

- O conhecimento fragil do SEI deve morar no codigo, nao no prompt.
- A skill deve usar sempre a operacao canonica mais especifica disponivel.
- Quando houver comando canonico, o agente nao deve montar fluxo alternativo.
- Toda operacao deve validar pre-condicoes e falhar de forma fechada.
- O output para automacao deve ser JSON versionado e previsivel.
- Workflows YAML devem representar regra de negocio, nao detalhes HTTP.
- Escritas sensiveis devem vir depois, com `preview` + `confirm`.

## Arquitetura Alvo

```text
Skill (OpenClaw / Claude Code)
  -> comandos canonicos do CLI (--json)
    -> camada sei_cli.operations
      -> SEIClient
        -> SEI RN (HTTP puro)
```

### Responsabilidades por camada

#### `sei_cli/client.py`
- Login, cookies, `infra_hash`, redirects, encoding, navegacao e parsing HTTP.
- Metodos tecnicos e reaproveitaveis.

#### `sei_cli/operations/`
- Operacoes orientadas a intencao.
- Resolucao de contexto e IDs.
- Validacao de entradas.
- Sequencia de passos suportada.
- Pos-condicoes e proximas acoes permitidas.
- Contratos JSON estaveis.

#### CLI (`sei_cli/cli.py`)
- Interface humana e automacao local.
- `rich` para leitura humana.
- `--json` para consumo por agentes.

#### `workflows/`
- Mapeamento declarativo do processo de negocio por orgao.
- Referencias sobre atores, etapas, decisoes e transicoes validas.

## Escopo da Primeira Onda

Comecar apenas com operacoes de leitura e navegacao, que oferecem alto ganho
e baixo risco.

### Operacoes iniciais

#### `inbox-snapshot`
Mostra contexto atual da sessao:
- status da sessao
- unidade atual
- total de processos recebidos
- total de processos gerados
- processos novos
- blocos relevantes

#### `process-open`
Abre um processo por numero SEI ou `id_procedimento` e devolve:
- identificadores resolvidos
- metadados basicos
- arvore/lista de documentos
- sugestao de proximas acoes de leitura

#### `process-read`
Le o processo com mais contexto do que `process-open`:
- documentos
- indicadores uteis para triagem
- opcionalmente marcadores, bloco, unidade e resumo simples

`process-read` tenta os documentos individualmente. Uma falha de acesso ou de
extracao nao interrompe a leitura dos demais. Alem de `documents_read`, o
retorno pode conter:

- `read_summary.documents_read_total`
- `read_summary.documents_failed_total`
- `read_summary.documents_restricted_total`
- `read_summary.read_status`: `complete`, `partial` ou `tree_only`
- `read_summary.partial_read` e `read_summary.partial_visibility`
- `documents_restricted` e `warning_details`

`origin_unit`/`origin_description` sao metadados. A unidade autora nao deve ser
tratada como bloqueio quando a arvore contextual da unidade atual possui URL ou
acao utilizavel.

#### `process-history`
Le o historico normalizado do processo sem exigir parsing manual da tela do SEI.

Invocacoes recomendadas:

```bash
sei process-history <processo> --json
sei process-history <processo> --full --json
sei process-history <processo> --limit 20 --date-from DD/MM/YYYY --json
```

O retorno inclui `total`, `returned`, `has_more`, `entries`, `latest`,
`open_units`, `latest_process_transition` e `latest_document_activity`.
`--full` retorna todos os registros; sem ele, o limite padrao e 20.

`process-summary` pode incorporar esse contexto com:

```bash
sei process-summary <processo> --include-history --history-limit 20 --json
```

Nesse caso, `summary_source` termina em `+history` quando o historico foi
incorporado. Historico nao substitui a leitura do documento-chave.

#### `document-read`
Le um documento por numero SEI ou IDs internos e devolve:
- IDs resolvidos
- metadados do documento
- texto limpo
- tipo detectado
- proximas acoes sugeridas

#### `relatorio-read`
Le um relatorio operacional e devolve estrutura parseada:
- equipe
- viaturas
- ocorrencias
- observacoes
- resumo legivel

#### `block-review`
Le um bloco e devolve:
- documentos contidos
- status de assinatura
- unidade origem/destino
- proximas acoes de leitura

## Catalogo Canonico Atual

Use esta lista como referencia curta da superficie promovida. Quando houver
comando nesta lista, skills e workflows devem preferi-lo aos comandos legados.

### Leitura e navegacao

- `inbox-snapshot`
- `process-open`
- `process-read`
- `process-summary`
- `process-history`
- `process-report`
- `document-read`
- `relatorio-read`

### Criacao e edicao documental

- `process-create-preview`
- `process-create-confirm`
- `document-create-preview`
- `document-create-confirm`
- `document-edit-preview`
- `document-edit-confirm`
- `document-quality-check`

### Marcadores e triagem

- `marker-catalog`
- `process-marker-preview`
- `process-marker-read`
- `process-marker-history`
- `process-marker-set-preview`
- `process-marker-set-confirm`
- `process-marker-update-preview`
- `process-marker-update-confirm`
- `process-marker-remove-preview`
- `process-marker-remove-confirm`
- `environment-triage-preview`
- `environment-triage-parallel`
- `environment-triage-apply`
- `tracking-group-catalog`
- `tracking-group-create-preview`
- `tracking-group-create-confirm`
- `process-watch-read`
- `process-watch-preview`
- `process-watch-confirm`
- `process-archive-preview`
- `process-archive-confirm`

### Processo, PDF e finalizacao

- `process-forward-preview`
- `process-forward-confirm`
- `process-conclude-preview`
- `process-conclude-confirm`
- `process-reopen-preview`
- `process-reopen-confirm`
- `process-finalize-preview`
- `process-finalize-confirm`
- `process-pdf-preview`
- `process-pdf-confirm`
- `document-pdf-preview`
- `document-pdf-confirm`

### Blocos de assinatura

- `block` (alias de compatibilidade para `signature-block-read`)
- `block-review`
- `signature-block-list`
- `signature-block-read`
- `signature-block-review`
- `signature-block-add-document-preview`
- `signature-block-add-document-confirm`
- `signature-block-recall-preview`
- `signature-block-recall-confirm`
- `signature-block-refresh-preview`
- `signature-block-refresh-confirm`
- `signature-block-sign-preview`
- `signature-block-sign-confirm`

## Estado Atual da Camada Documental

Esta frente foi considerada estavel para uso real:

- `document-create-preview`
- `document-create-confirm`
- `document-edit-preview`
- `document-edit-confirm`
- `document-quality-check`

Capacidades estabilizadas:

- heranca real de acesso do processo na criacao documental
- resolucao de tipo documental pelo formulario real
- criacao a partir de Documento Modelo com `--documento-modelo <numero_sei>`
- `--texto-inicial` representa apenas o modo inicial (`N`, `T`, `D`), nao o
  corpo do documento
- `T` significa Texto Padrao cadastrado no SEI; para copiar documento SEI
  existente, usar `--documento-modelo <numero_sei>`, que força `D`
- para escrever corpo novo, criar o documento e depois usar `document-edit-*`
- identificacao de secoes `editable` vs `readOnly`
- gravacao na secao editavel correta
- reler documento apos gravacao
- quality-check com sinais de padronizacao e placeholders

### Referencias internas no corpo do documento

Quando o corpo HTML citar documento ou processo SEI ja existente, a referencia
deve usar o link nativo do editor, nao `href` externo.

Padrao:

```html
<span contenteditable="false" style="text-indent:0;">
  <a class="ancora_sei" id="lnkSei{id_interno}" style="text-indent:0;">{numero_visivel}</a>
</span>
```

Regras:

- para documento, `{id_interno}` e o `id_documento` da arvore/process-read
- para processo, `{id_interno}` e o `id_procedimento` resolvido
- `{numero_visivel}` e o numero que aparece para o usuario, por exemplo
  `40715880` ou `08810105.000066/2026-82`
- nunca use `href="https://sei...controlador.php?..."` em documento interno:
  isso pode abrir fora da sessao/unidade atual e provocar tela de login
- deixe o texto narrativo fora da ancora: `Portaria-SEI ... (` + link +
  `)` ou `Processo SEI nº ` + link
- se o id interno nao estiver disponivel com seguranca, mantenha o numero em
  texto simples e sinalize para resolver antes de salvar
- nunca montar link com URL fixa do SEI; `infra_hash` muda e tende a levar ao
  login ou a contexto invalido

Observacao importante:

- `empty_body_check` continua sendo heuristico
- o proximo refinamento desejado e expor `document_version`
- com isso, o contrato deve ganhar `is_pristine_document`
- para documento "zero bala", o sinal principal deve ser a versao, nao o conteudo do template

## Proximo Escopo: Blocos de Assinatura

Depois da camada documental, a proxima frente prioritaria e blocos de assinatura.

### Objetivo

Permitir fluxo canonico de:

1. listar blocos relevantes
2. revisar documentos pendentes no bloco
3. disponibilizar/cancelar disponibilizacao
4. incluir/remover documento do bloco
5. assinar documentos do bloco com preflight de unidade
6. reler status apos assinatura

### Ordem recomendada

#### Fase 1. Leitura e triagem

- `signature-block-list`
- `signature-block-read`
- `signature-block-review`

Essas canônicas devem devolver:

- numero do bloco
- estado
- unidade origem
- unidade destino
- documentos contidos
- quantos estao assinados
- quantos estao pendentes
- proximas acoes validas

#### Fase 2. Mutacoes seguras de bloco

- `signature-block-add-document-preview`
- `signature-block-add-document-confirm`
- `signature-block-recall-preview`
- `signature-block-recall-confirm`
- `signature-block-refresh-preview`
- `signature-block-refresh-confirm`

Todas com `preview` + `confirm` quando houver risco operacional.

Backlog adicional de bloco:

- criar bloco novo por superficie canonica
- remover documento avulso por superficie canonica fora do `refresh`
- disponibilizar/cancelar por comandos canonicos avulsos quando isso for mais claro que `refresh`/`recall`
- permitir adicionar/remover unidades destino de um bloco de assinatura de forma canônica
- usar isso para cenários de assinatura por unidade destinatária sem recriar bloco
- manter leitura/revisão de destinos antes de mutar o bloco
- suportar ciclo de vida reutilizável do bloco:
  - ir para a unidade dona do bloco
  - cancelar disponibilizacao quando o bloco estiver disponibilizado
  - ajustar o conjunto de documentos no bloco
  - disponibilizar novamente para a unidade destinataria
  - usar isso para preparar um bloco reutilizavel com novos documentos pendentes
- `signature-block-update-destinations-preview`
- `signature-block-update-destinations-confirm`

#### Fase 3. Assinatura canonica

- `signature-block-sign-preview`
- `signature-block-sign-confirm`

Pre-condicoes obrigatorias:

- unidade correta acessivel
- documento realmente pendente
- cargo/filiacao validos para assinatura
- credenciais validas

Pos-condicoes obrigatorias:

- confirmar novo status de assinatura
- devolver documentos assinados
- reler bloco apos assinatura

### Contrato minimo sugerido para blocos

```json
{
  "schema_version": "1",
  "ok": true,
  "operation": "signature-block-read",
  "context": {},
  "resolved_ids": {
    "block_numero": "774681"
  },
  "data": {
    "block": {},
    "documents_total": 0,
    "signed_total": 0,
    "pending_total": 0
  },
  "next_actions": [],
  "warnings": [],
  "error": null
}
```

## Marcadores — Diretriz de Produto

Decisão operacional atual:

- o fluxo principal de marcadores é por processo específico
- o objetivo do marcador é ser significativo e contextual, não só categórico
- bulk pelo Controle de Processos fica fora da prioridade imediata

Implicações:

- priorizar `process-marker-preview`, `process-marker-read`, `process-marker-history`,
  `process-marker-set-*`, `process-marker-update-*` e `process-marker-remove-*`
- para marcador, a fonte de verdade é a caixa da unidade atual (`inbox-snapshot` /
  `client.list_processes()`): se o processo aparece em `recebidos` ou `gerados`,
  ele pode ser marcado naquela unidade
- falha de `process-read` não bloqueia marcador; leitura serve para enriquecer
  classificação e sugestão de texto, não para decidir se a mutação é permitida
- a investigação padrão para gerar resumo/texto meaningful é `contextual`
- usar `fast` apenas quando o usuário pedir velocidade/triagem ampla ou quando a leitura contextual falhar/demorar
- usar `deep`/`all` apenas quando o usuário pedir aprofundamento ou quando o caso exigir leitura integral
- marcadores são por ambiente/unidade; o mesmo processo pode ter marcadores
  diferentes em unidades diferentes
- o texto sugerido do marcador deve continuar curto, operacional e útil para triagem
- histórico e alteração de texto no próprio processo têm mais valor do que mutação em lote

Backlog rebaixado:

- bulk de marcador pelo Controle de Processos
- mutações em lote de múltiplos processos

## Auditoria de Sessao e Login

Objetivo:

- eliminar relogins desnecessarios
- reduzir perda de contexto de unidade/processo
- medir o custo de navegacao por canônica
- identificar fluxos que desviam da rota correta da UI do SEI

Hipotese operacional:

- quando a canônica segue a sequencia certa de rotas do SEI, a sessao se mantém
- relogin recorrente normalmente indica rota errada, refresh inadequado, wrapper mal resolvido
  ou restore de unidade inconsistente

### Prioridade de auditoria

1. leitura base
2. troca/restauração de unidade
3. bloco de assinatura
4. criação/edição documental
5. PDF nativo
6. triagem de ambiente

### Pontos de codigo sensiveis

- `_try_inicializar`
- `_navigate_to_arvore`
- `_auto_unit_switch`
- `switch_unit`
- qualquer fluxo que reabra `principal`
- qualquer fluxo que troque unidade e nao confirme restore
- qualquer fluxo que reabra arvore/processo mais vezes do que a UI faria

### Canônicas a verificar

- `process-open`
- `document-read`
- `process-read`
- `relatorio-read`
- `document-create-*`
- `document-edit-*`
- `process-pdf-*`
- `document-pdf-*`
- `signature-block-*`
- `process-finalize-*`
- `environment-triage-*`

### Sinais de erro critico

- terminar em `login.php` sem expiracao real
- relogin no meio de uma canônica
- restore de unidade incorreto
- segunda execucao funcionar melhor que a primeira sem motivo funcional
- diferenca grande entre fluxo local e fluxo da UI

### Regra de seguranca para testes reais

- toda escrita deve usar apenas processos e documentos de teste
- toda assinatura deve usar apenas processos, documentos e blocos de teste
- nenhuma mutação real deve ocorrer em processo operacional fora do conjunto de teste

## Encaminhamento, Conclusao e Reabertura

### Encaminhamento de processo

A canônica de encaminhamento precisa separar explicitamente dois conceitos
que hoje nao podem ser tratados como o mesmo campo:

- `retorno programado`
- `reabertura programada`

Diretriz:

- o parser do formulario de envio deve expor campos separados
- a canônica nao deve mais usar um `date_fields` generico
- o contrato deve informar qual dos dois grupos o formulario real expôs
- a camada de operacao deve validar que, em geral, o usuario informa um ou outro

Superficie desejada:

- `process-forward-preview`
- `process-forward-confirm`

Com politica:

- `--retorno-em`
- `--reabrir-em`
- falhar fechado se ambos forem usados e o formulario nao suportar combinacao
- preview deve refletir a semantica correta antes do `confirm`

### Conclusao de processo

O fluxo de conclusao tambem deve virar canônica completa.

Capacidades desejadas:

- concluir definitivo
- concluir com reabertura programada por data
- concluir com reabertura programada por prazo em dias

Superficie desejada:

- `process-conclude-preview`
- `process-conclude-confirm`

Contrato esperado:

- `conclude_policy.mode = definitive | reopen_on_date | reopen_in_days`
- `available_form_fields`
- `scheduled_reopen_supported`

### Reabertura de processo

Reabrir processo concluido depende da unidade:

- navegar para uma unidade que o usuario acesse
- essa unidade precisa ser uma unidade onde o processo esteve e foi concluido
- so nessa unidade a acao `reabrir` fica disponivel

Superficie desejada:

- `process-reopen-preview`
- `process-reopen-confirm`

Pre-condicoes:

- resolver o processo
- identificar unidades acessiveis onde a reabertura e possivel
- explicitar em qual unidade a reabertura ocorrera

Pos-condicoes:

- confirmar que o processo voltou a ficar aberto nessa unidade

### Bateria recomendada para validacao real

- leitura base: `process-open`, `document-read`, `process-read`, `relatorio-read`
- escrita documental: `document-create-*`, `document-edit-*`
- PDF nativo: `process-pdf-*`, `document-pdf-*`
- bloco de assinatura: `signature-block-read/review/refresh/sign`
- finalizacao: `process-finalize-*`
- triagem: `environment-triage-*`
- acompanhamento especial: `tracking-group-*`, `process-watch-*`

Para cada teste real, registrar:

- comando
- tempo total aproximado
- unidade inicial
- unidade final
- se houve relogin
- se houve troca de unidade
- se precisou repetir a execucao
- erro exato, se houver

Próximos refinamentos desejáveis dentro desta frente:

- histórico de marcador mais rico, quando a UI expuser mais metadados
- melhoria incremental da sugestão de texto
- manutenção segura de marcador existente sem remover/recriar desnecessariamente

## Acompanhamento Especial — Diretriz de Produto

Decisão operacional atual:

- acompanhamento especial é específico da unidade atual
- ele pode ser usado como etapa anterior à conclusão para "arquivar" processos sem perder rastreabilidade operacional
- a permissão de acompanhar é derivada da visibilidade do processo na caixa da unidade atual, em `recebidos` ou `gerados`
- a origem do processo ou falha de leitura contextual não bloqueia acompanhamento quando o processo está visível na unidade

Primeira leva implementada:

- `tracking-group-catalog`
- `tracking-group-create-preview`
- `tracking-group-create-confirm`
- `process-watch-read`
- `process-watch-preview`
- `process-watch-confirm`
- `process-archive-preview`
- `process-archive-confirm`

Regra de uso:

- usar `tracking-group-catalog` para resolver grupo por ID ou nome
- criar grupo ausente com `tracking-group-create-preview/confirm`
- usar `process-watch-preview` para mostrar processo, grupo, observação, estado atual e se a mutação será `add` ou `update`
- usar `process-watch-confirm --confirm` para aplicar e reler a lista de acompanhamento especial
- só considerar pronto para conclusão quando a verificação indicar `tracked=true`
- para concluir depois de acompanhar, seguir com `process-conclude-preview/confirm`
- quando a intenção do usuário for "arquivar", preferir `process-archive-preview/confirm`

### Arquivamento seguro

`process-archive-preview/confirm` encadeia acompanhamento especial e conclusão do processo na unidade atual.

Comportamento:

- resolve o processo pela caixa da unidade atual
- verifica se ele já está em acompanhamento especial nessa unidade
- se já estiver acompanhado, pula a etapa de acompanhamento e prepara a conclusão
- se não estiver acompanhado, exige `--group/--grupo` para resolver o grupo de acompanhamento
- aplica o acompanhamento especial antes de concluir
- só conclui depois de `process-watch-confirm` retornar verificação positiva

Exemplo:

```bash
sei process-archive-preview 08810254.000138/2026-88 --group "Concluídos" --json
sei process-archive-confirm 08810254.000138/2026-88 --group "Concluídos" --confirm --json
```

### Riscos a tratar desde o inicio

- bloco em unidade sem acesso
- documento ja assinado
- documento sem link valido de assinatura
- cargo divergente no formulario
- assinatura parcial com falha no meio do lote
- retorno do bloco apos assinatura
- bloco disponibilizado em outra unidade exigindo recolhimento antes de manutencao
- parser da arvore do processo marcando `assinado=false` para documentos ja assinados

## Proximo Escopo: Marcadores de Processo

Esta frente agora passa a ter uma camada canônica inicial focada no fluxo por processo específico.

### Objetivo

- contextualizar rapidamente do que trata o processo
- sugerir texto curto de marcador a partir da leitura canônica do processo
- aplicar marcador ao processo com `preview` + `confirm`
- remover marcador pelo fluxo atual do processo

### Primeira leva implementada

- `marker-catalog`
- `process-marker-preview`
- `process-marker-read`
- `process-marker-history`
- `process-marker-set-preview`
- `process-marker-set-confirm`
- `process-marker-update-preview`
- `process-marker-update-confirm`
- `process-marker-remove-preview`
- `process-marker-remove-confirm`

### Regra de uso nesta fase

- usar apenas processos de teste para validação real
- a permissão de marcar é derivada da visibilidade do processo na caixa da unidade atual
- a sugestão de texto do marcador tenta usar `process-summary --mode contextual`, mas falha de leitura
  não bloqueia `process-marker-set-*` quando o processo está visível em `recebidos` ou `gerados`
- a canônica atual cobre o fluxo dentro do processo específico

### Segunda fase planejada

- gestão granular de múltiplos marcadores no mesmo processo
- leitura do histórico de marcadores
- alteração de texto de marcador existente
- mutação em lote pelo Controle de Processos:
  - selecionar um ou mais processos
  - adicionar marcador
  - remover marcador
- leitura mais precisa de prazo/manifestações para enriquecer a sugestão do texto do marcador

### Fluxo canonico adicional: bloco reutilizavel

Este fluxo ficou validado como necessidade operacional real.

Objetivo:

- reaproveitar o mesmo bloco de assinatura ao longo do processo
- recolher o bloco quando necessario
- trocar os documentos contidos
- redisponibilizar para a unidade que vai assinar

Sequencia desejada:

1. `signature-block-read`
2. `signature-block-recall-preview`
3. `signature-block-recall-confirm`
4. `signature-block-add-document-preview`
5. `signature-block-add-document-confirm`
6. `signature-block-remove-document-confirm`
7. `signature-block-disponibilizar-confirm`
8. `signature-block-sign-preview`
9. `signature-block-sign-confirm`

Pre-condicoes importantes:

- a manutencao do bloco deve ocorrer na unidade dona do bloco
- se o bloco estiver disponibilizado, a canônica deve orientar recolhimento/cancelamento antes da mutacao
- a canônica deve deixar claro quando a unidade atual só pode assinar e quando pode administrar o bloco

### Regra operacional: assinatura local vs assinatura por bloco

Existem dois fluxos distintos e eles nao devem ser misturados:

1. Assinatura por bloco disponibilizado

- usar quando o signatario nao tem acesso a unidade geradora do documento
- o documento e colocado em bloco de assinatura
- o bloco e disponibilizado para a outra unidade
- a assinatura deve ocorrer na unidade destinataria, pelo fluxo `signature-block-sign-*`
- a pos-checagem deve considerar sucesso quando o documento selecionado deixa
  de estar `can_sign=true` para o usuario atual
- `assinado=false` no bloco nao significa, sozinho, falha da assinatura atual:
  pode indicar que ainda ha outro assinante pendente no mesmo documento
- em `signature-block-review`, `raw_can_sign` preserva o sinal bruto da tela do
  SEI; `can_sign` e `can_sign_for_current_user` representam a possibilidade
  efetiva de assinatura pelo usuario atual
- se o submit retorna erro legado de releitura como "documentos permanecem
  pendentes", confrontar com `signature-block-review`; se o documento selecionado
  nao aparece mais em `signable_document_ids`, a assinatura deve ser considerada
  aplicada

2. Assinatura local da unidade geradora

- usar quando o proprio usuario da unidade geradora vai assinar
- se o documento estiver em bloco disponibilizado, primeiro e necessario cancelar a disponibilizacao do bloco
- depois disso a assinatura deve ocorrer no ambiente da unidade geradora, pelo fluxo normal de assinatura de documento

## PDF Nativo — Camada Canônica

Objetivo:

- gerar PDF nativo do processo inteiro
- gerar PDF nativo de um documento específico
- explicitar preflight de unidade antes da geração
- manter `preview` + `confirm` para a escrita local do arquivo

Canônicas:

- `process-pdf-preview`
- `process-pdf-confirm`
- `document-pdf-preview`
- `document-pdf-confirm`

Regra operacional:

- se o processo ou documento estiver aberto apenas em outra unidade, a canônica deve refletir isso no `preflight`
- a execução deve seguir a lógica real do SEI:
  - navegar para o processo/documento
  - acionar `Gerar PDF`
  - confirmar `Gerar`
  - baixar o arquivo final

Observação:

- os comandos legados `download-pdf` e `download-doc-pdf` continuam existindo
- a skill e os fluxos guiados devem preferir a superfície canônica nova
- nesse caso, nao usar `signature-block-sign-*`

Consequencias para testes e canônicas:

- todo teste de `signature-block-sign-*` precisa considerar a unidade destinataria do bloco disponibilizado
- todo teste de assinatura local precisa garantir que o bloco nao esteja disponibilizado
- quando houver erro de assinatura, o diagnostico deve primeiro verificar se o fluxo escolhido bate com a logistica do bloco

### Backlog imediato acoplado a esta fase

1. Expor `document_version` nas canônicas documentais.
2. Adicionar `is_pristine_document` ao `document-quality-check`.
3. Rebaixar `empty_body_check` para heuristica auxiliar.
4. Usar `is_pristine_document` como sinal principal antes de assinatura/PDF.

## Contrato JSON Padrao

Toda operacao canonica deve expor um contrato estavel com esta estrutura:

```json
{
  "schema_version": "1",
  "ok": true,
  "operation": "document-read",
  "context": {
    "unidade_sigla": "OP 3",
    "usuario": "Fulano"
  },
  "resolved_ids": {
    "numero_processo": "08810058.000128/2026-69",
    "id_procedimento": "47607237",
    "numero_documento": "39860248",
    "id_documento": "48568466"
  },
  "data": {},
  "next_actions": [
    {
      "action": "process-open",
      "label": "Abrir processo relacionado"
    }
  ],
  "warnings": [],
  "error": null
}
```

### Regras do contrato

- `schema_version` deve existir desde a primeira versao.
- `ok` indica sucesso ou falha.
- `operation` identifica a operacao executada.
- `context` traz contexto minimo da sessao atual.
- `resolved_ids` concentra toda resolucao de identificadores.
- `data` contem a carga util da operacao.
- `next_actions` limita o proximo passo recomendado.
- `warnings` registra desvios nao fatais.
- `error` descreve o problema quando `ok=false`.

### Estrutura recomendada de erro

```json
{
  "code": "document_not_found",
  "message": "Documento 39860248 nao encontrado na unidade atual.",
  "retryable": false,
  "details": {}
}
```

Codigos iniciais sugeridos:
- `auth_required`
- `session_invalid`
- `unit_not_found`
- `process_not_found`
- `document_not_found`
- `block_not_found`
- `unsupported_state`
- `workflow_violation`
- `network_error`
- `parse_error`

## Regras Operacionais para Skills

As skills devem seguir estas regras:

1. Usar sempre o comando canonico mais especifico disponivel.
2. Preferir `--json` em todas as chamadas automatizadas.
3. Nunca montar URLs do SEI manualmente.
4. Nunca montar fluxos alternativos quando houver comando canonico.
5. Respeitar `next_actions` como guia do passo seguinte.
6. Em caso de falha, relatar `error.message` e nao improvisar.
7. Em operacoes sensiveis futuras, exigir confirmacao explicita do usuario.

## Evolucao dos Workflows YAML

Os YAMLs em `workflows/` devem deixar de descrever apenas a historia do
processo e passar a apontar para operacoes canonicas.

### Forma atual
- Focada em etapas de negocio.
- Boa para documentacao.
- Ainda nao esta diretamente conectada ao CLI.

### Forma desejada

Exemplo conceitual:

```yaml
nome: Reaprazamento de Ferias
orgao: cbmrn

etapas:
  - ordem: 1
    id: abrir_processo
    operation: process-open
    requires: []
    allowed_next:
      - ler_requerimento

  - ordem: 2
    id: ler_requerimento
    operation: document-read
    requires:
      - abrir_processo
    allowed_next:
      - ler_despacho
```

### Campos novos recomendados

- `id`
- `operation`
- `requires`
- `allowed_next`
- `approval_required`
- `notes`
- `examples`

### Regra

Workflow YAML deve dizer:
- qual operacao usar
- em que ordem
- quais dependencias existem
- quais proximas etapas sao permitidas

Nao deve dizer:
- qual URL chamar
- qual `infra_hash` usar
- qual POST tecnico reproduzir

## Estrutura de Codigo Proposta

```text
sei_cli/
  operations/
    __init__.py
    contracts.py
    errors.py
    reading.py
    workflows.py
```

### `contracts.py`
- dataclasses ou TypedDicts dos contratos de operacao
- serializacao consistente para JSON

### `errors.py`
- erros semanticos da camada de operacoes
- mapeamento para `error.code`

### `reading.py`
- operacoes iniciais de leitura
- orquestracao sobre o `SEIClient`

### `workflows.py`
- carga e validacao dos YAMLs
- resolucao de proxima etapa valida

## Backlog por Arquivo

### 1. `docs/operations.md`
Responsabilidade:
- ser a referencia viva da superficie canonica

Entregas:
- catalogo de operacoes
- contrato JSON
- criterios de aceite

### 2. `sei_cli/operations/contracts.py`
Responsabilidade:
- definir estruturas padrao de resposta

Entregas:
- `OperationResult`
- `OperationError`
- `NextAction`
- helpers para serializacao

### 3. `sei_cli/operations/errors.py`
Responsabilidade:
- padronizar erros da camada de operacoes

Entregas:
- excecoes como `ProcessNotFoundError`, `DocumentNotFoundError`
- conversao para `error.code`

### 4. `sei_cli/operations/reading.py`
Responsabilidade:
- implementar operacoes de leitura

Entregas iniciais:
- `inbox_snapshot(client, ...)`
- `process_open(client, numero_ou_id, ...)`
- `process_read(client, numero_ou_id, ...)`
- `document_read(client, numero_ou_id, ...)`
- `relatorio_read(client, numero_ou_id, ...)`
- `block_review(client, bloco, ...)`

### 5. `sei_cli/operations/workflows.py`
Responsabilidade:
- carregar workflows e resolver proximos passos

Entregas:
- listar workflows por orgao
- abrir workflow
- obter proxima etapa permitida
- validar integridade do YAML

### 6. `sei_cli/cli.py`
Responsabilidade:
- expor comandos canonicos

Entregas iniciais:
- `sei inbox-snapshot`
- `sei process-open`
- `sei process-read`
- `sei document-read`
- `sei relatorio-read`
- `sei block-review`
- output humano enxuto + `--json`

### 7. `tests/`
Responsabilidade:
- garantir estabilidade da interface

Entregas:
- testes unitarios das operacoes
- testes de contrato JSON
- fixtures/minimos para erros e edge cases

## Ordem Recomendada de Implementacao

### Fase 1. Fundacao
- criar `sei_cli/operations/`
- definir contratos em `contracts.py`
- definir erros em `errors.py`

### Fase 2. Primeiras operacoes
- implementar `inbox-snapshot`
- implementar `process-open`
- implementar `document-read`

### Fase 3. Exposicao no CLI
- adicionar comandos novos no `cli.py`
- garantir `--json`
- manter comandos antigos intactos

### Fase 4. Testes de contrato
- golden tests do JSON
- testes de falha controlada
- testes com fixtures offline

### Fase 5. Expansao de leitura
- `process-read`
- `relatorio-read`
- `block-review`

### Fase 6. Integracao com workflows
- loader YAML
- `workflow-show`
- `workflow-next`

### Fase 7. Endurecimento da skill
- atualizar a skill para usar apenas a superficie canonica
- documentar proibicoes de improviso operacional

## Criterios de Aceite por Operacao

Cada operacao nova so entra como canonica quando cumprir:

1. Resolve IDs de forma consistente.
2. Nao exige que a skill conheca detalhes HTTP.
3. Retorna JSON estavel e versionado.
4. Fornece `next_actions`.
5. Falha com `error.code` previsivel.
6. Tem teste offline cobrindo o contrato.
7. Tem pelo menos um exemplo de uso documentado.

## Melhorias de UX para Agentes

### Preferencias de naming
- usar nomes de comandos descritivos e curtos
- evitar verbos tecnicos demais
- preferir `process-open` a `get-process-tree`

### Preferencias de output
- incluir identificadores resolvidos sempre
- nao devolver HTML bruto por padrao
- resumir o contexto da unidade atual
- limitar texto muito longo, com opcao de detalhe quando necessario

### Preferencias de navegacao
- permitir numero SEI ou ID interno quando possivel
- resolver internamente e devolver ambos no resultado
- indicar claramente quando a unidade atual nao tem acesso

## Backlog de Casos Operacionais

### Issue 25. Processo fora da unidade acessivel ou com restricao de acesso

Contexto:
- em alguns casos o usuario conhece o numero do processo, mas nao tem acesso a
  unidade em que ele esta aberto
- nesses casos, documentos nao assinados podem ficar indisponiveis para leitura
- processos/documentos privados, restritos ou sigilosos tambem podem bloquear
  leitura total ou parcial

Objetivo:
- a operacao canonica deve continuar sendo util mesmo quando a leitura completa
  nao for possivel
- o agente precisa saber exatamente o que foi lido, o que nao foi lido e por que

Comportamento esperado:
- `process-open`, `process-read`, `document-read` e `process-report` devem
  distinguir entre:
  - falta de acesso a unidade geradora
  - documento nao assinado fora da unidade acessivel
  - processo/documento privado ou restrito
  - processo/documento sigiloso
- Se o processo esta visivel na caixa atual e a arvore atual oferece URL
  contextual (`arvore_url`/`src_url`, inclusive com `infra_unidade_atual`) para
  o documento, `document-read` e `process-read` devem usar essa URL antes de
  tentar trocar para a unidade autora/origem.
- A unidade autora do documento nao deve ser tratada como autoridade primaria
  de acesso quando o documento ja esta visualizavel no contexto atual.
- Troca automatica de unidade so deve ocorrer como fallback quando a arvore
  atual nao trouxer URL visualizavel para os documentos.
- Quando a arvore expuser `UNIDADE_GERADORA`, a unidade autora deve ser
  preservada como metadado (`origin_unit`/`origin_description`) para melhorar
  selecao contextual, sem virar bloqueio de acesso.
- Em modo `contextual`, a selecao padrao deve combinar:
  - documentos iniciais, que normalmente explicam o pedido/contexto original
  - documentos CBM, que normalmente indicam providencia institucional atual
  - documentos finais apenas como fallback quando nao houver sinal institucional
- Em processos recebidos por encaminhamento, documentos de unidade autora podem
  aparecer como `about:blank`. Isso nao deve bloquear `document-create-*` nem
  `document-edit-*` quando a arvore atual tambem expuser acao da unidade atual,
  como incluir documento.
- Para editar documento em processo encaminhado, a busca do editor deve expandir
  pastas lazy-loaded e, se necessario, reconstruir `arvore_visualizar` com
  `infra_unidade_atual` e `infra_hash` da sessao atual antes de declarar falha.
- a resposta JSON deve marcar leitura parcial de forma explicita
- a operacao nao deve mascarar o problema como erro generico de parser

Sinais que devem aparecer no contrato:
- `preflight.access_status`
- `preflight.required_unit`
- `preflight.access_limited`
- `read_summary.partial_read`
- `warnings` com motivo operacional legivel
- `error.code` especifico quando a leitura falhar totalmente

Codigos de erro sugeridos:
- `unit_access_required`
- `document_unavailable_in_current_unit`
- `restricted_access`
- `private_access`
- `classified_access`
- `partial_visibility`

Proximas acoes esperadas:
- informar ao usuario que a unidade atual nao permite leitura completa
- informar se existe unidade acessivel alternativa
- informar quais documentos ficaram ocultos ou nao assinados
- permitir que o restante do processo ainda seja resumido com sinalizacao de
  cobertura parcial

Impacto nas canonicas:
- `process-read` deve conseguir devolver analise parcial do processo
- `process-report` deve propagar lacunas de visibilidade no relatorio final
- `relatorio-read` deve dizer claramente quando o documento existe, mas nao pode
  ser lido naquele contexto operacional

## Riscos Conhecidos

### Risco: duplicar logica entre CLI e operacoes
Mitigacao:
- toda regra nova entra primeiro na camada `operations`
- o CLI so adapta entrada/saida

### Risco: skill continuar improvisando
Mitigacao:
- reduzir a skill
- explicitar comandos canonicos
- padronizar `next_actions`

### Risco: congelar interface cedo demais
Mitigacao:
- comecar com leitura
- observar uso real
- promover a MCP apenas o que ficar estavel

### Risco: workflows virarem burocracia
Mitigacao:
- manter YAML enxuto
- mapear apenas fluxos recorrentes
- nao modelar tudo cedo demais

## Plano de Validacao

### Validacao tecnica
- rodar `pytest tests/ -v`
- adicionar testes de contrato para cada operacao
- validar serializacao JSON

### Validacao operacional
- usar localmente com skill em rotinas reais de leitura
- observar onde a LLM ainda tenta desviar
- ajustar nomes de comandos, payloads e `next_actions`

### Sinais de maturidade para evoluir a MCP
- comandos canonicos estaveis por pelo menos algumas semanas de uso
- poucos ajustes de naming/contrato
- skill usando majoritariamente a superficie nova
- erros bem classificados e previsiveis

## Milestone 1

Objetivo:
- entregar a primeira superficie canonica de leitura

Escopo:
- `inbox-snapshot`
- `process-open`
- `document-read`
- contrato JSON v1
- testes de contrato

Definicao de pronto:
- skill consegue consultar caixa, abrir processo e ler documento sem montar
  fluxo manual
- CLI retorna JSON confiavel
- falhas comuns retornam `error.code` previsivel

## Milestone 2

Objetivo:
- adicionar leitura contextual e workflows

Escopo:
- `process-read`
- `relatorio-read`
- `block-review`
- loader de workflows
- `workflow-show`
- `workflow-next`

## Milestone 3

Objetivo:
- preparar a superficie para futuras mutacoes e MCP

Escopo:
- operacoes de escrita em modo controlado
- `preview` + `confirm`
- consolidacao da skill
- avaliacao formal da migracao para MCP

## Decisao de Arquitetura

Neste momento, a decisao oficial do projeto e:

- **Nao** migrar direto para MCP.
- **Sim** construir primeiro uma camada de operacoes canonicas no CLI.
- **Sim** usar workflows/YAML para guiar o fluxo de negocio.
- **Sim** usar essa camada como futura base de um MCP local, se o uso real
  provar que a interface estabilizou.

## Backlog Imediato

### Pos-verificacao de assinatura/autenticacao por `NosAcoes[]`

Objetivo:
- usar a nova deteccao estrutural por `NosAcoes[]` como pos-check principal em assinatura direta de documento
- reduzir falso positivo e falso negativo apos `sign_document()` e `authenticate_document()`

Escopo recomendado:
1. `sign_document()`
- manter o preflight atual de formulario
- apos o submit, reler a arvore completa com `get_full_document_tree(..., expand_all=True)`
- localizar o `id_documento`
- confirmar `SignatureInfo(kind="assinatura")`

2. `authenticate_document()`
- apos o submit, reler a arvore completa
- localizar o `id_documento`
- confirmar `SignatureInfo(kind="autenticacao")`

3. `process-finalize-confirm`
- substituir a pos-verificacao atual por `NosAcoes[]` como sinal principal
- manter a leitura textual/renderizada atual como fallback diagnostico

4. `signature-block-sign-confirm`
- nao e prioridade alterar agora
- o fluxo atual ja ficou confiavel pela releitura do bloco

Decisao tecnica:
- preferir `NosAcoes[]` como source of truth para pos-verificacao em assinatura/autenticacao direta
- manter o metodo atual como fallback complementar durante a transicao
- se houver divergencia entre arvore e leitura do documento, emitir warning diagnostico

Checklist real para o Lago depois da implementacao:
1. validar assinatura direta local de documento interno de teste
2. validar autenticacao direta de PDF externo de teste
3. validar `process-finalize-confirm` em processo misto:
   - externos -> `autenticacao`
   - internos -> `assinatura`
4. confirmar que o documento certo aparece com:
   - `assinado=true` para interno
   - `autenticado=true` para PDF
5. decidir, com base no real:
   - se o metodo antigo pode virar apenas fallback
   - ou se vale manter os dois checks em paralelo
