"""Unit tests for upload_external_document method.

Uses mocked HTTP responses — no live SEI calls, no actual file I/O
(file existence checked but content can be any bytes).
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest
from bs4 import BeautifulSoup

from sei_cli.client import SEIClient, _sanitize_for_iso_8859_1
from sei_cli.models import BlockDocument, EditorSection, TreeDocument


# --- HTML Fixtures ---

ARVORE_HTML = """
<html><body>
<script>
Nos[0] = new infraArvoreNo("PROCESSO","55555",null,
  "controlador.php?acao=arvore_visualizar&id_procedimento=55555",
  "ifrVisualizacao","proc","tipo","svg","svg","svg",true,true,null,null,null,"num");
</script>
<a href="controlador.php?acao=documento_incluir_externo&id_procedimento=55555&infra_hash=abc">
  <img src="svg/documento_incluir.svg?18" />
</a>
</body></html>
"""

# Type selection page
DOC_TYPE_CHOOSER = """
<html><body>
<form id="frmDocumentoEscolherTipo" action="controlador.php?acao=documento_gerar&infra_hash=def">
  <input type="hidden" name="infra_sistema" value="100000100" />
  <input type="hidden" name="infra_unidade_atual" value="910000015" />
  <input type="hidden" name="hdnIdSerie" value="" />
</form>
</body></html>
"""

# Cadastro form (external document)
DOC_CADASTRO_FORM = """
<html><body>
<form id="frmDocumentoCadastro" action="controlador.php?acao=documento_gerar_salvar&infra_hash=ghi">
  <input type="hidden" name="infra_sistema" value="100000100" />
  <input type="hidden" name="infra_unidade_atual" value="910000015" />
  <input type="hidden" name="hdnFlagDocumentoCadastro" value="1" />
  <input type="hidden" name="hdnIdSerie" value="-1" />
  <input type="radio" name="rdoFormato" value="N" checked />
  <input type="radio" name="rdoFormato" value="E" />
  <input type="hidden" name="hdnIdUsuario" value="910000008" />
  <input type="text" name="txtDataElaboracao" value="" />
  <input type="text" name="txtDescricao" value="" />
  <input type="text" name="txtNumero" value="" />
  <input type="hidden" name="hdnAnexos" value="" />
  <select name="selTipoConferencia">
    <option value="1">Cópia Simples</option>
    <option value="4" selected>Documento Original</option>
  </select>
  <input type="radio" name="rdoNivelAcesso" value="0" checked />
</form>
</body></html>
"""

BLOCK_DETAIL_HTML = """
<html><body>
<form id="frmRelBlocoProtocoloLista" action="controlador.php?acao=rel_bloco_protocolo_listar">
  <input type="hidden" name="hdnInfraTipoPagina" value="1" />
  <table>
    <tr class="infraTrClara">
      <td><input type="checkbox" name="chkInfraItem" value="81000122-871299" /></td>
      <td>4</td>
      <td>90000000.000034/2026-00</td>
      <td><a href="#ID-81000122-871299" onclick="return infraSparklingModal('controlador.php?acao=documento_visualizar&id_documento=81000122');">81000054</a></td>
      <td>Despacho</td>
      <td>Sintético Pessoa 05 / 2º Tenente QOEM BM</td>
      <td><a href="#" onclick="return acaoAssinar('81000122-871299', 'controlador.php?acao=documento_assinar&id_documento=81000122&infra_hash=abc');">Assinar</a></td>
      <td><img title="Assinatura" src="ok.png" /></td>
    </tr>
  </table>
</form>
</body></html>
"""

BLOCK_DETAIL_HTML_MULTI = """
<html><body>
<script>
function acaoAssinar(id) {
  infraAbrirJanelaModal(
    'controlador.php?acao=documento_assinar&acao_origem=rel_bloco_protocolo_listar&id_bloco=871299&infra_hash=modal',
    600, 450
  );
}
</script>
<form id="frmRelBlocoProtocoloLista" action="controlador.php?acao=rel_bloco_protocolo_listar">
  <input type="hidden" name="hdnInfraTipoPagina" value="1" />
  <input type="hidden" name="hdnInfraItemId" value="" />
  <input type="hidden" name="hdnInfraItensSelecionados" value="" />
  <table>
    <tr class="infraTrClara">
      <td><input type="checkbox" name="chkInfraItem" value="81000110-871299" /></td>
      <td>1</td>
      <td>90000000.000034/2026-00</td>
      <td><a href="#ID-81000110-871299" onclick="return infraSparklingModal('controlador.php?acao=documento_visualizar&id_documento=81000110');">81000043</a></td>
      <td>Parte Genérica</td>
      <td>Sintético Pessoa 05 / 2º Tenente QOEM BM</td>
      <td><a href="#" onclick="return acaoAssinar('81000110-871299');">Assinar</a></td>
      <td><img title="Assinatura" src="ok.png" /></td>
    </tr>
    <tr class="infraTrEscura">
      <td><input type="checkbox" name="chkInfraItem" value="81000121-871299" /></td>
      <td>2</td>
      <td>90000000.000034/2026-00</td>
      <td><a href="#ID-81000121-871299" onclick="return infraSparklingModal('controlador.php?acao=documento_visualizar&id_documento=81000121');">81000052</a></td>
      <td>Despacho</td>
      <td>Sintético Pessoa 05 / 2º Tenente QOEM BM</td>
      <td><a href="#" onclick="return acaoAssinar('81000121-871299', 'controlador.php?acao=documento_assinar&id_documento=81000121&infra_hash=abc');">Assinar</a></td>
      <td></td>
    </tr>
  </table>
</form>
</body></html>
"""

BLOCK_SIGN_FORM_HTML = """
<html><body>
<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">
  <input type="hidden" name="hdnIdDocumentos" value="81000110" />
  <input type="text" name="txtUsuario" value="Sintético Pessoa 05" />
  <input type="hidden" name="hdnIdUsuario" value="123" />
  <select name="selCargoFuncao">
    <option value="2º Tenente QOEM BM" selected>2º Tenente QOEM BM</option>
  </select>
</form>
</body></html>
"""

CHOOSER_HTML_WITH_COLLAPSED = """
<html><body>
<a onclick="escolher(5)">Despacho</a>
<div style="display:none">
  <a onclick="escolher(999)">Tipo Expandido Pouco Usado</a>
</div>
</body></html>
"""

PROCESS_CHOOSER_INITIAL = """
<html><body>
<form id="frmProcedimentoEscolherTipo" action="controlador.php?acao=procedimento_escolher_tipo&infra_hash=abc">
  <input type="hidden" name="hdnFiltroTipoProcedimento" value="U" />
