from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from sei_cli import auth
from sei_cli.client import SEIClient
from sei_cli.models import TreeDocument
from sei_cli.operations import reading
from sei_cli.operations.errors import DocumentAccessError, DocumentNotFoundError, DocumentURLUnavailableError, ParseError
from sei_cli.parsers import parse_expanded_folder

BASE = 'https://example.invalid/sei/'
VIEW = BASE + 'controlador.php?acao=documento_visualizar&id_documento=9'
PRINT = BASE + 'controlador.php?acao=documento_imprimir_web&id_documento=9'
NODE = 'Nos[1]=new infraArvoreNo("DOCUMENTO","9","8","about:blank","ifrConteudoVisualizacao","Synthetic (81000022)","Synthetic","svg/documento_interno.svg");'


def response(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request('GET', PRINT))


def bare(tree: str = NODE) -> SEIClient:
    client = object.__new__(SEIClient)
    client._sei_url = lambda path='': path if path.startswith('https://') else BASE + path
    client._navigate_to_arvore = MagicMock(return_value=tree)
    client._get = MagicMock(side_effect=AssertionError('Unexpected HTTP in offline test'))
    client.get_editor_sections = MagicMock(side_effect=AssertionError('Editor prohibited'))
    client.login = MagicMock(side_effect=AssertionError('Login prohibited'))
    client.switch_unit = MagicMock(side_effect=AssertionError('Unit switch prohibited'))
    return client


def document(**kwargs) -> TreeDocument:
    return TreeDocument(id_documento='9', nome='Synthetic', tipo=kwargs.pop('tipo', 'interno'), **kwargs)


def read(client, doc):
    return reading._read_tree_document_text(client, doc, id_documento='9', id_procedimento='8')


@pytest.mark.parametrize('quote', ["'", '"'])
@pytest.mark.parametrize('separator', ['\n', ' '])
def test_parse_both_quotes_and_same_line_with_signature(quote, separator):
    signature = 'NosAcoes[1]=new infraArvoreAcao("ASSINATURA","AS9","9","#",null,"Assinado por:\\nSynthetic Signer\\nSynthetic Role\\nSynthetic Unit","svg/assinatura.svg");'
    html = NODE + separator + f'Nos[1].src={quote}{VIEW}{quote};' + separator + signature
    docs = parse_expanded_folder(html, BASE)
    assert len(docs) == 1
    assert docs[0].src_url == VIEW
    assert docs[0].assinado is True
    assert docs[0].assinaturas[0].signer == 'Synthetic Signer'
    assert docs[0].url_evidence == {
        'node_present': True, 'src_assignment_present': True, 'src_literal_state': 'native',
        'tree_literal_state': 'about_blank', 'parsed_src_present': True, 'parsed_tree_present': False,
    }


@pytest.mark.parametrize('literal, state', [('', 'absent'), ('about:blank', 'about_blank'), (' ABOUT:BLANK ', 'about_blank')])
def test_blank_literals_become_absence_and_evidence_has_no_url(literal, state):
    doc = parse_expanded_folder(NODE + f" Nos[1].src='{literal}';", BASE)[0]
    assert doc.src_url is None and doc.arvore_url is None
    assert doc.url_evidence['src_literal_state'] == state
    serialized = json.dumps(doc.url_evidence)
    assert 'controlador.php' not in serialized and BASE not in serialized and 'about:blank' not in serialized


def test_missing_src_assignment_is_distinguished_from_explicit_empty():
    doc = parse_expanded_folder(NODE, BASE)[0]
    assert doc.url_evidence['src_assignment_present'] is False
    assert doc.url_evidence['src_literal_state'] == 'absent'


@pytest.mark.parametrize('error, code', [(auth.SessionAccessError('Synthetic'), 'session_inaccessible'), (httpx.ConnectError('Synthetic'), 'network_error')])
def test_terminal_errors_take_priority_over_missing_urls(error, code):
    assert reading._classify_document_read_error(document(), error)['code'] == code


@pytest.mark.parametrize('message, code', [('Documento privado', 'private_access'), ('Documento sigiloso', 'classified_access'), ('Acesso restrito', 'restricted_access'), ('Documento indisponível na unidade atual', 'document_unavailable_in_current_unit')])
def test_explicit_restrictions_take_priority_over_missing_urls(message, code):
    assert reading._classify_document_read_error(document(), RuntimeError(message))['code'] == code


