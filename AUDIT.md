# AUDIT — Métodos Públicos do SEIClient

**Data:** 2026-03-26  
**Versão:** main (d66bf61)  
**Total de métodos:** 114 públicos em 5193 linhas

---

## Resumo Executivo

### Estatísticas
- **Auth & Session:** 5 métodos
- **Process Navigation:** 6 métodos
- **Document Operations:** 6 métodos
- **Acompanhamento Especial:** 3 métodos
- **Marcadores:** 6 métodos
- **Blocos de Documentos:** 9 métodos
- **Tramitação:** 4 métodos
- **Grupos de Acompanhamento:** 4 métodos (⚠️ redundância)
- **Process Actions:** 4 métodos
- **Unidades:** 2 métodos

### Problemas Identificados
1. **Padrão misto inglês/português** — list_*/crear_* vs listar_*/criar_*
2. **Redundância de métodos equivalentes** — 3 casos principais
3. **Gaps em operações comuns** — documentos podem ser criados mas não editados online
4. **Métodos wrappers desnecessários** — alguns são apenas thin wrappers de outros

---

## Redundâncias Principais

### 1. Grupos de Acompanhamento (CRÍTICA)
4 métodos que deveriam ser 2:

| Método | Status | Causa |
|--------|--------|-------|
| `list_grupos_acompanhamento()` | Wrapper | Apenas chama `listar_grupos_acompanhamento()` (3 linhas) |
| `listar_grupos_acompanhamento()` | Ativo | Implementação real (~42 linhas) |
| `create_grupo_acompanhamento()` | Wrapper | Apenas chama `criar_grupo_acompanhamento()` (7 linhas) |
| `criar_grupo_acompanhamento()` | Ativo | Implementação real (~68 linhas) |

**Recomendação:** Remover os wrappers em inglês (list_*, create_*) ou tornar private. Padrão: português sempre.

### 2. Document Tree (RESOLVIDA)
**get_process_documents()** vs **get_full_document_tree()**
- ❌ `get_process_documents()`: não expande lazy folders, formato `Document`
- ✅ `get_full_document_tree()`: expande lazy folders, formato `TreeDocument`
- **Status:** Issue #14 deprecou `get_process_documents()`. Remover após período de transição.

### 3. Unidades (MENOR)
| Método | Retorna | Padrão |
|--------|---------|--------|
| `list_units()` | Todas as unidades do órgão | Inglês |
| `listar_unidades_usuario()` | Unidades do usuário logado | Português |

**Recomendação:** Padronizar em português. Renomear `list_units()` → `listar_unidades_orgao()` ou equivalente.

---

## Gaps — Operações Faltando

### Document Editing
- ✅ `create_process()` — criar processo
- ✅ `create_block()` — criar bloco
- ✅ `add_document_to_block()` — associar documento a bloco
- ❌ Editar documento (via editor_montar) — **não há método público**
- ❌ Assinar documento — **não há método público** (existe `_execute_sign_form()` privado)

**Impacto:** Agentes precisam cair em browser automation para editar/assinar docs.

### Process Management
- ✅ `reabrir_processo()` — reabrir
- ✅ `concluir_processo()` — concluir
- ❌ Devolver para unidade anterior — **não há método**
- ❌ Reclassificar (alterar assunto/classe) — **não há método**
- ✅ `alter_process()` — existe, mas é genérico (164 linhas)

### Acompanhamento Especial
- ✅ Criar, listar, alterar grupos
- ❌ Remover grupo — **não há método**
- ❌ Remover processo de acompanhamento — `remove_marcador()` é pra marcadores, não acompanhamento

---

## Classificação Detalhada

### AUTH & SESSION (5 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `login()` | 10 | Autenticação explícita | `_ensure_session()` reutiliza a sessão existente e interrompe falhas sem login automático |
| `close()` | 2 | ⚠️ Raro | Cleanup manual (context manager é preferível) |
| `status()` | 5 | ✅ Ativo | Verificar login status, autenticidade |
| `switch_unit()` | 50 | ✅ Ativo | Trocar unidade SEI (wrapper mais limpo que `trocar_unidade()`) |
| `trocar_unidade()` | 74 | ⚠️ Candidato deprecação | Mesma função de `switch_unit()`, redundante |