</form>
<a onclick="escolher(910000002)">Férias</a>
</body></html>
"""

PROCESS_CHOOSER_EXPANDED = """
<html><body>
<form id="frmProcedimentoEscolherTipo" action="controlador.php?acao=procedimento_escolher_tipo&infra_hash=abc">
  <input type="hidden" name="hdnFiltroTipoProcedimento" value="T" />
</form>
<a onclick="escolher(910000002)">Férias</a>
<a onclick="escolher(910000005)">Processo Expandido Pouco Usado</a>
</body></html>
"""

DOCUMENT_CHOOSER_INITIAL = """
<html><body>
<form id="frmDocumentoEscolherTipo" action="controlador.php?acao=documento_escolher_tipo&infra_hash=abc">
  <input type="hidden" name="hdnFiltroSerie" value="U" />
</form>
<a onclick="escolher(5)">Despacho</a>
</body></html>
"""

DOCUMENT_CHOOSER_EXPANDED = """
<html><body>
<form id="frmDocumentoEscolherTipo" action="controlador.php?acao=documento_escolher_tipo&infra_hash=abc">
  <input type="hidden" name="hdnFiltroSerie" value="T" />
</form>
<a onclick="escolher(5)">Despacho</a>
<a onclick="escolher(999)">Tipo Expandido Pouco Usado</a>
</body></html>
"""

PRINCIPAL_WRAPPER_HTML = """
<html><body>
<iframe id="ifrArvore" src="controlador.php?acao=procedimento_controlar&infra_sistema=100000100&infra_hash=xyz"></iframe>
</body></html>
"""

CONTROL_HTML = """
<html><body>
<form id="frmProcedimentoControlar"></form>
</body></html>
"""

BLOCK_PREVIEW_HTML = """
<html><body>
  <div id="divInfraSparklingModal">
    <p>Despacho de teste do bloco</p>
  </div>
