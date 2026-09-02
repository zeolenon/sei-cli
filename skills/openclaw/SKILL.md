---
name: sei
description: "Operar o SEI com leitura contextual e ações canônicas."
version: 0.7.1
author: Leo Zenon, Herminho
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [SEI-RN, sei-cli, leitura, triagem, blocos]
    related_skills: []
---

Esta skill acompanha a superfície canônica do `sei-cli` 0.7.1. Os comandos
abaixo devem ser executados pelo agente via `terminal`; a skill não expõe
detalhes internos de HTTP, HTML, sessão ou autenticação.

## Regra principal

Use **sempre as canônicas do CLI** quando houver uma para a tarefa. Não prefira métodos brutos do `SEIClient` nem comandos legados se a superfície canônica já existir.

## Regra de instalação e versão

Não tentar instalar, atualizar ou trocar a instalação do `sei-cli` automaticamente.

Antes de qualquer ação de setup ou upgrade, identificar a origem do binário em uso:

- `which sei`
- `sei --version`

Só propor upgrade, reinstalação com `pipx`/`uv` ou troca de PATH quando:

- o usuário pedir explicitamente
- a tarefa for explicitamente de setup/manutenção
- ou houver conflito real entre instalações e isso estiver bloqueando o uso

Operar o SEI não exige auto-upgrade da ferramenta.

## Inicialização recomendada após conectar

Na primeira utilização em uma máquina/sessão funcional, é recomendado fazer uma leitura leve dos ambientes acessíveis para melhorar navegação e preflight entre unidades.

Preferir algo não destrutivo, por exemplo:

1. `sei inbox-snapshot --json`
2. se necessário, uma triagem leve por ambiente com `environment-triage-preview --mode fast --limit ... --json`

Objetivo:

- descobrir unidades/ambientes mais usados
- facilitar trocas de unidade posteriores
- reduzir navegação cega entre ambientes

Isso é aquecimento de contexto, não pré-requisito obrigatório para toda tarefa.

## Superfície canônica atual

### Leitura

- `sei inbox-snapshot --json`
- `sei process-open <processo> --json`
- `sei process-read <processo> --json`
- `sei process-summary <processo> --json`
- `sei process-history <processo> --json`
- `sei process-history <processo> --full --json`
- `sei process-report <processo> --json`
- `sei document-read <documento> --process-id <processo> --json`
- `sei relatorio-read <documento> --process-id <processo> --json`

Regra operacional de leitura:

- Se o processo esta visivel na caixa/unidade atual e a arvore traz URL contextual valida para o documento, use a canônica de leitura nesse contexto.
- Nao trate a unidade autora/origem do documento como bloqueio quando a URL contextual da arvore atual esta disponivel.
- Troca para unidade autora e fallback para caso sem URL visualizavel no contexto atual, nao criterio primario.
- Em `process-read`/`process-summary` contextual, espere que a canônica leia primeiro documentos iniciais e depois priorize documentos CBM quando a arvore trouxer `UNIDADE_GERADORA`; isso ajuda em processos grandes com varias instituicoes respondendo.
- Se o resumo contextual mencionar uma norma, portaria conjunta ou documento-chave nao lido, aprofunde com `process-read --mode deep` ou `document-read` no documento citado.
- Em processo encaminhado/recebido, `about:blank` em documentos da unidade autora nao bloqueia criacao/edicao se a unidade atual tem acao no processo. Use `document-create-*` normalmente na unidade recebedora.
- Se `document-edit-preview` nao achar editor de primeira, a canônica deve expandir pastas lazy e tentar URL contextual atual antes de concluir que o editor nao existe.

### Histórico e resumo contextual

- Use `process-summary --include-history --history-limit 20 --json` para
  contextualizar um processo sem fazer manualmente POSTs de histórico.
- Use `process-history --full --json` quando for necessário o conjunto completo
  de movimentações; `--limit`, `--date-from` e `--date-to` reduzem a consulta.
- Leia `data.history_context`, `open_units`, `latest_process_transition` e
  `latest_document_activity` junto com os documentos. Histórico contextualiza o
  andamento, mas não substitui a leitura do documento-chave.
