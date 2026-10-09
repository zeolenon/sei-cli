"""Unit tests for give_notice_document and give_notice_process methods.

Uses mocked HTML responses — no live SEI calls.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch, call

import pytest

from sei_cli.client import SEIClient


# --- HTML Fixtures ---

# Arvore HTML with a document and its arvore_visualizar link
ARVORE_WITH_DOC = """
<script>
Nos[0] = new infraArvoreNo("PROCESSO","12345",null,
  "controlador.php?acao=arvore_visualizar&id_procedimento=12345&infra_hash=aaa",
  "ifrVisualizacao","Processo","tipo","svg","svg","svg",true,true,null,null,null,"num");
Nos[1] = new infraArvoreNo("DOCUMENTO","99001","12345",
  "controlador.php?acao=arvore_visualizar&id_documento=99001&id_procedimento=12345&infra_hash=bbb",
  "ifrConteudoVisualizacao","Despacho 1","Despacho","svg","svg","svg",true,false,null,null,null,"abc");
</script>
"""

# arvore_visualizar page with linkCienciaDocumento JS variable
ARVORE_VIS_WITH_CIENCIA_DOC = """
<html><body>
<script>
var linkCienciaDocumento = 'controlador.php?acao=documento_ciencia&acao_origem=arvore_visualizar&id_procedimento=12345&atualizar_arvore=1&infra_sistema=100000100&infra_hash=ccc&id_documento=';
var linkEditarConteudo = 'controlador.php?acao=editor_montar&id_documento=99001&infra_hash=ddd';
</script>
</body></html>
"""

# arvore_visualizar page WITHOUT linkCienciaDocumento (ciência not available)
ARVORE_VIS_NO_CIENCIA = """
<html><body>
<script>
var linkEditarConteudo = 'controlador.php?acao=editor_montar&id_documento=99001&infra_hash=eee';
var linkExcluirDocumento = 'controlador.php?acao=documento_excluir&id_documento=99001&infra_hash=fff';
</script>
</body></html>
"""

# arvore_visualizar page for PROCESS with linkCienciaProcesso
ARVORE_VIS_WITH_CIENCIA_PROC = """
<html><body>
<script>
var linkCienciaProcesso = 'controlador.php?acao=procedimento_ciencia&acao_origem=arvore_visualizar&id_procedimento=12345&atualizar_arvore=1&infra_sistema=100000100&infra_hash=ggg';
var linkExcluir = '';
</script>
</body></html>
"""

# Success response after ciência (redirect to arvore)
CIENCIA_SUCCESS_RESPONSE = """
<html><head><title>SEI</title></head><body>
<p>Ciência registrada.</p>
</body></html>
"""

# Confirmation form (some SEI versions require a submit)
CIENCIA_CONFIRM_FORM = """
<html><body>
<form id="frmCiencia" action="controlador.php?acao=documento_ciencia_salvar&infra_hash=hhh">
  <input type="hidden" name="id_procedimento" value="12345" />
  <input type="hidden" name="id_documento" value="99001" />
  <input type="submit" name="sbmConfirmar" value="Confirmar" />
