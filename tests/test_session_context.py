"""Offline contracts for preserving sessions and using native current-context URLs."""
from unittest.mock import MagicMock

import httpx
import pytest
from bs4 import BeautifulSoup

from sei_cli import auth, client as client_module
from sei_cli.client import SEIClient
from sei_cli.models import TreeDocument, Unit
from sei_cli.operations import reading
from sei_cli.operations.errors import error_from_exception

BASE = 'https://sei.example.test/sei/'


def control(unit='101'):
    return (
        '<title>Controle de Processos</title><form id="frmProcedimentoControlar">'
        f'<a id="lnkInfraUnidade" title="Synthetic unit {unit}" '
        f'onclick="window.location.href=\'controlador.php?acao=infra_trocar_unidade'
        f'&amp;infra_unidade_atual={unit}\';">UNIT {unit}</a>'
        '<a id="lnkUsuarioSistema" title="SYNTHETIC USER"></a>'
        '<table id="tblProcessosRecebidos"></table><table id="tblProcessosGerados"></table></form>'
    )


def bare(handler=None):
    client = SEIClient.__new__(SEIClient)
    client.base_url = 'https://sei.example.test'
    client._control_html = None
    client._menu_links = {}
    client._editor_hiddens = {}
    client._hash_pool = {}
    client._current_unit_id = '101'
    client._batch_active = False
    client._session_error = None
    if handler:
        client.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        client.client.cookies.set('PHPSESSID', 'synthetic-original', domain='sei.rn.gov.br', path='/')
    return client


@pytest.mark.parametrize('variant', ['direct_200', 'extra_redirect'])
def test_initializer_accepts_native_control_variants_without_login(variant):
    seen = []
    def handle(req):
        seen.append(req)
        if req.url.path.endswith('inicializar.php'):
            if variant == 'direct_200':
                return httpx.Response(200, text=control())
            return httpx.Response(302, headers={'Location': 'controlador.php?acao=principal&infra_hash=synthetic-native'})
        if req.url.params.get('acao') == 'principal':
            return httpx.Response(302, headers={'Location': 'controlador.php?acao=procedimento_controlar&infra_hash=synthetic-returned'})
        return httpx.Response(200, text=control())
    client = bare(handle)
    with client:
        assert client.status().valid
        assert client.status().valid
        assert client.list_processes().recebidos == []
    assert len(seen) == (1 if variant == 'direct_200' else 3)
    assert all(req.method == 'GET' and '/sip/' not in req.url.path for req in seen)


def test_inconclusive_probe_preserves_cookie_and_stops_repeated_probes():
    client = bare(lambda req: httpx.Response(200, text='<p>Unexpected wrapper</p>'))
    client._try_inicializar = MagicMock(return_value=None)
    with client:
        for _ in range(2):
            with pytest.raises(auth.SessionAccessError, match='inconclusiva'):
                client.status()
        assert client.client.cookies.get('PHPSESSID') == 'synthetic-original'
    client._try_inicializar.assert_called_once()


def test_absent_cookie_fails_without_initializer_or_authentication():
    client = bare(lambda req: httpx.Response(200, text=control()))
    client.client.cookies.clear()  # Synthetic fixture only.
    client._try_inicializar = MagicMock()
    with client, pytest.raises(auth.SessionAccessError, match='ausente'):
        client.status()
    client._try_inicializar.assert_not_called()


def test_cached_control_does_not_probe_but_explicit_refresh_does():
    client = bare(lambda req: httpx.Response(200, text=control()))
    client._control_html = control()
    client._try_inicializar = MagicMock(return_value=control('102'))
    with client:
        assert client.status().unidade_sigla == 'UNIT 101'
        client._try_inicializar.assert_not_called()
        client._fresh_control()
        assert client.status().unidade_sigla == 'UNIT 102'
        assert client._current_unit_id == '102'
    client._try_inicializar.assert_called_once()


def test_transport_error_keeps_cookie_and_network_classification():
    def handle(req):
        raise httpx.ConnectError('Synthetic transport failure', request=req)
    client = bare(handle)
    with client:
        with pytest.raises(httpx.ConnectError) as caught:
            client.status()
        assert client.client.cookies.get('PHPSESSID') == 'synthetic-original'
    assert error_from_exception(caught.value).code == 'network_error'


