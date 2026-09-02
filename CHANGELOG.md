# Changelog

## 0.8.0 — OCR e leitura visual de Anexos

### Acompanhamento Especial

- Adiciona a busca canônica e paginada `sei acompanhamento-search`.
- Aceita pesquisa por palavras, grupo e unidade, com confirmação local em tipo,
  descrição/observação, grupo e marcadores, sem sensibilidade a acentos.
- Mantém `sei acompanhamentos --json` como listagem completa compatível.

### Leitura de Anexos externos

- Adiciona extração centralizada para PDFs, JPEGs e PNGs externos.
- Executa OCR via Tesseract em PDFs rasterizados e imagens brutas quando
  disponível, com fallback de idioma para `eng`.
- Preserva páginas visuais como artefatos locais para análise multimodal.
- Expõe `document_extraction` e `visual_review` no contrato JSON.
- Impede que uma imagem sem camada textual seja tratada como Anexo vazio.

### Compatibilidade e limitações

- `read_document_content()` continua retornando texto; consumidores que precisam
  de proveniência devem usar `read_document_content_details()`.
- OCR é evidência textual auxiliar. Quando
  `visual_analysis_required=true`, a aplicação consumidora deve analisar os
  caminhos de `visual_artifacts`; o `sei-cli` não inventa descrição visual.
- Tesseract é opcional: sem ele, o pacote preserva os artefatos e informa a
  pendência em `warnings`.

### Validação do release

- Suíte offline com pytest.
- Verificação real no processo `08810116.003584/2025-48`:
  17/17 documentos lidos, 7 documentos com conteúdo visual identificado e
  artefatos visuais verificados no disco.

## 0.6.1 — Acompanhamento especial e arquivamento seguro

### Acompanhamento especial

- Adiciona canônicas `tracking-group-catalog`, `tracking-group-create-preview/confirm`, `process-watch-read` e `process-watch-preview/confirm`.
- Acompanhamento especial passa a seguir a mesma regra operacional dos marcadores: a fonte de verdade é a caixa da unidade atual.
- Processos visíveis em `recebidos` ou `gerados` podem ser colocados em acompanhamento especial naquela unidade, independentemente da unidade de origem.
- `process-watch-confirm` relê a lista de acompanhamentos especiais e só confirma sucesso quando o processo aparece como acompanhado.
- O fluxo fica preparado para uso antes de `process-conclude-*`, permitindo concluir processos sem perder o controle local na unidade.
- Adiciona `process-archive-preview/confirm` para verificar acompanhamento especial, aplicar quando faltar e concluir o processo na unidade atual em uma sequência canônica.

### Marcadores

- Ajusta a canônica `process-marker-*` para usar a caixa da unidade atual como fonte de verdade.
- Processos visíveis em `recebidos` ou `gerados` podem ser marcados naquela unidade, mesmo quando `process-read` falha ou aponta restrição de leitura em outra unidade.
- `process-read`/`process-summary` passam a ser enriquecimento best-effort para classificação e sugestão de texto, sem bloquear `process-marker-set-*`.
- A modalidade padrão de investigação para marcadores passa a ser `contextual`; `fast` e `deep`/`all` ficam como escolha explícita ou fallback operacional.
- O contrato passa a expor `marker_authority`, indicando que a decisão veio da lista de processos da unidade atual.

### Leitura contextual

- Corrige a decisão de troca automática de unidade em `process-read`, `process-summary` e `document-read`.
- Quando a árvore do processo visível na unidade atual traz URL contextual válida para o documento, a leitura usa essa URL e não bloqueia pela unidade autora/origem do documento.
- A troca para unidade autora fica reservada para casos em que a árvore atual não oferece URL visualizável para os documentos.
- A árvore passa a preservar `origin_unit` e `origin_description` dos documentos quando o SEI expõe `UNIDADE_GERADORA`.
- A seleção `contextual` passa a priorizar os primeiros documentos do processo e, no restante da amostra, documentos com origem/assinatura CBM; os últimos documentos continuam como fallback quando não houver sinal CBM.

### Documentos em processos encaminhados

- Ajusta a canônica para não tratar documentos `about:blank` de unidade autora como bloqueio quando a árvore atual expõe ação da unidade recebedora, como incluir documento.
- `document-create-*` pode seguir na unidade atual em processo recebido, deixando o próprio SEI rejeitar apenas se a ação realmente não existir.
- `_get_editor_url()` passa a expandir pastas lazy-loaded antes de falhar e tenta reconstruir `arvore_visualizar` com `infra_unidade_atual`/`infra_hash` atuais quando o documento aparece como `about:blank`.
- Documenta explicitamente que `--texto-inicial` aceita apenas `N/T/D`: `T` é Texto Padrão do SEI, enquanto cópia de documento existente deve usar `--documento-modelo <numero_sei>` e portanto `D`.
- Validação real no SEI: processo encaminhado `08810196.000046/2026-30`, documento criado `49382032` / SEI `40929963`, editado na seção `220` pela unidade `CMDO PABM APODI` sem troca para unidade autora.