**Recomendação:** Deprecar `trocar_unidade()` — `switch_unit()` é mais limpo e em inglês (padrão CLI).

### PROCESS NAVIGATION (6 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `list_processes()` | 10 | ✅ Ativo | Listar processos da unidade atual |
| `search()` | 43 | ✅ Ativo | Busca rápida por número (retorna HTML bruto) |
| `search_document()` | 46 | ✅ Ativo | Buscar documento por protocolo |
| `search_units()` | 14 | ✅ Ativo | Buscar unidades por nome/sigla |
| `check_new_processes()` | 6 | ✅ Ativo | Processos novos (resumo rápido) |
| `get_actions()` | 64 | ✅ Ativo | Ações disponíveis em um processo |

**Status:** Sem redundâncias. Bem organizado.

### DOCUMENT OPERATIONS (6 métodos)

| Método | Linhas | Status | Quando Usar | Nota |
|--------|--------|--------|-------------|------|
| `get_process_documents()` | 33 | ❌ Deprecated | ~~Listar docs~~ | Use `get_full_document_tree()` |
| `get_full_document_tree()` | 55 | ✅ Ativo | Listar docs com lazy-load expand | Retorna `TreeDocument` |
| `create_process()` | 169 | ✅ Ativo | Criar novo processo | Complexo: título, documento, metadados |
| `download_document()` | 49 | ✅ Ativo | Baixar PDF ou conteúdo | Requer `TreeDocument` |
| `read_document_content()` | 37 | ✅ Ativo | Ler conteúdo HTML de documento | Para docs internos (visualização) |
| `delete_document()` | 87 | ✅ Ativo | Deletar documento de um processo | High-risk operation |

**Recomendação:** Remover `get_process_documents()` após deprecation period (90 dias).

### BLOCKS (9 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `create_block()` | 78 | ✅ Ativo | Criar bloco de documentos |
| `delete_block()` | 78 | ✅ Ativo | Deletar bloco |
| `list_blocks()` | 13 | ✅ Ativo | Listar blocos da unidade |
| `get_block_documents()` | 25 | ✅ Ativo | Documentos em um bloco |
| `add_document_to_block()` | 121 | ✅ Ativo | Adicionar doc ao bloco |
| `remove_document_from_block()` | 90 | ✅ Ativo | Remover doc do bloco |
| `disponibilizar_block()` | 43 | ✅ Ativo | Disponibilizar bloco para distribuição |
| `cancelar_disponibilizacao_block()` | 39 | ✅ Ativo | Cancelar disponibilização |
| `devolver_block()` | 64 | ✅ Ativo | Devolver bloco ao criador |

**Status:** Sem redundâncias. Bem estruturado.

### MARCADORES (6 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `list_marcadores()` | 12 | ✅ Ativo | Listar marcadores (forma simples) |
| `listar_marcadores()` | 24 | ✅ Ativo | Listar marcadores (forma detalhada) |
| `criar_marcador()` | 66 | ✅ Ativo | Criar marcador com nome, cor, ícone |
| `editar_marcador()` | 73 | ✅ Ativo | Alterar propriedades de marcador |
| `set_marcador()` | 67 | ✅ Ativo | Aplicar marcador a um processo |
| `remove_marcador()` | 54 | ✅ Ativo | Remover marcador de um processo |

**Status:** `list_marcadores()` vs `listar_marcadores()` — ambas retornam lists, mas com detalhes diferentes. Considerar unificar.

### TRAMITAÇÃO (4 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `get_tramitar_form()` | 14 | ⚠️ Interno | Obter form de tramitação (usado por `tramitar_processo()`) |
| `list_unidades_destino_tramitacao()` | 10 | ✅ Ativo | Unidades para as quais tramitar |
| `tramitar_processo()` | 49 | ✅ Ativo | Tramitar processo para outra unidade |
| `enviar_processo()` | 149 | ✅ Ativo | Enviar processo (mais genérico) |