def test_incidental_login_reference_does_not_invalidate_control():
    assert bare()._is_valid_control_html(control() + '<p>Help mentions login.php</p>')


def test_read_redirect_stops_before_login_request_and_preserves_cookie():
    seen = []
    def handle(req):
        seen.append(req.url.path)
        return httpx.Response(302, headers={'Location': '/sip/login.php'})
    client = bare(handle)
    with client:
        with pytest.raises(auth.SessionAccessError):
            client._get(BASE + 'controlador.php?acao=documento_visualizar&infra_hash=synthetic')
        assert client.client.cookies.get('PHPSESSID') == 'synthetic-original'
        with pytest.raises(auth.SessionAccessError):
            client.status()
    assert seen == ['/sei/controlador.php']


def test_tree_uses_exact_native_process_and_iframe_links_without_probe():
    client = bare()
    client._ensure_control = MagicMock(return_value=control() + (
        '<a href="controlador.php?acao=procedimento_trabalhar&amp;id_procedimento=1234&amp;infra_hash=wrong">wrong</a>'
        '<a href="controlador.php?acao=procedimento_trabalhar&amp;id_procedimento=123&amp;infra_hash=native">right</a>'
    ))
    client._get = MagicMock(side_effect=[
        MagicMock(text='<iframe name="ifrArvore" src="controlador.php?acao=procedimento_visualizar&amp;infra_hash=treeNative"></iframe>'),
        MagicMock(text='synthetic usable tree'),
    ])
    client._tree_access_quality = MagicMock(return_value=51)
    client.search = MagicMock()
    client._ensure_session = MagicMock(side_effect=AssertionError('Unnecessary probe'))
    assert client._navigate_to_arvore('123') == 'synthetic usable tree'
    urls = [call.args[0] for call in client._get.call_args_list]
    assert urls == [BASE + 'controlador.php?acao=procedimento_trabalhar&id_procedimento=123&infra_hash=native',
                    BASE + 'controlador.php?acao=procedimento_visualizar&infra_hash=treeNative']
    client.search.assert_not_called()
    client._ensure_session.assert_not_called()


def test_process_absent_from_control_uses_search_once_without_manual_url():
    client = bare()
    client._ensure_control = MagicMock(return_value=control())
    client.search = MagicMock(return_value='<iframe name="ifrArvore" src="native-tree"></iframe>')
    client._get = MagicMock(return_value=MagicMock(text='usable tree'))
    client._tree_access_quality = MagicMock(return_value=51)
    assert client._navigate_to_arvore('999') == 'usable tree'
    client.search.assert_called_once_with('999')
    client._get.assert_called_once_with(BASE + 'native-tree')


def test_session_failure_in_tree_does_not_try_search_fallback():
    client = bare()
    client._navigate_to_process_page = MagicMock(side_effect=auth.SessionAccessError('Synthetic access failure'))
    client.search = MagicMock()
    with pytest.raises(auth.SessionAccessError):
        client._navigate_to_arvore('999')
    client.search.assert_not_called()


def test_folder_expansion_stops_on_terminal_session_error(monkeypatch):
    client = bare()
    client._navigate_to_arvore = MagicMock(return_value='synthetic tree')
    first = MagicMock(carregado=False, link='native-folder', folder_id='PASTA1', protocolos='9')
    second = MagicMock(carregado=False, link='native-folder-2', folder_id='PASTA2', protocolos='10')
    monkeypatch.setattr(client_module, 'parse_tree_folders', lambda html: [first, second])
    monkeypatch.setattr(client_module, 'parse_tree_signatures', lambda html: {})
    monkeypatch.setattr(client_module, 'parse_expanded_folder', lambda *args: [])
    client._post = MagicMock(side_effect=auth.SessionAccessError('Synthetic access failure'))
    with pytest.raises(auth.SessionAccessError):
        client.get_full_document_tree('999')
    client._post.assert_called_once()


