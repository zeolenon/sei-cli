"""Auth tests using HTML fixtures (no network calls)."""
from __future__ import annotations

from sei_cli.config import orgao_to_value
from sei_cli.parsers import parse_login_form


def test_parse_login_form_extracts_action(login_html: str) -> None:
    parsed = parse_login_form(login_html, "https://sei.example.test/sip/login.php")
    assert "login.php" in parsed.action


def test_orgao_cbm_maps_to_28() -> None:
    assert orgao_to_value("CBM") == "28"
    assert orgao_to_value("cbm") == "28"


def test_orgao_unknown_returns_as_is() -> None:
    assert orgao_to_value("99") == "99"


def test_existing_session_initializes_wrapper_and_reuses_cookie(monkeypatch) -> None:
    """Canonical initialization stays GET-only and reuses cached control HTML."""
    import httpx
    from sei_cli import auth
    from sei_cli.client import SEIClient

    control = (
        '<title>Controle de Processos</title><form id="frmProcedimentoControlar">'
        '<a id="lnkInfraUnidade" title="Example unit">UNIT</a>'
        '<a id="lnkUsuarioSistema" title="test-user"></a>'
        '<table id="tblProcessosRecebidos"></table>'
        '<table id="tblProcessosGerados"></table></form>'
    )
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith('inicializar.php'):
            return httpx.Response(302, headers={
                'Location': 'controlador.php?acao=principal&infra_hash=test-wrapper',
                'Set-Cookie': 'PHPSESSID=test-refreshed; Path=/; Secure; HttpOnly',
            })
        if request.url.params.get('acao') == 'principal':
            return httpx.Response(200, text=(
                '<iframe src="controlador.php?acao=procedimento_controlar'
                '&amp;infra_sistema=100000100&amp;infra_hash=test-control"></iframe>'
            ))
        assert request.url.params['acao'] == 'procedimento_controlar'
        assert request.url.params['infra_hash'] == 'test-control'
        assert 'PHPSESSID=test-refreshed' in request.headers.get('cookie', '')
        return httpx.Response(200, text=control)

    def forbidden(*args, **kwargs):
        raise AssertionError('No login, credentials or persistence in session reuse')

    monkeypatch.setattr('sei_cli.config.load_session', lambda: {'phpsessid': 'test-existing', 'unit_id': '1'})
    monkeypatch.setattr(auth, 'create_http_client', lambda: httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))
    monkeypatch.setattr(auth, 'login', forbidden)
    monkeypatch.setattr('sei_cli.client.load_credentials', forbidden)
    monkeypatch.setattr(SEIClient, '_persist_session', forbidden)
    with SEIClient() as client:
        assert client.status().valid
        assert client.list_processes().recebidos == []
        assert client.status().unidade_sigla == 'UNIT'
    assert len(seen) == 3
    assert all(request.method == 'GET' for request in seen)
    assert [request.url.params.get('acao') for request in seen] == [None, 'principal', 'procedimento_controlar']


def test_existing_session_initializer_does_not_follow_login_redirect(monkeypatch) -> None:
    import httpx
    from sei_cli import auth
    from sei_cli.client import SEIClient

    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={'Location': '/sip/login.php'})

    monkeypatch.setattr('sei_cli.config.load_session', lambda: {'phpsessid': 'test-existing'})
    monkeypatch.setattr(auth, 'create_http_client', lambda: httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))
    with SEIClient() as client:
        assert client._try_inicializar() is None
    assert len(seen) == 1
    assert seen[0].method == 'GET'
    assert seen[0].url.path == '/sei/inicializar.php'
