# Tramitar Processo

## API
- `SEIClient.get_tramitar_form(id_procedimento) -> TramitarForm`
- `SEIClient.list_unidades_destino_tramitacao(id_procedimento) -> list[Unit]`
- `SEIClient.enviar_processo(id_procedimento, unidade_destino, manter_aberto=True) -> bool`
- `SEIClient.tramitar_processo(...) -> bool` (alias)

## Fluxo HTTP implementado
1. Abrir processo via `procedimento_trabalhar` (link extraido da tela de controle).
2. No HTML do processo, localizar acao com `procedimento_enviar` (em `href` ou `onclick`).
3. Fazer GET na tela de envio.
4. Parsear formulario:
- `action`
- `hidden_fields`
- `select_fields`
- campo de destino
- checkbox de manter aberto
- unidades destino disponiveis
5. POST com unidade destino selecionada.

## Observacoes tecnicas
- URL com `infra_hash` nao e montada manualmente; sempre extraida do HTML da sessao atual.
- Ao enviar, `_control_html` e invalidado para forcar recarga de contexto na proxima operacao.
- `manter_aberto=True` marca o checkbox quando o campo existe no formulario.

## Teste no processo 81000109
Ambiente desta execucao nao conseguiu resolver DNS para `sei.rn.gov.br` (`httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known`), entao o teste online nao foi concluido aqui.

## Findings adicionais
### Create-Edit-Read (sem assinatura)
- Fluxo suportado no cliente: `create_document` -> `get_editor_sections` -> `save_document` -> `read_document`.
- O teste real no processo `81000109` nao foi executado neste ambiente pelo mesmo bloqueio de rede.

### Expansao de pagina no editor
- Em fixtures locais nao ha pagina `editor_montar` para inspecionar JS completo.
- Busca no codigo/fixtures nao encontrou referencia a `expandir_pagina`.
- Hipotese: recurso e frontend (CKEditor/JS) e nao muda payload HTTP de `editor_salvar`.

### Script de validacao (rodar em ambiente com acesso externo)
```python
from sei_cli.client import SEIClient

ID_PROC = "81000109"

with SEIClient() as c:
    c.login()
    c.switch_unit("BETA DESTINO")
    destinos = c.list_unidades_destino_tramitacao(ID_PROC)
    print([d.sigla for d in destinos[:10]])
    ok = c.enviar_processo(ID_PROC, "CMDO UNIDADE TESTE A", manter_aberto=True)
    print("enviado:", ok)
```