def test_native_document_url_absence_never_copies_other_action_token():
    client = bare()
    client.get_full_document_tree = MagicMock(return_value=[TreeDocument(id_documento='9', nome='Synthetic', tipo='interno', arvore_url='about:blank')])
    client._get = MagicMock()
    assert client._build_arvore_visualizar_url('9', '999') is None
    client._get.assert_not_called()


def test_document_access_failure_never_retries_old_url(monkeypatch):
    doc = TreeDocument(id_documento='9', nome='Synthetic', tipo='interno', src_url=BASE + 'native-document')
    client = MagicMock()
    client.read_document_content_details.side_effect = auth.SessionAccessError('Synthetic access failure')
    refresh = MagicMock()
    monkeypatch.setattr(reading, '_refresh_tree_document', refresh)
    with pytest.raises(auth.SessionAccessError):
        reading._read_tree_document_text(client, doc, id_documento='9', id_procedimento='999')
    client.read_document_content_details.assert_called_once_with(doc)
    client._ensure_session.assert_not_called()
    refresh.assert_not_called()


def test_wrong_unit_error_is_not_retried_as_session_error():
    client = bare()
    client._ensure_session = MagicMock()
    operation = MagicMock(side_effect=RuntimeError('Processo não encontrado na unidade ativa'))
    with pytest.raises(RuntimeError):
        client._navigate_with_retry(operation)
    operation.assert_called_once()
    client._ensure_session.assert_not_called()


@pytest.mark.parametrize('returned_unit', ['102', '101'])
def test_unit_selection_reuses_only_verified_native_return(monkeypatch, returned_unit):
    client = bare()
    client._control_html = control()
    client._fresh_control = MagicMock(return_value=control())
    chooser = MagicMock(text='synthetic chooser', url=BASE + 'controlador.php?acao=infra_trocar_unidade&infra_unidade_atual=101')
    client._get = MagicMock(return_value=chooser)
    client._post = MagicMock(return_value=MagicMock(text=control(returned_unit)))
    client._persist_session = MagicMock()
    monkeypatch.setattr(client_module, 'parse_units_switch_page', lambda *args: [Unit(sigla='UNIT 102', descricao='Synthetic 102', link='102')])
    monkeypatch.setattr(client_module, 'parse_unit_switch_form', lambda *args: ('native-switch', {}))
    if returned_unit == '102':
        assert client.switch_unit('102').unidade_sigla == 'UNIT 102'
        client._get.assert_called_once()  # Chooser only, no manual control GET.
        assert client._fresh_control.call_count == 1
        client._persist_session.assert_called_once()
    else:
        with pytest.raises(auth.SessionAccessError):
            client.switch_unit('102')
        client._persist_session.assert_not_called()


@pytest.mark.parametrize('error', [auth.SessionAccessError('Synthetic session failure'), httpx.ConnectError('Synthetic transport failure')])
def test_support_helpers_propagate_terminal_failure_without_retry(error):
    client = MagicMock()
    client.get_actions.side_effect = error
    with pytest.raises(type(error)):
        reading._safe_get_actions(client, '999')
    client.get_actions.assert_called_once()
    client.status.side_effect = error
    with pytest.raises(type(error)):
        reading._context_best_effort(client, {'valid': True})
    client.status.assert_called_once()
    doc = TreeDocument(id_documento='9', nome='Synthetic', tipo='interno', src_url='about:blank')
    result = reading._classify_document_read_error(doc, error)
    assert result['code'] == ('session_inaccessible' if isinstance(error, auth.SessionAccessError) else 'network_error')


@pytest.mark.parametrize('error', [auth.SessionAccessError('Synthetic session failure'), httpx.ConnectError('Synthetic transport failure')])
def test_process_failure_preserves_read_evidence_and_skips_remaining(monkeypatch, error):
    from test_operations import FakeClient
    client = FakeClient()
    original = reading._document_read_core
    calls = []
    def read(c, **kwargs):
        calls.append(kwargs['id_documento'])
        if len(calls) == 3:
            raise error
        return original(c, **kwargs)
    monkeypatch.setattr(reading, '_document_read_core', read)
    result = reading.process_read(client, '81000097', mode='all')
    assert result['ok'] is False
    assert result['context']['valid'] is False
    assert result['data']['read_interrupted'] is True
    assert len(result['data']['documents_read']) == 2
    assert len(result['data']['documents_unread']) == 6
    assert len(calls) == 3
    assert client._session_error