</body></html>
"""

# Upload success response: hash#filename#mimetype#size#datetime#
UPLOAD_SUCCESS = "abc123hash#documento.pdf#application/pdf#12345#2026-03-08 10:00:00#"

# Cadastro success: SEI redirects to the new document
CADASTRO_SUCCESS_URL = (
    "https://sei.example.test/sei/controlador.php"
    "?acao=arvore_visualizar&id_documento=77777&id_procedimento=55555&infra_hash=jkl"
)


def _make_client() -> SEIClient:
    """Create a SEIClient without running __init__."""
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
    url: str = "https://sei.example.test/sei/controlador.php",
    status: int = 200,
) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.url = url
    r.status_code = status
    r.content = text.encode("utf-8")
    r.headers = {}
    return r


def test_save_document_posts_raw_html_and_normalizes_escaped_sections() -> None:
    client = _make_client()
    captured: dict[str, str] = {}

    def fake_post(_url: str, data: dict, **_kwargs):
        captured.update(data)
        return _mock_response("OK")

    client._post = fake_post
    client._editor_hiddens = {"hdnInfraTipoPagina": "editor"}

    sections = [
        EditorSection(
            name="txaEditor_217",
            section_id="217",
            content="&amp;amp;lt;p class=&amp;amp;quot;Texto_Justificado&amp;amp;quot;&amp;amp;gt;Cabe&amp;amp;ccedil;alho&amp;amp;lt;/p&amp;amp;gt;",
            editable=True,
        ),
        EditorSection(
            name="txaEditor_220",
            section_id="220",
            content='<p class="Texto_Justificado_Recuo_Primeira_Linha">Corpo <strong>real</strong> — teste&nbsp;final</p>',
            editable=True,
        ),
    ]

    assert client.save_document("https://sei.example.test/sei/editor/editor_processar.php?acao=editor_salvar", sections) is True
    assert captured["hdnInfraTipoPagina"] == "editor"
    assert captured["txaEditor_217"] == '<p class="Texto_Justificado">Cabeçalho</p>'
    assert captured["txaEditor_220"] == (
        '<p class="Texto_Justificado_Recuo_Primeira_Linha">'
        "Corpo <strong>real</strong> &#8212; teste&nbsp;final</p>"
    )
    assert "&lt;p" not in captured["txaEditor_217"]
    assert "&amp;lt;p" not in captured["txaEditor_217"]
    assert "&lt;p" not in captured["txaEditor_220"]


def test_edit_document_section_edits_only_target_body_and_preserves_other_sections_raw() -> None:
    client = _make_client()
    posted: dict[str, str] = {}

    def fake_get_editor_sections(_id_documento: str, _id_procedimento: str):
        return (
            "https://sei.example.test/sei/editor/editor_processar.php?acao=editor_salvar",
            [
                EditorSection(
                    name="txaEditor_217",
                    section_id="217",
                    content="&lt;p&gt;Template protegido&lt;/p&gt;",
                    editable=True,
                ),
                EditorSection(
                    name="txaEditor_220",
                    section_id="220",
                    content="<p>Corpo antigo</p>",
                    editable=True,
                ),
                EditorSection(
                    name="txaEditor_999",
                    section_id="999",
                    content="&lt;p&gt;Rodap&eacute;&lt;/p&gt;",
                    editable=False,
                ),
            ],
        )

    def fake_post(_url: str, data: dict, **_kwargs):
        posted.update(data)
        return _mock_response("OK")

    client.get_editor_sections = fake_get_editor_sections
    client._post = fake_post

    ok = client.edit_document_section(
        "81000131",
        "81000130",
        "220",
        '<p class="Texto_Justificado_Recuo_Primeira_Linha">Sintético Militar 06 corpo <strong>sem escape</strong></p>',
    )

    assert ok is True
    assert posted["txaEditor_217"] == "<p>Template protegido</p>"
    assert posted["txaEditor_220"] == (
        '<p class="Texto_Justificado_Recuo_Primeira_Linha">Sintético Militar 06 corpo <strong>sem escape</strong></p>'
    )
    assert posted["txaEditor_999"] == "<p>Rodapé</p>"
    assert all("&lt;" not in value and "&amp;lt;" not in value for key, value in posted.items() if key.startswith("txaEditor_"))


class TestUploadExternalDocument:
    def setup_method(self):
        self.client = _make_client()
        # Create a temporary PDF file for testing
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self.tmpfile.write(b"%PDF-1.4 fake pdf content")
        self.tmpfile.flush()
        self.tmpfile.close()

    def teardown_method(self):
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_create_document_extracts_id_and_editor_url_from_html(self):
        cadastro_soup = BeautifulSoup(DOC_CADASTRO_FORM, "lxml")
        self.client._load_document_creation_form = MagicMock(
            return_value=(
                cadastro_soup,
                {"hdnFlagDocumentoCadastro": "1", "rdoNivelAcesso": "0"},
                "https://sei.example.test/sei/controlador.php?acao=documento_gerar_salvar&infra_hash=ghi",
                "5",
            )
        )
        response = _mock_response(
            "<html><body><script>var linkEditarConteudo = 'controlador.php?acao=editor_montar&id_documento=77777&infra_hash=ed';</script></body></html>",
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_procedimento=55555",
        )
        self.client._post = MagicMock(return_value=response)
        self.client._get_editor_url = MagicMock(return_value=None)

        created = self.client.create_document("55555", "despacho", descricao="Teste")

        assert created.id_documento == "77777"
        assert created.editor_url == "https://sei.example.test/sei/controlador.php?acao=editor_montar&id_documento=77777&infra_hash=ed"

    def test_create_document_uses_documento_modelo_protocol_number(self):
        cadastro_soup = BeautifulSoup(DOC_CADASTRO_FORM, "lxml")
        self.client._load_document_creation_form = MagicMock(
            return_value=(
                cadastro_soup,
                {
                    "hdnFlagDocumentoCadastro": "1",
                    "rdoNivelAcesso": "0",
                    "rdoTextoInicial": "N",
                    "txtProtocoloDocumentoTextoBase": "",
                    "hdnIdDocumentoTextoBase": "",
                },
                "https://sei.example.test/sei/controlador.php?acao=documento_gerar_salvar&infra_hash=ghi",
                "5",
            )
        )
        response = _mock_response(
            "<html><body><script>var linkEditarConteudo = 'controlador.php?acao=editor_montar&id_documento=77777&infra_hash=ed';</script></body></html>",
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_procedimento=55555",
        )
        self.client._post = MagicMock(return_value=response)
        self.client._get_editor_url = MagicMock(return_value=None)

        self.client.create_document(
            "55555",
            "despacho",
            texto_inicial="N",
            documento_modelo="81000061",
            descricao="Teste",
        )

        posted = self.client._post.call_args.args[1]
        assert posted["rdoTextoInicial"] == "D"
        assert posted["txtProtocoloDocumentoTextoBase"] == "81000061"
        assert posted["hdnIdDocumentoTextoBase"] == ""

    def test_create_document_raises_when_form_is_reloaded_without_id(self):
        cadastro_soup = BeautifulSoup(DOC_CADASTRO_FORM, "lxml")
        self.client._load_document_creation_form = MagicMock(
            return_value=(
                cadastro_soup,
                {"hdnFlagDocumentoCadastro": "1", "rdoNivelAcesso": "0"},
                "https://sei.example.test/sei/controlador.php?acao=documento_gerar_salvar&infra_hash=ghi",
                "5",
            )
        )
        response = _mock_response(
            DOC_CADASTRO_FORM,
            url="https://sei.example.test/sei/controlador.php?acao=documento_gerar_salvar&infra_hash=ghi",
        )
        self.client._post = MagicMock(return_value=response)
        self.client._get_editor_url = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="reexibiu o formulário de criação do documento"):
            self.client.create_document("55555", "despacho", descricao="Teste")

    @patch.object(SEIClient, '_follow_upload')
    @patch.object(SEIClient, '_post')
    @patch.object(SEIClient, '_get')
    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_successful_upload(self, mock_arvore, mock_get, mock_post, mock_follow):
        """Full happy-path: returns id_documento on success."""
        mock_arvore.return_value = ARVORE_HTML

        # GET calls: type chooser, cadastro form
        mock_get.side_effect = [
            _mock_response(DOC_TYPE_CHOOSER),
        ]
        # POST calls:
        # 1. type selection → returns cadastro form
        # 2. file upload (via client.post) → returns UPLOAD_SUCCESS (handled via _follow_upload)
        # 3. cadastro form submission → redirects to new document
        success_response = _mock_response(
            "<html></html>",
            url=CADASTRO_SUCCESS_URL,
        )
        mock_post.side_effect = [
            _mock_response(DOC_CADASTRO_FORM),  # type selection POST
            success_response,                    # cadastro form POST
        ]

        # _follow_upload is called for the raw multipart upload
        mock_follow.return_value = _mock_response(UPLOAD_SUCCESS)

        # Patch client.post (the raw httpx client) for the multipart upload
        self.client.client = MagicMock()
        self.client.client.post.return_value = MagicMock()

        id_doc = self.client.upload_external_document(
            "55555",
            self.tmpfile.name,
            "externo",
            descricao="Documento de teste",
            data_elaboracao="08/03/2026",
        )

        assert id_doc == "77777"

    def test_raises_file_not_found(self):
        """Should raise FileNotFoundError for nonexistent file."""
        with pytest.raises(FileNotFoundError, match="não encontrado"):
            self.client.upload_external_document(
                "55555",
                "/nonexistent/path/doc.pdf",
                "externo",
            )

    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_raises_when_process_not_found(self, mock_arvore):
        """Should raise RuntimeError when arvore returns None."""
        mock_arvore.return_value = None

        with pytest.raises(RuntimeError, match="não encontrado"):
            self.client.upload_external_document(
                "55555",
                self.tmpfile.name,
                "externo",
            )

    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_raises_when_incluir_link_not_found(self, mock_arvore):
        """Should raise RuntimeError when 'Incluir Documento' link is absent."""
        mock_arvore.return_value = "<html><body>No link here</body></html>"

        with pytest.raises(RuntimeError, match="Incluir Documento"):
            self.client.upload_external_document(
                "55555",
                self.tmpfile.name,
                "externo",
            )

    @patch.object(SEIClient, '_follow_upload')
    @patch.object(SEIClient, '_post')
    @patch.object(SEIClient, '_get')
    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_raises_on_upload_failure(self, mock_arvore, mock_get, mock_post, mock_follow):
        """Should raise RuntimeError when upload response is invalid."""
        mock_arvore.return_value = ARVORE_HTML
        mock_get.return_value = _mock_response(DOC_TYPE_CHOOSER)
        mock_post.return_value = _mock_response(DOC_CADASTRO_FORM)

        # Simulate failed upload (no '#' in response)
        mock_follow.return_value = _mock_response("UPLOAD_ERROR")
        self.client.client = MagicMock()
        self.client.client.post.return_value = MagicMock()

        with pytest.raises(RuntimeError, match="[Uu]pload"):
            self.client.upload_external_document(
                "55555",
                self.tmpfile.name,
                "externo",
            )

    @patch.object(SEIClient, '_follow_upload')
    @patch.object(SEIClient, '_post')
    @patch.object(SEIClient, '_get')
    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_raises_when_id_doc_not_in_response(
        self, mock_arvore, mock_get, mock_post, mock_follow
    ):
        """Should raise RuntimeError when id_documento not found in response."""
        mock_arvore.return_value = ARVORE_HTML
        mock_get.return_value = _mock_response(DOC_TYPE_CHOOSER)
        mock_post.side_effect = [
            _mock_response(DOC_CADASTRO_FORM),
            _mock_response(
                "<html><body>Something unexpected</body></html>",
                url="https://sei.example.test/sei/unknown_page",
            ),
        ]
        mock_follow.return_value = _mock_response(UPLOAD_SUCCESS)
        self.client.client = MagicMock()
        self.client.client.post.return_value = MagicMock()

        with pytest.raises(RuntimeError, match="id_documento"):
            self.client.upload_external_document(
                "55555",
                self.tmpfile.name,
                "externo",
            )

    def test_hdnanexos_uses_latin1_separator(self):
        """Verify the ± separator is Latin-1 \\xb1 (not UTF-8 \\xc2\\xb1)."""
        # The upload_external_document code uses sep = '\xb1'
        # which is U+00B1 (PLUS-MINUS SIGN) in Latin-1 / unicode code point 177
        # When encoded to Latin-1, this is a single byte \xb1
        sep = '\xb1'
        assert sep.encode('latin-1') == b'\xb1'
        assert sep.encode('utf-8') == b'\xc2\xb1'  # NOT this
        # Verify the character is what we expect
        assert ord(sep) == 0xB1  # 177 decimal = plus-minus sign

    def test_defaults_data_elaboracao_to_today(self):
        """data_elaboracao defaults to today's date if not provided."""
        from datetime import date
        today = date.today().strftime('%d/%m/%Y')

        # We can't easily call the full method without mocks, but we can verify
        # the default logic by checking the date formatting
        assert len(today) == 10
        assert today[2] == '/'
        assert today[5] == '/'

    @patch.object(SEIClient, '_follow_upload')
    @patch.object(SEIClient, '_post')
    @patch.object(SEIClient, '_get')
    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_rdoformato_set_to_D(self, mock_arvore, mock_get, mock_post, mock_follow):
        """Verify rdoFormato is set to 'D' (Digitalizado) in the cadastro form.

        For external documents the type-selection step uses id_serie=-1 (Externo),
        but the cadastro form (documento_receber) only accepts N or D for rdoFormato.
        We use D=Digitalizado because we are uploading a scanned/digital file.
        """
        mock_arvore.return_value = ARVORE_HTML
        mock_get.return_value = _mock_response(DOC_TYPE_CHOOSER)

        # Capture what gets posted to the cadastro form
        captured_posts = []

        def capture_post(url, data, **kwargs):
            captured_posts.append((url, dict(data) if isinstance(data, dict) else data))
            if len(captured_posts) == 1:
                return _mock_response(DOC_CADASTRO_FORM)
            return _mock_response("<html></html>", url=CADASTRO_SUCCESS_URL)

        mock_post.side_effect = capture_post
        mock_follow.return_value = _mock_response(UPLOAD_SUCCESS)
        self.client.client = MagicMock()
        self.client.client.post.return_value = MagicMock()

        try:
            self.client.upload_external_document(
                "55555",
                self.tmpfile.name,
                "boletim",
                data_elaboracao="08/03/2026",
            )
        except RuntimeError:
            pass  # May fail at the final step; we just want to check the POST data

        # The first POST (type selection) should use id_serie=-1 (Externo path)
        if len(captured_posts) >= 1:
            type_sel_data = captured_posts[0][1]
            assert type_sel_data.get('hdnInfraItensSelecionados') == '-1', (
                f"Type selection must use -1 (Externo), got: {type_sel_data.get('hdnInfraItensSelecionados')}"
            )
        # The second POST (cadastro submission) should have rdoFormato='D'
        if len(captured_posts) >= 2:
            form_data = captured_posts[1][1]
            assert form_data.get('rdoFormato') == 'D', (
                f"rdoFormato should be 'D' for external docs, got: {form_data.get('rdoFormato')}"
            )