- Se `summary_source` terminar em `+history`, o resumo combinou documentos e
  histórico. Se for `tree_partial`, há contexto de árvore, mas não texto
  documental suficiente para classificação segura.

### Leitura parcial e visibilidade

- `process-read --mode all --json` tenta cada documento individualmente; uma
  falha não interrompe os demais.
- Considere `read_summary.read_status`:
  - `complete`: todos os documentos selecionados foram lidos;
  - `partial`: parte foi lida e parte falhou;
  - `tree_only`: a árvore é visível, mas nenhum documento selecionado foi lido.
- Use `documents_restricted`, `documents_restricted_total` e
  `warning_details` para reportar a cobertura. Códigos relevantes incluem
  `document_unavailable_in_current_unit`, `restricted_access`,
  `private_access`, `classified_access` e `partial_visibility`.
- `partial_read` significa sucesso documental parcial; `partial_visibility`
  também cobre árvore lazy não expandida ou contexto limitado.
- Não converta `origin_unit` em bloqueio automático. Documentos acessíveis na
  árvore contextual da unidade atual devem ser lidos normalmente.
- Se não houver árvore/documentos acessíveis, reporte o código estruturado
  retornado pela operação (`unit_access_required` ou erro de leitura), sem
  inventar conteúdo ausente.

### Criação e edição de rascunho

- `sei process-create-preview ... --json`
- `sei process-create-confirm ... --confirm --json`
- `sei document-create-preview ... --json`
- `sei document-create-confirm ... --confirm --json`
- Para reutilizar conteudo existente, use `--documento-modelo <numero_sei>` em `document-create-*`; isso seleciona `Documento Modelo` (`rdoTextoInicial=D`) e preenche `txtProtocoloDocumentoTextoBase`.
- `--texto-inicial` nao e o corpo do documento. Ele aceita apenas `N`, `T` ou `D`: `N` = nenhum, `T` = Texto Padrão cadastrado no SEI, `D` = Documento Modelo.
- Para copiar conteudo de um documento SEI existente, nunca use `T`; use `--documento-modelo <numero_sei>`, que força `D` automaticamente.
- Para escrever corpo novo, crie o documento com `N` e depois use `document-edit-confirm --text/--content`.
- `sei document-edit-preview ... --json`
- `sei document-edit-confirm ... --confirm --json`
- `sei document-quality-check ... --json`

Regra para referencias SEI no corpo HTML:

- Quando citar documento ou processo SEI ja existente, use o link nativo
  `ancora_sei`, nunca `href` externo.
- Formato: `<span contenteditable="false" style="text-indent:0;"><a class="ancora_sei" id="lnkSei{id_interno}" style="text-indent:0;">{numero_visivel}</a></span>`.
- Para documento, `{id_interno}` e o `id_documento`; para processo, e o
  `id_procedimento`.
- Nunca use `href="https://sei...controlador.php?..."` em documento interno.
  Mesmo quando parece correto visualmente, esse link abre fora do contexto do
  editor e pode forcar novo login. Se `document-quality-check` acusar
  `external_sei_hrefs`, corrija antes de assinar.
- O texto narrativo fica fora da ancora: `Portaria-SEI ... (` + link + `)` ou
  `Processo SEI nº ` + link.
- Se o id interno nao estiver resolvido, deixe como texto simples e avise que a
  referencia precisa ser vinculada antes de salvar/assinar.

### PDF nativo

- `sei process-pdf-preview <processo> --json`
- `sei process-pdf-confirm <processo> --confirm --json`
- `sei document-pdf-preview <documento> --process-id <processo> --json`
- `sei document-pdf-confirm <documento> --process-id <processo> --confirm --json`

### Marcadores e triagem