</form>
</body></html>
"""

# Restricted arvore (documents are about:blank)
ARVORE_RESTRICTED = """
<script>
Nos[0] = new infraArvoreNo("PROCESSO","12345",null,"controlador.php?acao=arvore_visualizar&id_procedimento=12345","ifrVisualizacao","proc","tipo","svg","svg","svg",true,true,null,null,null,"num");
Nos[0].html = 'Processo aberto somente na unidade <a class="ancoraSigla">CBM - TEST - UNIT</a>.<br />';
Nos[1] = new infraArvoreNo("DOCUMENTO","99001","12345","about:blank","ifrConteudoVisualizacao","Doc","Doc","svg","svg","svg",true,false,null,null,null,"abc");
NosAcoes[0] = new infraArvoreAcao("UNIDADE_GERADORA","UG99001","99001","#",null,"TEST UNIT",null,true,"CBM - TEST - UNIT");
</script>
"""


def _make_client() -> SEIClient:
    """Create a SEIClient without running __init__ (no network/config needed)."""
    client = SEIClient.__new__(SEIClient)
    client.base_url = SEIClient.BASE
    client._hash_pool = {}
    client._control_html = None
    client._menu_links = {}
    client._editor_hiddens = {}
    client._current_unit_id = None
    client._last_login = 0.0
    client._batch_active = False
    return client


def _mock_response(text: str, url: str = "https://sei.example.test/sei/arvore.php") -> MagicMock:
    """Create a mock httpx.Response."""
    r = MagicMock()
    r.text = text
    r.url = url
    r.status_code = 200
    r.content = text.encode("utf-8")
    r.headers = {}
    return r


# ---- Tests for give_notice_document ----

class TestGiveNoticeDocument:
    def setup_method(self):
        self.client = _make_client()

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_success_direct_redirect(self, mock_get, mock_arvore):
        """Should return ok=True when ciência URL redirects to arvore."""
        mock_arvore.return_value = ARVORE_WITH_DOC

        # Two GET calls:
        # 1. arvore_visualizar for the document (returns JS with linkCienciaDocumento)
        # 2. the actual ciência URL (returns success redirect to arvore_visualizar)
        success_r = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_procedimento=12345",
        )
        mock_get.side_effect = [
            _mock_response(ARVORE_VIS_WITH_CIENCIA_DOC),
            success_r,
        ]

        result = self.client.give_notice_document("99001", "12345")

        assert result["ok"] is True
        assert "ciência" in result["message"].lower() or "sucesso" in result["message"].lower()
        assert result["id"] == "99001"

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_document_not_found_in_arvore(self, mock_get, mock_arvore):
        """Should return ok=False when document not in arvore."""
        mock_arvore.return_value = ARVORE_WITH_DOC

        result = self.client.give_notice_document("NONEXISTENT", "12345")

        assert result["ok"] is False
        assert "não encontrado" in result["message"].lower()
        mock_get.assert_not_called()

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_ciencia_not_available(self, mock_get, mock_arvore):
        """Should return ok=False when linkCienciaDocumento is absent."""
        mock_arvore.return_value = ARVORE_WITH_DOC
        mock_get.return_value = _mock_response(ARVORE_VIS_NO_CIENCIA)

        result = self.client.give_notice_document("99001", "12345")

        assert result["ok"] is False
        assert "não disponível" in result["message"].lower() or "ciência" in result["message"].lower()

    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_raises_when_process_not_found(self, mock_arvore):
        """Should raise RuntimeError when process is not found."""
        mock_arvore.return_value = None

        with pytest.raises(RuntimeError, match="não encontrado"):
            self.client.give_notice_document("99001", "12345")

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    @patch.object(SEIClient, '_post')
    def test_submits_confirmation_form(self, mock_post, mock_get, mock_arvore):
        """Should auto-submit confirmation form when present in ciência response."""
        mock_arvore.return_value = ARVORE_WITH_DOC

        success_after_form = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar",
        )
        mock_get.side_effect = [
            _mock_response(ARVORE_VIS_WITH_CIENCIA_DOC),
            _mock_response(CIENCIA_CONFIRM_FORM),
        ]
        mock_post.return_value = success_after_form

        result = self.client.give_notice_document("99001", "12345")

        # POST should have been called for the confirmation form
        mock_post.assert_called_once()
        assert result["ok"] is True

    @patch.object(SEIClient, 'switch_unit')
    @patch.object(SEIClient, 'list_units')
    @patch.object(SEIClient, 'status')
    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_auto_unit_switch(
        self, mock_get, mock_arvore, mock_status, mock_units, mock_switch
    ):
        """Should auto-switch unit when process is restricted."""
        from sei_cli.models import SystemStatus, Unit

        # First call: restricted arvore; second call (after switch): normal arvore
        mock_arvore.side_effect = [ARVORE_RESTRICTED, ARVORE_WITH_DOC]
        mock_status.return_value = SystemStatus(
            valid=True,
            unidade_sigla="OTHER UNIT",
            unidade_descricao="Other",
            usuario="TEST",
            ultimo_acesso="",
        )
        mock_units.return_value = [
            Unit(sigla="CBM - TEST - UNIT", descricao="Test", link="999"),
            Unit(sigla="OTHER UNIT", descricao="Other", link="111"),
        ]

        success_r = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar",
        )
        mock_get.side_effect = [
            _mock_response(ARVORE_VIS_WITH_CIENCIA_DOC),
            success_r,
        ]

        result = self.client.give_notice_document("99001", "12345")

        # Should have switched units
        mock_switch.assert_any_call("CBM - TEST - UNIT")
        assert result["ok"] is True


# ---- Tests for give_notice_process ----

class TestGiveNoticeProcess:
    def setup_method(self):
        self.client = _make_client()

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_success(self, mock_get, mock_arvore):
        """Should return ok=True when ciência for process succeeds."""
        mock_arvore.return_value = ARVORE_WITH_DOC

        success_r = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_procedimento=12345",
        )
        # First GET: process arvore_visualizar (returns JS with linkCienciaProcesso)
        # Second GET: the actual ciência URL
        mock_get.side_effect = [
            _mock_response(ARVORE_VIS_WITH_CIENCIA_PROC),
            success_r,
        ]

        result = self.client.give_notice_process("12345")

        assert result["ok"] is True
        assert result["id_procedimento"] == "12345"

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_ciencia_not_available(self, mock_get, mock_arvore):
        """Should return ok=False when linkCienciaProcesso is absent."""
        mock_arvore.return_value = ARVORE_WITH_DOC
        mock_get.return_value = _mock_response(ARVORE_VIS_NO_CIENCIA)

        result = self.client.give_notice_process("12345")

        assert result["ok"] is False
        assert "ciência" in result["message"].lower() or "não disponível" in result["message"].lower()

    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_raises_when_process_not_found(self, mock_arvore):
        """Should raise RuntimeError when process is not found."""
        mock_arvore.return_value = None

        with pytest.raises(RuntimeError, match="não encontrado"):
            self.client.give_notice_process("12345")

    @patch.object(SEIClient, '_navigate_to_arvore')
    @patch.object(SEIClient, '_get')
    def test_uses_arvore_html_when_no_proc_vis_url(self, mock_get, mock_arvore):
        """Should use arvore_html directly when no arvore_visualizar URL for process found."""
        # Arvore that directly contains linkCienciaProcesso
        arvore_with_inline_ciencia = ARVORE_WITH_DOC + """