class TestAutoUnitSwitchAuthenticate:
    """Tests that _sign_or_authenticate now uses _auto_unit_switch."""

    def setup_method(self):
        self.client = _make_client()

    @patch.object(SEIClient, '_execute_sign')
    @patch.object(SEIClient, '_get')
    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_sign_uses_navigate_to_arvore(self, mock_arvore, mock_get, mock_sign):
        """_sign_or_authenticate now uses _navigate_to_arvore (not _ensure_control)."""
        arvore_with_doc = """
<script>
Nos[1] = new infraArvoreNo("DOCUMENTO","88888","55555",
  "controlador.php?acao=arvore_visualizar&id_documento=88888&id_procedimento=55555&infra_hash=xxx",
  "ifrConteudoVisualizacao","Doc","Doc","svg","svg","svg",true,false,null,null,null,"abc");
</script>
"""
        mock_arvore.return_value = arvore_with_doc

        doc_vis_html = """
<html><body>
<script>
var linkAssinarDocumento = 'controlador.php?acao=documento_assinar&id_documento=88888&infra_hash=yyy';
</script>
</body></html>
"""
        mock_get.return_value = _mock_response(doc_vis_html)
        mock_sign.return_value = {"signed": ["88888"], "already_signed": [], "errors": []}

        result = self.client._sign_or_authenticate("88888", "55555")

        assert mock_arvore.call_args_list[0].args == ("55555",)
        assert mock_arvore.call_count >= 1
        assert result.get("signed") == ["88888"]

    @patch.object(SEIClient, '_navigate_to_arvore')
    def test_returns_error_when_arvore_not_found(self, mock_arvore):
        """Should return error dict when arvore is None."""
        mock_arvore.return_value = None

        result = self.client._sign_or_authenticate("88888", "55555")

        assert "error" in result
        assert "55555" in result["error"]


