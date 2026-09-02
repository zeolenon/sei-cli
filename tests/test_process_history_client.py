from __future__ import annotations

from unittest.mock import MagicMock

from sei_cli.client import SEIClient


TREE_HTML = """
<script>
var linkHistorico = 'controlador.php?acao=procedimento_consultar_historico&id_procedimento=47607237';
</script>
"""

HISTORY_FORM_HTML = """
<html><body>
<form id="frmProcedimentoHistorico"
      action="controlador.php?acao=procedimento_consultar_historico">
  <input type="hidden" name="id_procedimento" value="47607237" />
  <input type="hidden" name="infra_sistema" value="100000100" />
  <input type="hidden" name="hdnTipoHistorico" value="R" />
  <input type="submit" name="sbmConsultar" value="Ver histórico completo" />
</form>
<table id="tblHistorico">
  <tr><th>Data/Hora</th><th>Unidade</th><th>Usuário</th><th>Descrição</th></tr>
  <tr><td>01/09/2026 11:03</td><td>CBM - DF - CAF/CPO</td><td>Fulano</td>
      <td>Bloco 614662 retornado para CBM - DAL - DAL/1</td></tr>
</table>
</body></html>
"""

FULL_HISTORY_HTML = """
<html><body>
<table id="tblHistorico">
  <tr><th>Data/Hora</th><th>Unidade</th><th>Usuário</th><th>Descrição</th></tr>
  <tr><td>01/09/2026 11:03</td><td>CBM - DF - CAF/CPO</td><td>Fulano</td>
      <td>Bloco 614662 retornado para CBM - DAL - DAL/1</td></tr>
  <tr><td>30/08/2026 09:00</td><td>CBM - DAL - DAL/1</td><td>Ciclano</td>
      <td>Processo recebido na unidade</td></tr>
</table>
</body></html>
"""


def test_get_process_history_full_posts_preserved_form_fields() -> None:
    client = SEIClient.__new__(SEIClient)
    client._navigate_to_arvore = MagicMock(return_value=TREE_HTML)
    client._sei_url = lambda path: f"https://sei.rn.gov.br/sei/{path}"
    client._get = MagicMock(return_value=MagicMock(text=HISTORY_FORM_HTML))
    client._post = MagicMock(return_value=MagicMock(text=FULL_HISTORY_HTML))

    entries = client.get_process_history("47607237", full=True)

    assert len(entries) == 2
    assert entries[0].date_time == "01/09/2026 11:03"
    assert entries[0].description.startswith("Bloco 614662")
    client._post.assert_called_once()
    post_url, payload = client._post.call_args.args
    assert "procedimento_consultar_historico" in post_url
    assert payload["id_procedimento"] == "47607237"
    assert payload["infra_sistema"] == "100000100"
    assert payload["hdnTipoHistorico"] == "P"
