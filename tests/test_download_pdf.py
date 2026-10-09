"""Tests for download_pdf and download_document_pdf methods.

Uses HTML fixtures and mocked HTTP responses — no network calls.
"""
from __future__ import annotations

import io
import re
from unittest.mock import MagicMock, patch

import pytest

from sei_cli.client import SEIClient

# --- Fixture HTML snippets ---

# URL for process (no id_documento)
ARVORE_WITH_PDF_URL = """
<html><body>
<script>
var Nos = [];
Nos[0] = {
  'acoes': [
    '<a href="controlador.php?acao=procedimento_gerar_pdf&amp;acao_origem=arvore_visualizar&amp;id_procedimento=12345&amp;arvore=1&amp;infra_sistema=100000100&amp;infra_unidade_atual=910000015&amp;infra_hash=abcdef">Gerar PDF do Processo</a>'
  ]
};
</script>
</body></html>
"""

# URL for document (has id_documento)
ARVORE_WITH_DOC_URL = """
<html><body>
<script>
var Nos = [];
Nos[1] = {
  'acoes': [
    '<a href="controlador.php?acao=procedimento_gerar_pdf&amp;id_documento=99999&amp;id_procedimento=12345&amp;infra_hash=def456">Gerar PDF do Documento</a>'
  ]
};
</script>
</body></html>
"""

ARVORE_WITH_PROCESS_AND_OTHER_DOC_URL = """
<html><body>
<script>
var Nos = [];
Nos[0] = {
  'acoes': [
    '<a href="controlador.php?acao=procedimento_gerar_pdf&amp;acao_origem=arvore_visualizar&amp;id_procedimento=12345&amp;arvore=1&amp;infra_sistema=100000100&amp;infra_unidade_atual=910000015&amp;infra_hash=abcdef">Gerar PDF do Processo</a>'
  ]
};
Nos[1] = {
  'acoes': [
    '<a href="controlador.php?acao=procedimento_gerar_pdf&amp;id_documento=11111&amp;id_procedimento=12345&amp;infra_hash=def456">Gerar PDF do Documento</a>'
  ]
};
</script>
</body></html>
"""

ARVORE_WITHOUT_PDF_URL = """
<html><body>
<script>
var Nos = [];
Nos[0] = { 'acoes': [] };
</script>
</body></html>
"""

CONFIRM_FORM_HTML = """
<html><body>
<form action="controlador.php?acao=procedimento_gerar_pdf_executar&amp;infra_hash=abc123">
  <input type="hidden" name="infra_hash" value="abc123"/>
  <input type="submit" name="sbmGerarPDF" value="Gerar"/>
</form>
</body></html>
"""

IFRAME_RESPONSE_HTML = """
<html><body>
<script>
document.getElementById('ifrVisualizacao').src = 'controlador.php?acao=exibir_arquivo&nome_arquivo=temp123.pdf \t \n ';
</script>
</body></html>
"""

PDF_BYTES = b"%PDF-1.4 test content"

class FakeResponse:
    """Minimal mock for httpx.Response."""
    def __init__(
        self,
        text: str = "",
        content: bytes = b"",
        status_code: int = 200,
        content_type: str = "text/html; charset=utf-8",
        url: str = "https://sei.example.test/sei/controlador.php",
    ) -> None:
        self.text = text
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.url = url

def make_client() -> SEIClient:
    """Create a SEIClient with mocked internals."""
    client = SEIClient.__new__(SEIClient)
    client.base_url = "https://sei.example.test"
    client._control_html = None
    client._menu_links = {}
    client._editor_hiddens = {}
    client._current_unit_id = None
    client._last_login = 0.0
    client._batch_active = False
    client._hash_pool = {}
    return client


# --- Tests ---

def test_download_pdf_success(tmp_path) -> None:
    """Happy path: 7-step process PDF download."""
    client = make_client()
    output_path = str(tmp_path / "test.pdf")

    get_responses = [
        FakeResponse(text=CONFIRM_FORM_HTML),  # call 1: page with form
        FakeResponse(content=PDF_BYTES, content_type="application/pdf"),  # call 2: actual PDF
    ]
    def side_effect_get(url):
        return get_responses.pop(0)

    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PDF_URL), \
         patch.object(client, "_get", side_effect=side_effect_get) as mock_get, \
         patch.object(client, "_post", return_value=FakeResponse(text=IFRAME_RESPONSE_HTML)) as mock_post:

        result = client.download_pdf("12345", output_path=output_path)

    assert result == output_path
    import os
    assert os.path.exists(output_path)
    with open(output_path, "rb") as f:
        assert f.read() == PDF_BYTES

    # Verify calls
    assert mock_get.call_count == 2
    assert "procedimento_gerar_pdf" in mock_get.call_args_list[0][0][0]
    assert "exibir_arquivo" in mock_get.call_args_list[1][0][0]

    assert mock_post.call_count == 1
    post_args = mock_post.call_args[0]
    assert "procedimento_gerar_pdf_executar" in post_args[0]
    assert post_args[1] == {'hdnInfraTipoPagina': '2', 'hdnFlagGerar': '1', 'rdoTipo': 'T'}


def test_download_pdf_default_output_path(tmp_path) -> None:
    client = make_client()
    get_responses = [
        FakeResponse(text=CONFIRM_FORM_HTML),
        FakeResponse(content=PDF_BYTES, content_type="application/pdf"),
    ]
    def side_effect_get(url):
        return get_responses.pop(0)

    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PDF_URL), \
         patch.object(client, "_get", side_effect=side_effect_get), \
         patch.object(client, "_post", return_value=FakeResponse(text=IFRAME_RESPONSE_HTML)), \
         patch("builtins.open", MagicMock()):
        result = client.download_pdf("12345")

    assert result == "/tmp/sei_12345.pdf"