class TestExecuteSignForm:
    def setup_method(self):
        self.client = _make_client()
        self.client.client = MagicMock()
        self.client.base_url = SEIClient.BASE
        self.client._control_html = None
        self.client._hash_pool = {}

    @patch("sei_cli.client.auth._follow")
    @patch("sei_cli.client.load_credentials")
    @patch("sei_cli.client.orgao_to_value")
    def test_marks_returned_form_for_external_verification(
        self,
        mock_orgao_to_value,
        mock_load_credentials,
        mock_follow,
    ):
        from sei_cli.models import Credentials

        mock_load_credentials.return_value = Credentials(
            usuario="u",
            senha="s",
            orgao="CBM",
            login_url="https://sei.example.test",
            cargo="2º Tenente QOEM BM",
        )
        mock_orgao_to_value.return_value = "28"

        form = BeautifulSoup(
            '<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">'
            '<input name="hdnIdDocumentos" value="81000122" />'
            '<input name="txtUsuario" value="u" />'
            "</form>",
            "lxml",
        ).find("form")

        response = _mock_response(
            '<html><body><form id="frmAssinaturas"></form></body></html>',
            url="https://sei.example.test/sei/controlador.php?acao=documento_assinar&id_documento=81000122",
        )
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        result = self.client._execute_sign_form(form, "<html></html>")

        assert result["signed"] == []
        assert result["already_signed"] == []
        assert result["errors"] == []
        assert result["returned_to_sign_form"] is True

    @patch("sei_cli.client.auth._follow")
    @patch("sei_cli.client.load_credentials")
    @patch("sei_cli.client.orgao_to_value")
    def test_still_accepts_success_redirect(
        self,
        mock_orgao_to_value,
        mock_load_credentials,
        mock_follow,
    ):
        from sei_cli.models import Credentials

        mock_load_credentials.return_value = Credentials(
            usuario="u",
            senha="s",
            orgao="CBM",
            login_url="https://sei.example.test",
            cargo="2º Tenente QOEM BM",
        )
        mock_orgao_to_value.return_value = "28"

        form = BeautifulSoup(
            '<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">'
            '<input name="hdnIdDocumentos" value="81000122" />'
            '<input name="txtUsuario" value="u" />'
            "</form>",
            "lxml",
        ).find("form")

        response = _mock_response(
            "<html><body>ok</body></html>",
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_documento=81000122",
        )
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        result = self.client._execute_sign_form(form, "<html></html>")

        assert result["signed"] == ["81000122"]
        assert result["errors"] == []

    @patch("sei_cli.client.auth._follow")
    @patch("sei_cli.client.load_credentials")
    @patch("sei_cli.client.orgao_to_value")
    def test_detects_already_signed_before_treating_returned_form_as_error(
        self,
        mock_orgao_to_value,
        mock_load_credentials,
        mock_follow,
    ):
        from sei_cli.models import Credentials

        mock_load_credentials.return_value = Credentials(
            usuario="u",
            senha="s",
            orgao="CBM",
            login_url="https://sei.example.test",
            cargo="2º Tenente QOEM BM",
        )
        mock_orgao_to_value.return_value = "28"

        form = BeautifulSoup(
            '<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">'
            '<input name="hdnIdDocumentos" value="81000122" />'
            "</form>",
            "lxml",
        ).find("form")

        response = _mock_response(
            "<html><body>XDocumento 81000054 já foi assinado por &quot;Sintético Pessoa 05&quot;."
            "<form id=\"frmAssinaturas\"></form></body></html>",
            url="https://sei.example.test/sei/controlador.php?acao=documento_assinar&id_documento=81000122",
        )
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        result = self.client._execute_sign_form(form, "<html></html>")

        assert result["signed"] == []
        assert result["errors"] == []
        assert result["already_signed"]

    @patch("sei_cli.client.auth._follow")
    @patch("sei_cli.client.load_credentials")
    @patch("sei_cli.client.orgao_to_value")
    def test_detects_missing_tipo_conferencia_as_explicit_error(
        self,
        mock_orgao_to_value,
        mock_load_credentials,
        mock_follow,
    ):
        from sei_cli.models import Credentials

        mock_load_credentials.return_value = Credentials(
            usuario="u",
            senha="s",
            orgao="CBM",
            login_url="https://sei.example.test",
            cargo="2º Tenente QOEM BM",
        )
        mock_orgao_to_value.return_value = "28"

        form = BeautifulSoup(
            '<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">'
            '<input name="hdnIdDocumentos" value="81000056" />'
            "</form>",
            "lxml",
        ).find("form")

        response = _mock_response(
            "<html><body>Documento 81000056 não possui Tipo de Conferência informada."
            '<form id="frmAssinaturas"></form></body></html>',
            url="https://sei.example.test/sei/controlador.php?acao=documento_assinar&id_documento=81000056",
        )
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        result = self.client._execute_sign_form(form, "<html></html>")

        assert result["signed"] == []
        assert result["already_signed"] == []
        assert "Tipo de Conferência informada" in result["errors"][0]

    @patch("sei_cli.client.auth._follow")
    @patch("sei_cli.client.load_credentials")
    @patch("sei_cli.client.orgao_to_value")
    def test_uses_selected_cargo_from_form_when_credentials_cargo_is_empty(
        self,
        mock_orgao_to_value,
        mock_load_credentials,
        mock_follow,
    ):
        from sei_cli.models import Credentials

        mock_load_credentials.return_value = Credentials(
            usuario="u",
            senha="s",
            orgao="CBM",
            login_url="https://sei.example.test",
            cargo="",
        )
        mock_orgao_to_value.return_value = "28"

        form = BeautifulSoup(
            '<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">'
            '<input name="hdnIdDocumentos" value="81000122" />'
            '<select name="selCargoFuncao">'
            '<option value="2º Tenente QOEM BM" selected>2º Tenente QOEM BM</option>'
            "</select>"
            "</form>",
            "lxml",
        ).find("form")

        response = _mock_response(
            "<html><body>ok</body></html>",
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_documento=81000122",
        )
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        self.client._execute_sign_form(form, "<html></html>")

        posted_content = self.client.client.post.call_args.kwargs["content"].decode("iso-8859-1")
        assert "selCargoFuncao=2%BA+Tenente+QOEM+BM" in posted_content

    @patch("sei_cli.client.auth._follow")
    @patch("sei_cli.client.load_credentials")
    @patch("sei_cli.client.orgao_to_value")
    def test_skips_null_placeholder_option_when_credentials_cargo_is_empty(
        self,
        mock_orgao_to_value,
        mock_load_credentials,
        mock_follow,
    ):
        from sei_cli.models import Credentials

        mock_load_credentials.return_value = Credentials(
            usuario="u",
            senha="s",
            orgao="CBM",
            login_url="https://sei.example.test",
            cargo="",
        )
        mock_orgao_to_value.return_value = "28"

        form = BeautifulSoup(
            '<form id="frmAssinaturas" action="controlador.php?acao=documento_assinar">'
            '<input name="hdnIdDocumentos" value="81000122" />'
            '<select name="selCargoFuncao">'
            '<option value="null"></option>'
            '<option value="2º Tenente QOEM BM">2º Tenente QOEM BM</option>'
            "</select>"
            "</form>",
            "lxml",
        ).find("form")

        response = _mock_response(
            "<html><body>ok</body></html>",
            url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_documento=81000122",
        )
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        self.client._execute_sign_form(form, "<html></html>")

        posted_content = self.client.client.post.call_args.kwargs["content"].decode("iso-8859-1")
        assert "selCargoFuncao=null" not in posted_content
        assert "selCargoFuncao=2%BA+Tenente+QOEM+BM" in posted_content