- `sei marker-catalog --json`
- `sei process-marker-preview <processo> --json`
- `sei process-marker-read <processo> --json`
- `sei process-marker-history <processo> --json`
- `sei process-marker-set-preview <processo> --marker <nome-ou-id> --json`
- `sei process-marker-set-confirm <processo> --marker <nome-ou-id> --confirm --json`
- `sei process-marker-update-preview <processo> --texto/--text "<texto>" --json`
- `sei process-marker-update-confirm <processo> --texto/--text "<texto>" --confirm --json`
- `sei process-marker-remove-preview <processo> --json`
- `sei process-marker-remove-confirm <processo> --confirm --json`
- `sei environment-triage-preview [--mode fast|contextual|deep] --json`
- `sei environment-triage-parallel [--mode fast|contextual|deep] --json`
- `sei tracking-group-catalog --json`
- `sei tracking-group-create-preview "<nome>" --json`
- `sei tracking-group-create-confirm "<nome>" --confirm --json`
- `sei process-watch-read <processo> --json`
- `sei process-watch-preview <processo> --group <nome-ou-id> --json`
- `sei process-watch-confirm <processo> --group <nome-ou-id> --confirm --json`
- `sei process-archive-preview <processo> [--group <nome-ou-id>] --json`
- `sei process-archive-confirm <processo> [--group <nome-ou-id>] --confirm --json`

Regra operacional para marcadores:

- marcador é por ambiente/unidade
- se o processo aparece no `inbox-snapshot` da unidade atual, em `recebidos` ou `gerados`, ele pode ser marcado naquela unidade
- não use falha de `process-read` como bloqueio para marcar processo visível na caixa atual
- `process-read` serve para classificar melhor o caso e sugerir texto, não para decidir a permissão de marcação
- a modalidade padrão de investigação para resumo/texto do marcador é `contextual`
- use `fast` quando o usuário pedir velocidade/triagem ampla ou quando o contextual falhar/demorar
- use `deep`/`all` quando o usuário pedir aprofundamento ou quando a decisão exigir leitura integral
- o mesmo processo pode ter marcadores diferentes em unidades diferentes

Regra operacional para acompanhamento especial:

- acompanhamento especial é por ambiente/unidade
- se o processo aparece no `inbox-snapshot` da unidade atual, em `recebidos` ou `gerados`, ele pode ser colocado em acompanhamento especial naquela unidade
- não use falha de `process-read` como bloqueio para acompanhar processo visível na caixa atual
- o mesmo processo pode estar em acompanhamentos diferentes em unidades diferentes
- para "arquivar" sem perder o processo, primeiro use `process-watch-preview/confirm`; só depois use `process-conclude-preview/confirm`
- preferir `process-archive-preview/confirm` quando o usuário pedir "arquivar", pois a canônica garante a ordem: verificar acompanhamento especial, aplicar se faltar e concluir o processo na unidade
- se o processo ainda não estiver acompanhado, `process-archive-*` precisa de `--group/--grupo`; se já estiver acompanhado, pode concluir sem grupo
- use `tracking-group-catalog` para resolver grupo por nome/ID; se o grupo não existir, crie com `tracking-group-create-preview/confirm`
- `sei environment-triage-apply ... --confirm --json`

### Encaminhamento, conclusão e reabertura

- `sei process-forward-preview <processo> <destinos...> --json`
- `sei process-forward-confirm <processo> <destinos...> --confirm --json`
- `sei process-conclude-preview <processo> --json`
- `sei process-conclude-confirm <processo> --confirm --json`
- `sei process-reopen-preview <processo> [--unit <unidade>] --json`
- `sei process-reopen-confirm <processo> [--unit <unidade>] --confirm --json`

### Finalização de documentos de um processo

- `sei process-finalize-preview <processo> [docs...] --json`
- `sei process-finalize-confirm <processo> [docs...] --confirm --json`

### Blocos de assinatura

- `sei block <bloco> --json` é alias compatível de
  `signature-block-read`; não use `client.get_block()` nem parsing manual.
- `sei signature-block-list --json`
- `sei signature-block-read <bloco> --json`
- `sei signature-block-review <bloco> --json`
- `sei signature-block-add-document-preview <bloco> <doc> --json`
- `sei signature-block-add-document-confirm <bloco> <doc> --confirm --json`
- `sei signature-block-recall-preview <bloco> --json`
- `sei signature-block-recall-confirm <bloco> --confirm --json`
- `sei signature-block-refresh-preview <bloco> ... --json`
- `sei signature-block-refresh-confirm <bloco> ... --confirm --json`
- `sei signature-block-sign-preview <bloco> --json`
- `sei signature-block-sign-confirm <bloco> --confirm --json`