### Blocos de assinatura

- Ajusta a pós-checagem de `signature-block-sign-confirm`/`sign_block`: documento de bloco assinado com sucesso é validado por deixar de estar `can_sign=true` para o usuário atual, mesmo que o bloco ainda mostre `assinado=false` por haver outras assinaturas pendentes.
- A operação deixa de abortar com o erro legado "Documentos permanecem pendentes após releitura do bloco" quando a releitura canônica confirma `remaining_signable_total=0` para os documentos selecionados.
- `signature-block-review` passa a separar `raw_can_sign` do SEI de `can_sign`/`can_sign_for_current_user`, evitando mostrar como assinável documento que já traz assinatura do usuário atual.
- Erros de pós-checagem ignorados com sucesso passam para `ignored_postcheck_errors` em vez de permanecerem em `sign_results[].errors`.

## 0.6.0 — Canônicas documentais e Documento Modelo

### Destaques

- Adiciona suporte a criação de documento interno a partir de Documento Modelo do SEI.
- Endurece a edição canônica para evitar HTML escapado ou duplamente escapado ao salvar documentos.
- Melhora a escolha automática da seção real de corpo em documentos com múltiplas seções editáveis.
- Consolida as canônicas de criação/edição documental e os atalhos de fluxo usados por agentes.

### Criação por Documento Modelo

- `document-create-preview` e `document-create-confirm` agora aceitam `--documento-modelo <numero_sei>`.
- Ao informar `--documento-modelo`, a canônica força `texto_inicial=D`.
- O POST de criação preenche `txtProtocoloDocumentoTextoBase` com o número SEI informado.
- O contrato JSON passa a expor `texto_inicial` e `documento_modelo` em `payload_preview` e `created_document`.

Exemplo:

```bash
sei document-create-preview 49286513 despacho --documento-modelo 40842131 --json
sei document-create-confirm 49286513 despacho --documento-modelo 40842131 --confirm --json
```

### Edição sem escape HTML

- `save_document()` normaliza todas as seções `txaEditor_*` para HTML cru antes do POST.
- Tags estruturais escapadas, inclusive multi-escapadas como `&amp;amp;lt;p`, são desescapadas antes do envio.
- O fluxo preserva as seções não editadas, mas evita repostar conteúdo estrutural escapado.
- Caracteres fora de ISO-8859-1 continuam sendo convertidos para entidades numéricas para compatibilidade com o editor legado do SEI.
- `document-edit-confirm` retorna sinais de validação em `save_validation`.

### Seção correta de corpo

- `document-edit-preview` evita selecionar cabeçalho, timbre, metadados e rodapé como seção de corpo.
- A heurística foi validada em documentos multi-seção, incluindo Encaminhamento e Justificativa.
- Matriz real validada no processo de teste `08810254.000138/2026-88` / `49286513`:

| Tipo | Seção de corpo validada |
| --- | --- |
| Encaminhamento | `1062` |
| Parecer | `601` |
| Ordem de Serviço | `341` |
| Parte Genérica | `341` |
| Despacho | `220` |
| Memorando | `341` |
| Autorização | `341` |
| Despacho Diligencial | `220` |
| Informação | `422` |
| Justificativa | `873` |
| Relatório de Viagem | `3690` |
| Minuta de Portaria | `616` |
| Solicitação de Providências | `341` |
| Solicitação | `4499` |

### Canônicas e CLI

- O catálogo de workflows passa a tratar `criar_processo`, `criar_documento`, `despachar` e `encaminhar` como fluxos canônicos suportados.
- O CLI aceita `--text` como alias de `--texto` nos pontos de edição aplicáveis.
- A skill OpenClaw foi atualizada com a regra de Documento Modelo e com a regra de salvar HTML cru no editor.

### Validação

- Suíte local: `pytest tests/ -q`.
- Checagem de diff: `git diff --check`.
- Validação real no SEI, unidade `CMDO PABM APODI`, usuário `LEO ZENON TASSI`.
- Validação independente do Lago no Slack:
  - fontes lidas com sucesso: `40851082`, `40842124`, `40842131`
  - cópias por Documento Modelo lidas com sucesso: `40851470`, `40851104`, `40851127`
  - nenhum documento mostrou HTML escapado literal como `&lt;p&gt;`, `&amp;lt;p`, `&lt;strong&gt;` ou `&lt;table`
  - os pares de conteúdo foram equivalentes por leitura textual:
    - `40851082` -> `40851470`
    - `40842124` -> `40851104`
    - `40842131` -> `40851127`

### Observações

- A prova independente confirmou a camada textual extraída pelo SEI; não houve prova visual por browser/CDP nessa sessão.
- Justificativa expõe uma tabela de referência em `875`; a seção de corpo real validada é `873`.
- `1062` é o corpo do Encaminhamento testado, não um padrão global para todos os tipos.