**Status:** Sem redundâncias claras. `get_tramitar_form()` deve ser private (é helper).

### GRUPOS DE ACOMPANHAMENTO (4 métodos — CRÍTICA)
**[Ver Redundâncias #1 acima]**

### PROCESS ACTIONS (4 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `alter_process()` | 122 | ✅ Ativo | Alterar processo (genérico: prioridade, etc) |
| `reabrir_processo()` | 124 | ✅ Ativo | Reabrir processo fechado |
| `concluir_processo()` | 17 | ✅ Ativo | Concluir processo |
| `concluir_processos()` | 133 | ✅ Ativo | Concluir múltiplos processos |

**Status:** Bem estruturado. `concluir_processos()` é útil para batch operations.

### UNITS (2 métodos)

| Método | Linhas | Status | Quando Usar |
|--------|--------|--------|-------------|
| `list_units()` | 11 | ⚠️ Padrão misto | Listar unidades do órgão (inglês) |
| `listar_unidades_usuario()` | 43 | ✅ Ativo | Listar unidades do usuário (português) |

**Status:** Padrão misto. Considerar renomear `list_units()` para manter consistência.

---

## Proposta de Reorganização

### CURTO PRAZO (1-2 sprints)

1. **Deprecate redundante wrappers:**
   - `list_grupos_acompanhamento()` → deprecate (use `listar_grupos_acompanhamento()`)
   - `create_grupo_acompanhamento()` → deprecate (use `criar_grupo_acompanhamento()`)
   - `trocar_unidade()` → deprecate (use `switch_unit()`)
   - `get_process_documents()` → deprecate (use `get_full_document_tree()`)

2. **Adicionar docstrings de deprecação** apontando para alternativas.

3. **Padronizar nomenclatura:**
   - Decidir: inglês ou português?
   - **Recomendação:** CLI em inglês (Click), métodos em português (compatibilidade com SEI-RN)

### MÉDIO PRAZO (3-4 sprints)

1. **Remover wrappers deprecados** após 90 dias.

2. **Criar métodos públicos para gaps críticos:**
   - `sign_document(id_documento, id_procedimento)` — reexposição de `_execute_sign_form()`
   - `edit_document(id_documento, id_procedimento, novo_conteudo)` — integração com editor_montar
   - `remove_grupo_acompanhamento(grupo_id)` — gap identificado

3. **Adicionar testes** para todos os métodos públicos (hoje muitos dependem de fixtures HTML mock).

### LONGO PRAZO (roadmap)

1. **Desacoplamento de responsabilidades:**
   - Separar auth em `SessionManager`
   - Separar parsing em `SEIParser`
   - Manter `SEIClient` como orquestrador (facade)

2. **Versionamento de API:**
   - v1.0: como está hoje
   - v2.0: sem deprecated methods, com gaps preenchidos

---

## Métricas

| Métrica | Valor | Avaliação |
|---------|-------|-----------|
| Métodos públicos | 114 | ⚠️ Muitos; alguns deveriam ser private |
| Métodos com >100 linhas | 7 | ⚠️ Candidates para refactoring |
| Wrappers desnecessários | 4 | ⚠️ Redundância identificada |
| Gaps críticos | 3 | ✅ Tolerável; menos impactante que redundância |
| Cobertura de testes | ~60% | ⚠️ Muitos usam mocks; faltam testes de integração |

---

## Conclusão

O `SEIClient` funciona bem mas apresenta **problemas de design evolutivo** (métodos adicionados ao longo do tempo sem consolidação):

1. **Padrão misto inglês/português** — impede discoverability
2. **Wrappers desnecessários** — confundem colaboradores
3. **Gaps em operações comuns** — força agentes para browser automation

As recomendações acima visam **melhorar manutenibilidade e usabilidade** sem breaking changes (via deprecation gradual).