def test_unknown_parser_failure_is_not_relabelled_as_missing_url():
    doc = document(url_evidence={'node_present': True, 'src_literal_state': 'absent'})
    result = reading._classify_document_read_error(doc, RuntimeError('Unexpected parser state'))
    assert result['code'] == 'parse_error'
    assert result['details']['url_evidence'] == doc.url_evidence
    assert reading._classify_document_read_error(doc, RuntimeError('Link de visualização não encontrado'))['code'] == 'document_url_unavailable'


def test_missing_source_is_neutral_even_with_pdf_tree_navigation_url():
    doc = document(tipo='pdf', arvore_url=BASE + 'native-tree-node')
    client = SimpleNamespace(download_document=MagicMock(side_effect=ValueError('Document has no download URL')),
                             get_full_document_tree=MagicMock(return_value=[doc]))
    with pytest.raises(DocumentURLUnavailableError):
        read(client, doc)
    assert client.download_document.call_count == 1
    assert client.get_full_document_tree.call_count == 1


def test_no_url_uses_one_initial_tree_and_one_refresh_without_editor_or_url_get():
    doc = document(src_url='about:blank', arvore_url='about:blank')
    client = bare()
    client._get_full_document_tree_context = MagicMock(return_value=([doc], NODE))
    client.read_document_content_details = MagicMock(side_effect=AssertionError('about:blank cannot be read'))
    with pytest.raises(DocumentURLUnavailableError):
        read(client, doc)
    client._navigate_to_arvore.assert_called_once_with('8')
    client._get_full_document_tree_context.assert_called_once_with('8', expand_all=True)
    client._get.assert_not_called()
    client.read_document_content_details.assert_not_called()
    client.get_editor_sections.assert_not_called()
    client.login.assert_not_called()
    client.switch_unit.assert_not_called()


def test_changed_native_source_recovers_with_only_one_refresh():
    old = document(src_url=VIEW)
    new = document(src_url=VIEW + '&current=1')
    client = bare()
    client.read_document_content_details = MagicMock(side_effect=[RuntimeError('Synthetic parse failure'), {'text': 'Recovered text'}])
    client._get_full_document_tree_context = MagicMock(return_value=([new], NODE))
    assert read(client, old)[0] == 'Recovered text'
    assert client.read_document_content_details.call_count == 2
    assert client._get_full_document_tree_context.call_count == 1
    assert client._navigate_to_arvore.call_count == 1


def test_unchanged_pdf_does_not_download_twice_after_details_failure():
    doc = document(tipo='pdf', src_url=VIEW)
    client = SimpleNamespace(read_document_content_details=MagicMock(side_effect=RuntimeError('Unexpected parser state')),
                             download_document=MagicMock(side_effect=AssertionError('Duplicate download prohibited')),
                             get_full_document_tree=MagicMock(return_value=[doc]))
    with pytest.raises(ParseError, match='Unexpected parser state'):
        read(client, doc)
    assert client.read_document_content_details.call_count == 1
    client.download_document.assert_not_called()
    assert client.get_full_document_tree.call_count == 1


def test_unchanged_print_link_is_not_fetched_twice():
    doc = document()
    tree = NODE + f" var printLink='{PRINT}'; trailing();"
    client = bare(tree)
    client._get = MagicMock(side_effect=RuntimeError('Unexpected parser state'))
    client._get_full_document_tree_context = MagicMock(return_value=([doc], tree))
    with pytest.raises(ParseError, match='Unexpected parser state'):
        read(client, doc)
    assert client._get.call_count == 1
    assert client._navigate_to_arvore.call_count == 1
    assert client._get_full_document_tree_context.call_count == 1


def test_native_print_appearing_in_refreshed_context_is_preserved():
    doc = document()
    client = bare()
    client._get = MagicMock(return_value=response('<p>Fresh native print</p>'))
    client._get_full_document_tree_context = MagicMock(return_value=([doc], NODE + f" var printLink='{PRINT}';"))
    assert read(client, doc)[0] == 'Fresh native print'
    client._get.assert_called_once_with(PRINT)
    assert client._navigate_to_arvore.call_count == 1
    assert client._get_full_document_tree_context.call_count == 1


def test_second_attempt_legacy_text_expiry_is_terminal():
    old, new = document(src_url=VIEW), document(src_url=VIEW + '&current=1')
    client = SimpleNamespace(read_document_content_details=MagicMock(side_effect=[RuntimeError('Synthetic parse failure'), RuntimeError('Session expired viewing document. Re-login needed.')]),
                             read_document=MagicMock(side_effect=RuntimeError('Link de visualização não encontrado')),
                             get_full_document_tree=MagicMock(return_value=[new]))
    with pytest.raises(auth.SessionAccessError):
        read(client, old)
    assert client.get_full_document_tree.call_count == 1