Quando um bloco não aparece, `block_not_found` significa que ele não foi
localizado na lista da unidade atual. Verifique `error.details.visibility` e
`lookup_scope`: ausência nessa lista não prova inexistência global do bloco.

## Comandos legados: não preferir

Se existir canônica equivalente, **não** use por padrão:

- `encaminhar`
- `concluir`
- `reabrir`
- `sign`
- `authenticate`
- `read-doc`
- `read-relatorio`
- `block-add`
- `block-create`
- `block-disponibilizar`
- `block-cancelar`
- `block-delete`
- `block-devolver`
- `block-remove`

Esses comandos só entram se o usuário pedir explicitamente o legado ou se houver alguma lacuna real na canônica.

## Segurança e higiene

- Nenhum valor de credencial deve ser registrado; qualquer credencial eventualmente encontrada deve aparecer somente como `[REDACTED]`.
- Nunca copie cookies, tokens, hashes de sessão ou HTML bruto para relatórios,
  commits ou mensagens ao usuário.
- Antes de reportar a versão, valide `sei --version`; a versão esperada desta
  skill é `0.7.1`.

## Política de confirmação

### Exigem confirmação explícita do usuário

- assinar documento
- assinar bloco
- autenticar/certificar documento externo
- encaminhar processo para outra unidade
- concluir processo
- reabrir processo
- cancelar, excluir ou remover artefatos consolidados

### Não exigem segunda confirmação se o pedido do usuário já for explícito

- criar processo de teste
- criar documento rascunho
- editar conteúdo do rascunho
- rodar `document-quality-check`
- gerar PDF
- ler e resumir processos/documentos
- aplicar/atualizar marcador em triagem organizacional

## Regras operacionais por fluxo

### 1. Documento: criar e redigir

Fluxo padrão:

1. `document-create-preview`
2. `document-create-confirm`
3. `document-edit-preview`
4. `document-edit-confirm`
5. `document-quality-check`
6. `document-read`

Não parar para pedir uma segunda autorização entre criar e salvar rascunho se o usuário já pediu explicitamente o documento.

### 2. Assinatura local vs assinatura por bloco

São fluxos diferentes.

- **Assinatura local:** assinar na unidade geradora com `process-finalize-*` ou `sign_document`, depois de recolher o bloco se ele estiver disponibilizado.
- **Assinatura por bloco:** usar `signature-block-sign-*` apenas na unidade destinatária do bloco disponibilizado.
- Se o documento está no bloco e o bloco ainda expõe ação de assinar para a unidade atual, continue no fluxo `signature-block-sign-*` mesmo que já exista assinatura anterior de outro militar.
- Não desviar para assinatura local só porque o documento já tem alguma assinatura anterior. O sinal correto é o bloco ainda estar com `can_sign=true` / `signable_document_ids` preenchido.
- Pós-checagem correta: assinatura por bloco fechou para o usuário atual quando os documentos selecionados deixam de aparecer em `signable_document_ids`. Não exigir `assinado=true` se o documento ainda pode depender de outro assinante.
- Se aparecer erro de releitura do tipo "documentos permanecem pendentes", rode `signature-block-review`; se `remaining_signable_total`/`signable_document_ids` zerou para os documentos selecionados, trate como assinatura aplicada.
- Em `signature-block-review`, prefira `can_sign_for_current_user`/`signable_document_ids` para decisão operacional; `raw_can_sign` é apenas o sinal bruto da tela do SEI.

Cenário de referência validado: `CMDO PABM APODI -> PAD-PDF`.

### 3. `process-finalize`

Use para finalizar um processo misto com PDFs externos + documentos internos.

Regras:

- PDFs externos vão para autenticação.
- Documentos internos só vão para assinatura se:
  - o formulário de assinatura confirmar o usuário/cargo atual
  - e o final do texto for compatível com o signatário esperado
- ambiguidade no rodapé -> `skip` por padrão
- `--force-sign` só quando a checagem do formulário bater

### 4. Encaminhamento

`process-forward-*` tem duas famílias de campo distintas:

- `retorno programado`
  - `--retorno-em`
  - `--retorno-dias`
  - `--retorno-dias-uteis`
- `reabertura programada`
  - `--reabrir-em`
  - `--reabrir-dias`
  - `--reabrir-dias-uteis`