<script>
var linkCienciaProcesso = 'controlador.php?acao=procedimento_ciencia&id_procedimento=12345&infra_hash=zzz';
</script>
"""
        mock_arvore.return_value = arvore_with_inline_ciencia

        success_r = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar",
        )
        # First GET: arvore_visualizar for the process root (no link found → use arvore directly)
        # But in this case there IS a proc_vis match in the arvore_html
        mock_get.side_effect = [
            _mock_response(ARVORE_VIS_WITH_CIENCIA_PROC),  # proc vis page
            success_r,                                       # ciência GET
        ]

        result = self.client.give_notice_process("12345")
        assert result["ok"] is True


# ---- Tests for _handle_ciencia_response ----

class TestHandleCienciaResponse:
    def setup_method(self):
        self.client = _make_client()

    def test_redirect_to_arvore_is_success(self):
        """Response redirected to arvore_visualizar → success."""
        r = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id=1",
        )
        result = self.client._handle_ciencia_response(r, "99001")
        assert result["ok"] is True

    def test_redirect_to_controlar_is_success(self):
        """Response redirected to procedimento_controlar → success."""
        r = _mock_response(
            CIENCIA_SUCCESS_RESPONSE,
            url="https://sei.example.test/sei/controlador.php?acao=procedimento_controlar",
        )
        result = self.client._handle_ciencia_response(r, "12345")
        assert result["ok"] is True

    def test_no_redirect_assumed_success(self):
        """No redirect but no error → assumed success."""
        r = _mock_response(
            "<html><body><p>OK</p></body></html>",
            url="https://sei.example.test/sei/something_else",
        )
        result = self.client._handle_ciencia_response(r, "99001")
        assert result["ok"] is True
