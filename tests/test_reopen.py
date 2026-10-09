from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sei_cli.client import SEIClient


ARVORE_HTML = """
<html><body><script>
Nos[0] = new infraArvoreNo(
  "PROCESSO","12345",null,
  "controlador.php?acao=arvore_visualizar&id_procedimento=12345&id_documento=999&infra_hash=aaa",
  "ifrVisualizacao","Processo","tipo","svg","svg","svg",true,true,null,null,null,"num"
);
</script></body></html>
"""

ARVORE_VIS_REOPEN = """
<html><body><script>
var linkReabrirProcesso = 'controlador.php?acao=procedimento_reabrir&amp;id_procedimento=12345&amp;infra_hash=bbb';
</script></body></html>
"""

ARVORE_VIS_NO_REOPEN = """
<html><body><script>
var linkConcluirProcesso = 'controlador.php?acao=procedimento_concluir&id_procedimento=12345';
</script></body></html>
"""

PROCESS_OPEN_HTML = """
<html><body>
  <a href="controlador.php?acao=procedimento_concluir&id_procedimento=12345">Concluir</a>
</body></html>
"""

VALIDATION_ERROR_HTML = """
<html><body>
  <textarea id="txaInfraValidacao">Processo já está aberto na unidade atual.</textarea>
</body></html>
"""


def _make_client() -> SEIClient:
    client = SEIClient.__new__(SEIClient)
    client.base_url = "https://sei.example.test"
    client._hash_pool = {}
    client._control_html = None
    client._menu_links = {}
    client._editor_hiddens = {}
    client._current_unit_id = None
    client._last_login = 0.0
    client._batch_active = False
    return client


def _mock_response(
    text: str,
    url: str = "https://sei.example.test/sei/controlador.php?acao=arvore_visualizar",
) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.url = url
    response.status_code = 200
    response.content = text.encode("utf-8")
    response.headers = {}
    return response


class TestReopenProcess:
    def setup_method(self) -> None:
        self.client = _make_client()

    @patch.object(SEIClient, "_navigate_to_arvore")
    @patch.object(SEIClient, "_get")
    def test_navigate_to_arvore_visualizar_follows_process_link(
        self,
        mock_get: MagicMock,
        mock_arvore: MagicMock,
    ) -> None:
        mock_arvore.return_value = ARVORE_HTML
        mock_get.return_value = _mock_response(ARVORE_VIS_REOPEN)

        html = self.client._navigate_to_arvore_visualizar("12345")

        assert html == ARVORE_VIS_REOPEN
        mock_get.assert_called_once_with(
            "https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_procedimento=12345&infra_hash=aaa"
        )

    @patch.object(SEIClient, "_navigate_to_arvore")
    @patch.object(SEIClient, "_get")
    def test_navigate_to_arvore_visualizar_returns_existing_visualizer_html(
        self,
        mock_get: MagicMock,
        mock_arvore: MagicMock,
    ) -> None:
        mock_arvore.return_value = ARVORE_VIS_REOPEN

        html = self.client._navigate_to_arvore_visualizar("12345")

        assert html == ARVORE_VIS_REOPEN
        mock_get.assert_not_called()

    @patch.object(SEIClient, "_navigate_to_arvore_visualizar")
    def test_check_reopen_available_reads_link_from_arvore_visualizar(
        self,
        mock_vis: MagicMock,
    ) -> None:
        mock_vis.return_value = ARVORE_VIS_REOPEN

        assert self.client.check_reopen_available("12345") is True

    @patch.object(SEIClient, "_navigate_to_arvore_visualizar")
    def test_check_reopen_available_returns_false_without_link(
        self,
        mock_vis: MagicMock,
    ) -> None:
        mock_vis.return_value = ARVORE_VIS_NO_REOPEN

        assert self.client.check_reopen_available("12345") is False

    @patch.object(SEIClient, "_navigate_to_arvore_visualizar")
    @patch.object(SEIClient, "_open_process_page")
    @patch.object(SEIClient, "_get")
    def test_reabrir_processo_uses_link_reabrir_processo_and_verifies_open_state(
        self,
        mock_get: MagicMock,
        mock_open_process_page: MagicMock,
        mock_vis: MagicMock,
    ) -> None:
        mock_vis.return_value = ARVORE_VIS_REOPEN
        mock_get.return_value = _mock_response("<html><body>OK</body></html>")
        mock_open_process_page.return_value = PROCESS_OPEN_HTML

        assert self.client.reabrir_processo("12345") is True
        mock_get.assert_called_once_with(
            "https://sei.example.test/sei/controlador.php?acao=procedimento_reabrir&id_procedimento=12345&infra_hash=bbb"
        )

    @patch.object(SEIClient, "_navigate_to_arvore_visualizar")
    @patch.object(SEIClient, "_get")
    def test_reabrir_processo_surfaces_sei_validation_error(
        self,
        mock_get: MagicMock,
        mock_vis: MagicMock,
    ) -> None:
        mock_vis.return_value = ARVORE_VIS_REOPEN
        mock_get.return_value = _mock_response(VALIDATION_ERROR_HTML)

        with pytest.raises(
            RuntimeError,
            match="Processo já está aberto na unidade atual.",
        ):
            self.client.reabrir_processo("12345")

    @patch.object(SEIClient, "_navigate_to_arvore_visualizar")
    def test_reabrir_processo_raises_when_link_is_absent(
        self,
        mock_vis: MagicMock,
    ) -> None:
        mock_vis.return_value = ARVORE_VIS_NO_REOPEN

        with pytest.raises(RuntimeError, match="Ação 'Reabrir Processo' não encontrada"):
            self.client.reabrir_processo("12345")