def test_download_pdf_raises_when_arvore_not_found() -> None:
    client = make_client()
    with patch.object(client, "_navigate_to_arvore", return_value=None):
        with pytest.raises(RuntimeError, match="não encontrado"):
            client.download_pdf("99999")


def test_download_pdf_raises_when_url_missing() -> None:
    client = make_client()
    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITHOUT_PDF_URL):
        with pytest.raises(RuntimeError, match="Link 'Gerar PDF' não encontrado"):
            client.download_pdf("12345")


def test_download_pdf_raises_on_login_redirect() -> None:
    client = make_client()
    login_response = FakeResponse(
        text="<html><input name='pwdSenha'/></html>",
        url="https://sei.example.test/sip/login.php",
    )
    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PDF_URL), \
         patch.object(client, "_get", return_value=login_response):
        with pytest.raises(RuntimeError, match="[Ss]ess"):
            client.download_pdf("12345")


def test_download_pdf_raises_when_no_form_on_confirm_page() -> None:
    client = make_client()
    no_form_html = "<html><body><p>Nenhum formulário aqui.</p></body></html>"
    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PDF_URL), \
         patch.object(client, "_get", return_value=FakeResponse(text=no_form_html)):
        with pytest.raises(RuntimeError, match="[Ff]ormulário"):
            client.download_pdf("12345")


def test_download_pdf_raises_when_iframe_missing() -> None:
    client = make_client()
    get_responses = [FakeResponse(text=CONFIRM_FORM_HTML)]
    def side_effect_get(url):
        return get_responses.pop(0)

    no_iframe_html = "<html><body><script>console.log('nada');</script></body></html>"
    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PDF_URL), \
         patch.object(client, "_get", side_effect=side_effect_get), \
         patch.object(client, "_post", return_value=FakeResponse(text=no_iframe_html)):
        with pytest.raises(RuntimeError, match="URL do PDF não encontrada"):
            client.download_pdf("12345")


def test_download_document_pdf_success(tmp_path) -> None:
    """Happy path for single document PDF."""
    client = make_client()
    output_path = str(tmp_path / "doc.pdf")

    get_responses = [
        FakeResponse(text=CONFIRM_FORM_HTML),
        FakeResponse(content=PDF_BYTES, content_type="application/pdf"),
    ]
    def side_effect_get(url):
        return get_responses.pop(0)

    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_DOC_URL), \
         patch.object(client, "_get", side_effect=side_effect_get) as mock_get, \
         patch.object(client, "_post", return_value=FakeResponse(text=IFRAME_RESPONSE_HTML)):

        result = client.download_document_pdf("99999", "12345", output_path=output_path)

    assert result == output_path
    import os
    assert os.path.exists(output_path)
    
    assert mock_get.call_count == 2
    # Verify we picked the URL containing id_documento=99999
    assert "id_documento=99999" in mock_get.call_args_list[0][0][0]


def test_download_document_pdf_falls_back_to_apenas_mode_when_direct_link_is_missing(tmp_path) -> None:
    client = make_client()
    output_path = str(tmp_path / "doc-fallback.pdf")

    get_responses = [
        FakeResponse(text=CONFIRM_FORM_HTML),
        FakeResponse(content=PDF_BYTES, content_type="application/pdf"),
    ]

    def side_effect_get(url):
        return get_responses.pop(0)

    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PROCESS_AND_OTHER_DOC_URL), \
         patch.object(client, "_get", side_effect=side_effect_get) as mock_get, \
         patch.object(client, "_post", return_value=FakeResponse(text=IFRAME_RESPONSE_HTML)) as mock_post:

        result = client.download_document_pdf("99999", "12345", output_path=output_path)

    assert result == output_path
    assert "id_procedimento=12345" in mock_get.call_args_list[0][0][0]
    assert "id_documento=" not in mock_get.call_args_list[0][0][0]
    post_args = mock_post.call_args[0]
    assert post_args[1]["rdoTipo"] == "A"
    assert post_args[1]["hdnDocumentosApenas"] == "99999"


def test_download_document_pdf_raises_when_url_missing() -> None:
    client = make_client()
    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITHOUT_PDF_URL):
        with pytest.raises(RuntimeError, match="Link 'Gerar PDF' não encontrado"):
            client.download_document_pdf("99999", "12345")


def test_download_pdf_iframe_url_cleaned(tmp_path) -> None:
    """Verify whitespace and non-printable chars are removed from iframe URL."""
    client = make_client()
    get_responses = [
        FakeResponse(text=CONFIRM_FORM_HTML),
        FakeResponse(content=PDF_BYTES),
    ]
    def side_effect_get(url):
        return get_responses.pop(0)

    with patch.object(client, "_navigate_to_arvore", return_value=ARVORE_WITH_PDF_URL), \
         patch.object(client, "_get", side_effect=side_effect_get) as mock_get, \
         patch.object(client, "_post", return_value=FakeResponse(text=IFRAME_RESPONSE_HTML)):

        client.download_pdf("12345", output_path=str(tmp_path/"test.pdf"))

    # The IFRAME_RESPONSE_HTML has ' ...temp123.pdf \t \n '
    pdf_url = mock_get.call_args_list[1][0][0]
    assert "temp123.pdf" in pdf_url
    assert "\t" not in pdf_url
    assert "\n" not in pdf_url
    assert " " not in pdf_url