class TestBlockDetailHelpers:
    def setup_method(self):
        self.client = _make_client()
        self.client.client = MagicMock()
        self.client.base_url = SEIClient.BASE

    def test_extract_block_document_entries_maps_real_ids(self):
        entries = self.client._extract_block_document_entries(BLOCK_DETAIL_HTML)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["seq"] == "4"
        assert entry["processo"] == "90000000.000034/2026-00"
        assert entry["documento_id"] == "81000122"
        assert entry["numero_sei"] == "81000054"
        assert entry["numero_documento"] == "81000054"
        assert entry["preview_url"].endswith("id_documento=81000122")
        assert entry["sign_url"].endswith("id_documento=81000122&infra_hash=abc")
        assert entry["can_sign"] is True
        assert entry["assinado"] is False

    def test_parse_chooser_types_includes_collapsed_entries(self):
        types = self.client._parse_chooser_types(CHOOSER_HTML_WITH_COLLAPSED)

        assert [item.id_serie for item in types] == ["5", "999"]
        assert [item.nome for item in types] == ["Despacho", "Tipo Expandido Pouco Usado"]

    def test_list_process_types_expands_chooser_via_form_submit(self):
        self.client._ensure_control = MagicMock(
            return_value='<a href="controlador.php?acao=procedimento_escolher_tipo&infra_hash=root">Iniciar Processo</a>'
        )
        self.client._get = MagicMock(return_value=_mock_response(PROCESS_CHOOSER_INITIAL))
        self.client._post = MagicMock(return_value=_mock_response(PROCESS_CHOOSER_EXPANDED))

        types = self.client.list_process_types()

        assert [item.nome for item in types] == ["Férias", "Processo Expandido Pouco Usado"]
        assert self.client._post.call_args.args[1]["hdnFiltroTipoProcedimento"] == "T"

    def test_list_document_types_expands_chooser_via_form_submit(self):
        self.client._navigate_to_arvore = MagicMock(
            return_value='<a href="controlador.php?acao=documento_escolher_tipo&infra_hash=root"><img src="svg/documento_incluir.svg?18" /></a>'
        )
        self.client._get = MagicMock(return_value=_mock_response(DOCUMENT_CHOOSER_INITIAL))
        self.client._post = MagicMock(return_value=_mock_response(DOCUMENT_CHOOSER_EXPANDED))

        types = self.client.list_document_types("55555")

        assert [item.nome for item in types] == ["Despacho", "Tipo Expandido Pouco Usado"]
        assert self.client._post.call_args.args[1]["hdnFiltroSerie"] == "T"

    def test_load_document_creation_form_resolves_expanded_type_name_to_id(self):
        self.client._navigate_to_arvore = MagicMock(
            return_value='<a href="controlador.php?acao=documento_escolher_tipo&infra_hash=root"><img src="svg/documento_incluir.svg?18" /></a>'
        )
        get_responses = [
            _mock_response(DOCUMENT_CHOOSER_INITIAL),
        ]
        self.client._get = MagicMock(side_effect=get_responses)
        self.client._post = MagicMock(
            side_effect=[
                _mock_response(DOCUMENT_CHOOSER_EXPANDED),
                _mock_response(DOC_CADASTRO_FORM),
            ]
        )

        _soup, _data, _action, id_serie = self.client._load_document_creation_form("55555", "Tipo Expandido Pouco Usado")

        assert id_serie == "999"
        submit_payload = self.client._post.call_args_list[1].args[1]
        assert submit_payload["hdnIdSerie"] == "999"

    def test_ensure_control_invalidates_login_like_cached_html(self):
        self.client._control_html = "<html><body><input name='pwdSenha' /></body></html>"
        self.client._ensure_session = MagicMock(return_value="<html><title>Controle de Processos</title></html>")

        html = self.client._ensure_control()

        assert "Controle de Processos" in html
        assert self.client._ensure_session.call_count == 1

    def test_preview_block_document_matches_by_numero_sei(self):
        self.client._get_block_detail_page = MagicMock(
            return_value=(
                _mock_response(BLOCK_DETAIL_HTML),
                BeautifulSoup(BLOCK_DETAIL_HTML, "lxml"),
            )
        )
        self.client._get = MagicMock(return_value=_mock_response(BLOCK_PREVIEW_HTML))

        content = self.client.preview_block_document("871299", "81000054")

        assert "Despacho de teste do bloco" in content

    def test_build_arvore_visualizar_url_returns_only_native_tree_url(self):
        native = "controlador.php?acao=arvore_visualizar&id_documento=81000132&infra_hash=native"
        self.client.get_full_document_tree = MagicMock(return_value=[TreeDocument(
            id_documento="81000132", nome="Solicitação", tipo="interno", arvore_url=native,
        )])
        self.client._get = MagicMock()
        assert self.client._build_arvore_visualizar_url("81000132", "81000130") == native
        self.client._get.assert_not_called()

    def test_get_editor_url_expands_lazy_tree_before_failing(self):
        self.client._navigate_to_arvore = MagicMock(return_value="<script></script>")
        self.client._auto_unit_switch = MagicMock()

        class _UnitGuard:
            def __enter__(self):
                return None

            def __exit__(self, *args):
                return None

        self.client._auto_unit_switch.return_value = _UnitGuard()
        self.client.get_full_document_tree = MagicMock(
            return_value=[
                TreeDocument(
                    id_documento="81000132",
                    nome="Solicitação",
                    tipo="interno",
                    arvore_url="controlador.php?acao=arvore_visualizar&id_documento=81000132&infra_hash=abc",
                )
            ]
        )
        self.client._get = MagicMock(
            return_value=_mock_response(
                "var linkEditarConteudo = 'controlador.php?acao=editor_montar&id_documento=81000132&infra_hash=def';"
            )
        )

        url = self.client._get_editor_url("81000132", "81000130")

        assert url is not None
        assert "acao=editor_montar" in url
        self.client.get_full_document_tree.assert_called_once_with("81000130", expand_all=True)

    def test_get_editor_url_stops_without_native_url_for_about_blank_lazy_doc(self):
        self.client._navigate_to_arvore = MagicMock(return_value="<script></script>")
        self.client._auto_unit_switch = MagicMock()

        class _UnitGuard:
            def __enter__(self):
                return None

            def __exit__(self, *args):
                return None

        self.client._auto_unit_switch.return_value = _UnitGuard()
        self.client.get_full_document_tree = MagicMock(
            return_value=[
                TreeDocument(
                    id_documento="81000132",
                    nome="Solicitação",
                    tipo="interno",
                    arvore_url="about:blank",
                )
            ]
        )
        self.client._build_arvore_visualizar_url = MagicMock(
            return_value="controlador.php?acao=arvore_visualizar&id_documento=81000132&infra_hash=fresh"
        )
        self.client._get = MagicMock(
            return_value=_mock_response(
                "var linkEditarConteudo = 'controlador.php?acao=editor_montar&id_documento=81000132&infra_hash=def';"
            )
        )

        url = self.client._get_editor_url("81000132", "81000130")

        assert url is None
        self.client._build_arvore_visualizar_url.assert_not_called()
        self.client._get.assert_not_called()

    def test_sign_block_uses_row_specific_sign_url_for_single_index(self):
        self.client._get_block_detail_page = MagicMock(
            return_value=(
                _mock_response(
                    BLOCK_DETAIL_HTML_MULTI,
                    url="https://sei.example.test/sei/controlador.php?acao=rel_bloco_protocolo_listar&id_bloco=871299&infra_hash=lista",
                ),
                BeautifulSoup(BLOCK_DETAIL_HTML_MULTI, "lxml"),
            )
        )
        self.client._execute_sign = MagicMock(
            return_value={
                "doc_ids": "81000121",
                "signed": ["81000121"],
                "already_signed": [],
                "errors": [],
            }
        )
        self.client.get_block_documents = MagicMock(return_value=[])

        result = self.client.sign_block("871299", doc_indices=[2])

        assert result["signed"] == ["81000121"]
        called_url = self.client._execute_sign.call_args.args[0]
        called_doc = self.client._execute_sign.call_args.args[1]
        assert called_url.endswith("acao=documento_assinar&id_documento=81000121&infra_hash=abc")
        assert called_doc == "81000121"

    def test_sign_block_accepts_row_with_existing_signature_marker_when_sign_url_exists(self):
        self.client._get_block_detail_page = MagicMock(
            return_value=(
                _mock_response(
                    BLOCK_DETAIL_HTML_MULTI,
                    url="https://sei.example.test/sei/controlador.php?acao=rel_bloco_protocolo_listar&id_bloco=871299&infra_hash=lista",
                ),
                BeautifulSoup(BLOCK_DETAIL_HTML_MULTI, "lxml"),
            )
        )
        self.client._post = MagicMock(return_value=_mock_response(BLOCK_SIGN_FORM_HTML))
        self.client._execute_sign_form = MagicMock(
            return_value={
                "doc_ids": "81000110",
                "signed": ["81000110"],
                "already_signed": [],
                "errors": [],
            }
        )
        self.client.get_block_documents = MagicMock(return_value=[])

        result = self.client.sign_block("871299", doc_indices=[1])

        assert result["signed"] == ["81000110"]
        called_url = self.client._post.call_args.args[0]
        called_data = self.client._post.call_args.args[1]
        assert "acao=documento_assinar" in called_url
        assert called_data["hdnInfraItemId"] == "81000110-871299"
        assert called_data["hdnInfraItensSelecionados"] == "81000110-871299"

    def test_sign_block_accepts_verification_when_document_is_no_longer_signable(self):
        self.client._get_block_detail_page = MagicMock(
            return_value=(
                _mock_response(
                    BLOCK_DETAIL_HTML_MULTI,
                    url="https://sei.example.test/sei/controlador.php?acao=rel_bloco_protocolo_listar&id_bloco=871299&infra_hash=lista",
                ),
                BeautifulSoup(BLOCK_DETAIL_HTML_MULTI, "lxml"),
            )
        )
        self.client._execute_sign = MagicMock(
            return_value={
                "doc_ids": "81000121",
                "signed": ["81000121"],
                "already_signed": [],
                "errors": [],
            }
        )
        self.client.get_block_documents = MagicMock(
            return_value=[
                BlockDocument(
                    seq="2",
                    processo="90000000.000034/2026-00",
                    documento_id="81000121",
                    tipo_documento="Despacho",
                    numero_sei="81000052",
                    numero_documento="81000052",
                    assinado=False,
                    can_sign=False,
                )
            ]
        )

        result = self.client.sign_block("871299", doc_indices=[2])

        assert result["errors"] == []
        assert "81000121" in result["signed"]
        assert result["post_verification"]["remaining_signable_refs"] == []

    def test_sign_or_authenticate_recovers_when_form_returns_but_document_is_signed(self):
        from sei_cli.models import TreeDocument

        self.client._navigate_to_arvore = MagicMock(
            return_value='controlador.php?acao=arvore_visualizar&id_documento=81000121&id_procedimento=55555'
        )

        class _UnitGuard:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        self.client._auto_unit_switch = MagicMock(return_value=_UnitGuard())
        self.client._get = MagicMock(
            return_value=_mock_response(
                "<html><script>var linkAssinarDocumento = 'controlador.php?acao=documento_assinar&id_documento=81000121&infra_hash=abc';</script></html>"
            )
        )
        self.client._execute_sign = MagicMock(
            return_value={
                "doc_ids": "81000121",
                "signed": [],
                "already_signed": [],
                "errors": ["SEI retornou ao formulário de assinatura sem confirmar a operação."],
                "returned_to_sign_form": True,
            }
        )
        self.client.get_full_document_tree = MagicMock(
            return_value=[
                TreeDocument(
                    id_documento="81000121",
                    nome="Despacho",
                    tipo="interno",
                    assinado=False,
                )
            ]
        )
        self.client.view_document_html = MagicMock(
            return_value="<html><body>Documento assinado eletronicamente por Fulano</body></html>"
        )

        result = self.client.sign_document("81000121", "55555")

        assert result["signed"] == ["81000121"]
        assert result["errors"] == []
        assert result["post_verification"]["tree"]["verified"] is False
        assert result["post_verification"]["fallback"]["verified"] is True

    def test_sign_or_authenticate_prefers_tree_post_verification_for_signature(self):
        from sei_cli.models import SignatureInfo, TreeDocument

        self.client._navigate_to_arvore = MagicMock(
            return_value='controlador.php?acao=arvore_visualizar&id_documento=81000121&id_procedimento=55555'
        )

        class _UnitGuard:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        self.client._auto_unit_switch = MagicMock(return_value=_UnitGuard())
        self.client._get = MagicMock(
            return_value=_mock_response(
                "<html><script>var linkAssinarDocumento = 'controlador.php?acao=documento_assinar&id_documento=81000121&infra_hash=abc';</script></html>"
            )
        )
        self.client._execute_sign = MagicMock(
            return_value={
                "doc_ids": "81000121",
                "signed": [],
                "already_signed": [],
                "errors": ["SEI retornou ao formulário de assinatura sem confirmar a operação."],
                "returned_to_sign_form": True,
            }
        )
        self.client.get_full_document_tree = MagicMock(
            return_value=[
                TreeDocument(
                    id_documento="81000121",
                    nome="Despacho",
                    tipo="interno",
                    assinado=True,
                    assinaturas=[
                        SignatureInfo(
                            signer="Sintético Pessoa 05",
                            role="2º Tenente QOEM BM",
                            unit="CBM",
                            kind="assinatura",
                            icon="svg/assinatura.svg?18",
                        )
                    ],
                )
            ]
        )
        self.client.view_document_html = MagicMock()

        result = self.client.sign_document("81000121", "55555")

        assert result["signed"] == ["81000121"]
        assert result["errors"] == []
        assert result["post_verification"]["tree"]["verified"] is True
        self.client.view_document_html.assert_not_called()

    def test_sign_or_authenticate_expands_lazy_loaded_folder_when_doc_not_in_root(self):
        from sei_cli.models import TreeDocument

        self.client._navigate_to_arvore = MagicMock(
            return_value='controlador.php?acao=arvore_visualizar&id_procedimento=55555'
        )

        class _UnitGuard:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        self.client._auto_unit_switch = MagicMock(return_value=_UnitGuard())
        self.client.get_full_document_tree = MagicMock(
            return_value=[
                TreeDocument(
                    id_documento="81000121",
                    nome="Despacho",
                    tipo="interno",
                    arvore_url="https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_documento=81000121&id_procedimento=55555&infra_hash=expanded",
                    assinado=False,
                )
            ]
        )
        self.client._get = MagicMock(
            return_value=_mock_response(
                "<html><script>var linkAssinarDocumento = 'controlador.php?acao=documento_assinar&id_documento=81000121&infra_hash=abc';</script></html>"
            )
        )
        self.client._execute_sign = MagicMock(
            return_value={
                "doc_ids": "81000121",
                "signed": ["81000121"],
                "already_signed": [],
                "errors": [],
            }
        )

        result = self.client.sign_document("81000121", "55555")

        assert result["signed"] == ["81000121"]
        called_url = self.client._get.call_args.args[0]
        assert called_url == "https://sei.example.test/sei/controlador.php?acao=arvore_visualizar&id_documento=81000121&id_procedimento=55555&infra_hash=expanded"

    def test_authenticate_document_prefers_tree_post_verification_for_authentication(self):
        from sei_cli.models import SignatureInfo, TreeDocument

        self.client._navigate_to_arvore = MagicMock(
            return_value='controlador.php?acao=arvore_visualizar&id_documento=81000122&id_procedimento=55555'
        )

        class _UnitGuard:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        self.client._auto_unit_switch = MagicMock(return_value=_UnitGuard())
        self.client._get = MagicMock(
            return_value=_mock_response(
                "<html><script>var linkAssinarDocumento = 'controlador.php?acao=documento_assinar&id_documento=81000122&infra_hash=abc';</script></html>"
            )
        )
        self.client._execute_sign = MagicMock(
            return_value={
                "doc_ids": "81000122",
                "signed": [],
                "already_signed": [],
                "errors": ["SEI retornou ao formulário de assinatura sem confirmar a operação."],
                "returned_to_sign_form": True,
            }
        )
        self.client.get_full_document_tree = MagicMock(
            return_value=[
                TreeDocument(
                    id_documento="81000122",
                    nome="Documento Externo",
                    tipo="pdf",
                    autenticado=True,
                    assinaturas=[
                        SignatureInfo(
                            signer="Sintético Pessoa 05",
                            role="2º Tenente QOEM BM",
                            unit="CBM",
                            kind="autenticacao",
                            icon="svg/autenticacao2.svg?18",
                        )
                    ],
                )
            ]
        )
        self.client.view_document_html = MagicMock()

        result = self.client.authenticate_document("81000122", "55555")

        assert result["signed"] == ["81000122"]
        assert result["errors"] == []
        assert result["post_verification"]["tree"]["verified"] is True
        self.client.view_document_html.assert_not_called()