def test_unchanged_context_legacy_print_expiry_is_terminal():
    doc = document()
    client = SimpleNamespace(read_document_from_tree=MagicMock(side_effect=[RuntimeError('Link de visualização não encontrado'), RuntimeError('Session expired viewing document. Re-login needed.')]),
                             _get_full_document_tree_context=MagicMock(return_value=([doc], NODE)))
    with pytest.raises(auth.SessionAccessError):
        read(client, doc)
    assert client._get_full_document_tree_context.call_count == 1


def test_stronger_explicit_restriction_after_refresh_is_preserved():
    doc = document()
    client = SimpleNamespace(read_document_from_tree=MagicMock(side_effect=[RuntimeError('Link de visualização não encontrado'), RuntimeError('Documento privado: acesso restrito')]),
                             _get_full_document_tree_context=MagicMock(return_value=([doc], NODE)))
    with pytest.raises(DocumentAccessError) as result:
        read(client, doc)
    assert result.value.code == 'private_access'


def test_exact_document_id_and_single_quote_url_boundaries():
    wrong = PRINT.replace('id_documento=9', 'id_documento=99')
    tree = f"var wrong='{wrong}'; var right='{PRINT}'; trailing();"
    client = bare(tree)
    client._get = MagicMock(return_value=response('<p>Correct document</p>'))
    assert client.read_document_from_tree(document(), '8') == 'Correct document'
    client._get.assert_called_once_with(PRINT)


def test_native_view_login_html_is_typed_terminal():
    client = bare(f"var printLink='{PRINT}';")
    client._get = MagicMock(return_value=response('<form><input name="pwdSenha"></form>'))
    with pytest.raises(auth.SessionAccessError):
        client.read_document_from_tree(document(), '8')
    assert client._get.call_count == 1


def test_missing_metadata_in_canonical_client_never_opens_editor():
    client = bare()
    client._get_full_document_tree_context = MagicMock(return_value=([], NODE))
    with pytest.raises(DocumentNotFoundError):
        read(client, None)
    client.get_editor_sections.assert_not_called()
    client._get.assert_not_called()


def test_lazy_folder_preserves_signature_and_print_context(monkeypatch):
    import sei_cli.client as module
    client = bare('synthetic root')
    folder = SimpleNamespace(carregado=False, link='native-folder', folder_id='PASTA1', protocolos='9')
    monkeypatch.setattr(module, 'parse_tree_folders', lambda content: [folder])
    expanded = 'OK\n' + NODE + f" Nos[1].src='{VIEW}';" + ' NosAcoes[1]=new infraArvoreAcao("ASSINATURA","AS9","9","#",null,"Assinado por:\\nSynthetic Signer\\nSynthetic Role\\nSynthetic Unit","svg/assinatura.svg");' + f" var printLink='{PRINT}';"
    client._post = MagicMock(return_value=response(expanded))
    docs, context = client._get_full_document_tree_context('8')
    assert len(docs) == 1 and docs[0].assinado is True
    assert docs[0].src_url == VIEW and PRINT in context
    client._get = MagicMock(return_value=response('<p>Lazy print</p>'))
    assert client.read_document_from_tree(docs[0], '8', tree_html=context) == 'Lazy print'
    assert client._navigate_to_arvore.call_count == 1
    client._post.assert_called_once()
    client._get.assert_called_once_with(PRINT)


def test_current_unit_context_word_is_not_a_permission_restriction():
    doc = document()
    assert reading._classify_document_read_error(doc, RuntimeError('Documento sem URL na unidade atual'))['code'] == 'document_url_unavailable'
    assert reading._classify_document_read_error(doc, RuntimeError('Unexpected parser state na unidade atual'))['code'] == 'parse_error'
    assert reading._classify_document_read_error(doc, DocumentURLUnavailableError('Sem URL na unidade atual'))['code'] == 'document_url_unavailable'


def test_new_print_parser_failure_does_not_get_hidden_by_previous_missing_link():
    doc = document()
    client = bare()
    client._get = MagicMock(side_effect=RuntimeError('Unexpected parser state'))
    client._get_full_document_tree_context = MagicMock(return_value=([doc], NODE + f" var printLink='{PRINT}';"))
    with pytest.raises(ParseError, match='Unexpected parser state') as result:
        read(client, doc)
    assert result.value.details['src_url_present'] is False
    assert client._get.call_count == 1
    assert client._get_full_document_tree_context.call_count == 1