def test_unit_guard_does_not_restore_after_terminal_result():
    client = bare()
    client.status = MagicMock(return_value=MagicMock(unidade_sigla='ORIGINAL'))
    client._can_switch_to = MagicMock(return_value=True)
    client.switch_unit = MagicMock()
    with client._auto_unit_switch('synthetic tree', target_unit='TARGET'):
        client._session_error = 'Synthetic interrupted read; restoration pending'
    client.switch_unit.assert_called_once_with('TARGET')


def test_latched_failure_blocks_direct_post_without_sending():
    seen = []
    client = bare(lambda req: (seen.append(req) or httpx.Response(200, text=control())))
    client._session_error = 'Synthetic latched failure'
    with client, pytest.raises(auth.SessionAccessError):
        client._post(BASE + 'native-search', {'txtPesquisaRapida': '999'})
    assert seen == []


def test_empty_selection_response_reconciles_via_native_initializer(monkeypatch):
    client = bare()
    client._control_html = control()
    client._fresh_control = MagicMock(side_effect=[control(), control('102')])
    chooser = MagicMock(text='synthetic chooser', url=BASE + 'controlador.php?acao=infra_trocar_unidade&infra_unidade_atual=101')
    client._get = MagicMock(return_value=chooser)
    client._post = MagicMock(return_value=MagicMock(text=''))
    client._persist_session = MagicMock()
    monkeypatch.setattr(client_module, 'parse_units_switch_page', lambda *args: [Unit(sigla='UNIT 102', descricao='Synthetic 102', link='102')])
    monkeypatch.setattr(client_module, 'parse_unit_switch_form', lambda *args: ('native-switch', {}))
    assert client.switch_unit('102').unidade_sigla == 'UNIT 102'
    assert client._fresh_control.call_count == 2
    client._get.assert_called_once()  # Chooser only; refresh uses the initializer.
    client._persist_session.assert_called_once()


def test_legacy_cli_checks_existing_session_without_automatic_login(monkeypatch):
    from click.testing import CliRunner
    from sei_cli import cli as cli_module
    client = MagicMock()
    client.__enter__.return_value = client
    client.add_document_to_block.return_value = {'ok': True}
    monkeypatch.setattr(cli_module, 'SEIClient', lambda: client)
    result = CliRunner().invoke(cli_module.cli, ['block-add', '999', '9', '1', '--json'])
    assert result.exit_code == 0
    client.status.assert_called_once()
    client.login.assert_not_called()
    client.add_document_to_block.assert_called_once()


def test_explicit_login_command_still_uses_explicit_login(monkeypatch):
    from click.testing import CliRunner
    from sei_cli import cli as cli_module
    from sei_cli.models import SystemStatus
    client = MagicMock()
    client.__enter__.return_value = client
    client.login.return_value = SystemStatus(valid=True, unidade_sigla='UNIT 101')
    monkeypatch.setattr(cli_module, 'SEIClient', lambda: client)
    result = CliRunner().invoke(cli_module.cli, ['login', '--json'])
    assert result.exit_code == 0
    client.login.assert_called_once()
    client.status.assert_not_called()


def test_cached_control_works_with_existing_session_only_guard():
    client = bare()
    client._control_html = control()
    client._ensure_session = MagicMock(side_effect=AssertionError('Unnecessary guarded probe'))
    assert client.status().valid
    client._ensure_session.assert_not_called()


def test_relatorio_stops_before_print_fallback_on_terminal_access_error():
    client = bare()
    client.get_editor_sections = MagicMock(side_effect=auth.SessionAccessError('Synthetic access error'))
    client.view_document_html = MagicMock()
    with pytest.raises(auth.SessionAccessError):
        client.read_relatorio('9', '999')
    client.view_document_html.assert_not_called()