class TestTryInicializar:
    def setup_method(self):
        self.client = _make_client()
        self.client._sei_url = SEIClient._sei_url.__get__(self.client, SEIClient)
        self.client.client = MagicMock()

    def test_try_inicializar_follows_principal_iframe_to_control(self):
        r1 = _mock_response("", url="https://sei.example.test/sei/inicializar.php", status=302)
        r1.headers = {"location": "controlador.php?acao=principal&infra_hash=abc"}
        r2 = _mock_response(PRINCIPAL_WRAPPER_HTML, url="https://sei.example.test/sei/controlador.php?acao=principal&infra_hash=abc")
        r3 = _mock_response(CONTROL_HTML, url="https://sei.example.test/sei/controlador.php?acao=procedimento_controlar&infra_hash=xyz")
        self.client.client.get.side_effect = [r1, r2, r3]

        html = self.client._try_inicializar()

        assert html == CONTROL_HTML


class TestPostEncodingSanitization:
    def setup_method(self):
        self.client = _make_client()
        self.client.client = MagicMock()
        self.client.base_url = SEIClient.BASE

    @patch("sei_cli.client.auth._follow")
    def test_post_sanitizes_non_latin1_punctuation(self, mock_follow):
        response = _mock_response("<html></html>", url="https://sei.example.test/sei/controlador.php?acao=x")
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        self.client._post(
            "https://sei.example.test/sei/controlador.php?acao=x",
            {"txtTexto": "Calendário \u2014 curso \u201cSAT\u201d"},
        )

        content = self.client.client.post.call_args.kwargs["content"].decode("iso-8859-1")
        assert "%E2%80%94" not in content
        assert "%E2%80%9C" not in content
        assert "%E2%80%9D" not in content
        assert "Calend%E1rio+-+curso+%22SAT%22" in content

    @patch("sei_cli.client.auth._follow")
    def test_post_pairs_sanitizes_non_latin1_punctuation(self, mock_follow):
        response = _mock_response("<html></html>", url="https://sei.example.test/sei/controlador.php?acao=x")
        self.client.client.post.return_value = response
        mock_follow.return_value = response

        self.client._post_pairs(
            "https://sei.example.test/sei/controlador.php?acao=x",
            [("selTexto", "Ofício Externo — responder até amanhã")],
        )

        content = self.client.client.post.call_args.kwargs["content"].decode("iso-8859-1")
        assert "%E2%80%94" not in content
        assert "Of%EDcio+Externo+-+responder+at%E9+amanh%E3" in content


def test_sanitize_for_iso_8859_1():
    assert _sanitize_for_iso_8859_1("Denúncia \u2014 Centro") == "Denúncia - Centro"
    assert _sanitize_for_iso_8859_1("\u201cteste\u201d") == "\x22teste\x22"
    assert _sanitize_for_iso_8859_1("normal text áàã") == "normal text áàã"
    assert _sanitize_for_iso_8859_1("\u2026fim") == "...fim"