Regras:

- `reabertura programada` só faz sentido quando o processo fecha na origem (`--fechar`)
- não misturar data e dias na mesma família
- se o destino for ambíguo, falhar fechado e pedir que o usuário escolha a unidade certa

### 5. Conclusão

Use `process-conclude-*`.

Modos:

- definitivo
- com reabertura programada por data
- com reabertura programada por dias

No formulário real:

- `rdoConcluir=S` -> definitivo
- `rdoConcluir=V` -> concluir com reabertura programada

### 6. Reabertura

Use `process-reopen-*`.

Regras:

- a unidade que consegue reabrir é a que concluiu o processo
- `--unit` é preferência, não garantia
- o preview tenta:
  1. unidade pedida
  2. unidade atual
  3. unidades do histórico que o usuário acessa
- se encontrar outra unidade válida, deve informar `candidate_unit`
- não assumir sucesso só porque a árvore mostra `linkReabrirProcesso`; a fonte de verdade é `procedimento_visualizar`

### 7. Triagem de ambiente

`environment-triage-preview`:

- `fast`: só metadados
- `contextual`: default, lê no máximo 1 documento-chave por processo
- `deep`: casos difíceis, mais lento

Selecionar processos por:

- novo
- com mudança recente
- sem marcador
- opcionalmente revisão de marcador já existente

Aplicação definitiva deve preferir pelo menos `contextual`.

### 8. Marcadores

Texto do marcador deve refletir:

- assunto do processo
- estado atual
- se exige manifestação
- prazo, se houver
- militares envolvidos, quando fizer sentido

Formato preferido: `assunto - status atual`

Exemplos:

- `Ofício externo da SEAD - responder até 05/04`
- `Férias Sd Vinicius - reaprazamento solicitado`
- `Suprimento serviços Apodi - empenho emitido`

## Armadilhas importantes

### Unidade ambígua

Se mais de uma unidade combinar com o texto informado, não escolher sozinho. Pedir definição do usuário.

### Troca de unidade

Evitar `switch` redundante. Preferir as canônicas com preflight/restore interno.

### Rascunho vs documento assinado

Documento em rascunho pode exigir unidade dona do processo. Documento assinado é mais acessível entre unidades.

### Editor

- não aplicar `html.escape()` no corpo
- preservar HTML cru
- a canônica `document-edit-*` deve salvar tags reais (`<p>`, `<strong>`, `<table>`) e nunca `&lt;p&gt;` visível
- preservar seções não alvo, mas normalizar qualquer seção `txaEditor_*` escapada antes do POST
- se a seção template-lock não aceitar conteúdo, usar a seção editável seguinte

### Sessão/login

Quando o fluxo estiver correto, o SEI não deve forçar relogin. Se aparecer relogin, tratar como bug de navegação/rota, não como comportamento normal.

### Gotcha 12 — Lazy-loaded folders hide data

SEI pagina documentos do processo em pastas (`PASTA1`, `PASTA2`, ...). Pastas com `carregado=false` **não** vêm carregadas na resposta inicial da árvore. Isso afeta documentos, `NosAcoes` de assinatura e metadados.

Antes de concluir que um documento não existe, que um processo tem poucos documentos ou que uma assinatura está ausente:

1. verifique se há pastas lazy-loaded
2. expanda todas antes de tirar conclusão
3. prefira sempre `get_full_document_tree(id, expand_all=True)` em vez de parsing bruto da árvore
4. se por algum motivo usar HTML cru da árvore, expanda todas as pastas primeiro

Exemplo real validado: um processo mostrava só 2 documentos e 2 assinaturas no root. Depois da expansão da `PASTA1`, apareciam 12 documentos e 10 assinaturas. Concluir “não há assinatura” olhando só o root é erro crítico.

## Padrão de decisão do agente

Para qualquer tarefa SEI:

1. descobrir a canônica correspondente
2. usar `preview`
3. validar contexto/unidade/destino/signatário
4. pedir confirmação só se a política exigir
5. executar `confirm`
6. reler/verificar resultado

## Referências internas

- `docs/operations.md` — backlog e decisões operacionais
- `docs/plan.md` — visão mais ampla do projeto
- `skills/oda/SKILL.md` — formatação documental
