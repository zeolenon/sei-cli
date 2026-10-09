"""Active-unit reuse tests; no session files, credentials, or live requests."""
from unittest.mock import MagicMock

import pytest

from sei_cli import client as client_module
from sei_cli.client import SEIClient
from sei_cli.models import SystemStatus, Unit

UNIT_ID = "910000015"
SIGLA = "CBM - CB2 - 3ºBBM - CMD 3ªCIA"
DESCRIPTION = "COMANDO DA 3ª CIA BM DO 3º BBM DA 2ª REGIÃO - CB-2 (CidadeTeste)"
CONTROL = f"""<a id="lnkInfraUnidade" onclick="window.location.href='controlador.php?acao=infra_trocar_unidade&amp;infra_unidade_atual={UNIT_ID}';">{SIGLA}</a>"""


def client_and_status(monkeypatch, *, valid=True, control=CONTROL):
    client = SEIClient.__new__(SEIClient)
    client.base_url = "https://sei.example.test"
    client._current_unit_id = "910000010"  # Deliberately stale session metadata.
    client._control_html = control
    client._ensure_control = MagicMock(return_value=control)
    for name in ("_fresh_control", "_get", "_post", "_persist_session", "_ensure_session", "login"):
        setattr(client, name, MagicMock(side_effect=AssertionError("Unexpected operation: " + name)))
    status = SystemStatus(valid=valid, unidade_sigla=SIGLA, unidade_descricao=DESCRIPTION, usuario="TEST")
    monkeypatch.setattr(client_module, "parse_system_status", lambda html: status)
    return client, status


@pytest.mark.parametrize("requested", [UNIT_ID, SIGLA, "  " + DESCRIPTION.lower() + "  "])
def test_current_unit_returns_before_selection_or_refresh(monkeypatch, requested):
    client, status = client_and_status(monkeypatch)

    assert client.switch_unit(requested) is status
    assert client._current_unit_id == UNIT_ID
    client._ensure_control.assert_called_once_with()
    for name in ("_fresh_control", "_get", "_post", "_persist_session", "_ensure_session", "login"):
        getattr(client, name).assert_not_called()


def test_cached_unit_id_does_not_prove_active_unit(monkeypatch):
    client, _ = client_and_status(monkeypatch)

    with pytest.raises(AssertionError, match="Unexpected operation: _fresh_control"):
        client.switch_unit("910000010")

    client._fresh_control.assert_called_once_with()
    client._post.assert_not_called()


def test_partial_label_does_not_skip_unit_resolution(monkeypatch):
    client, _ = client_and_status(monkeypatch)

    with pytest.raises(AssertionError, match="Unexpected operation: _fresh_control"):
        client.switch_unit("CidadeTeste")

    client._post.assert_not_called()


def test_invalid_control_status_does_not_reuse_unit(monkeypatch):
    client, _ = client_and_status(monkeypatch, valid=False)

    with pytest.raises(AssertionError, match="Unexpected operation: _fresh_control"):
        client.switch_unit(UNIT_ID)

    client._post.assert_not_called()


def test_exact_sigla_reuses_without_unit_selection_link(monkeypatch):
    client, status = client_and_status(monkeypatch, control="<html>control</html>")

    assert client.switch_unit(SIGLA) is status
    client._fresh_control.assert_not_called()
    client._get.assert_not_called()
    client._post.assert_not_called()


def test_other_unit_keeps_existing_selection_flow(monkeypatch):
    client, active = client_and_status(monkeypatch)
    target_id = "910000011"
    target = SystemStatus(valid=True, unidade_sigla="SECRETARIA", unidade_descricao="Secretaria", usuario="TEST")
    client._fresh_control = MagicMock(return_value=CONTROL)
    client._get = MagicMock(side_effect=[MagicMock(text="units", url=f"https://sei.example.test/sei/controlador.php?acao=infra_trocar_unidade&infra_unidade_atual={UNIT_ID}"), MagicMock(text="target-control")])
    target_control = f'<a id="lnkInfraUnidade" onclick="window.location.href=\'controlador.php?acao=infra_trocar_unidade&amp;infra_unidade_atual={target_id}\';">SECRETARIA</a>'
    client._post = MagicMock(return_value=MagicMock(text=target_control))
    client._persist_session = MagicMock()
    client._is_valid_control_html = MagicMock(return_value=True)
    monkeypatch.setattr(client_module, "parse_system_status", lambda html: target if html == target_control else active)
    monkeypatch.setattr(client_module, "parse_units_switch_page", lambda *args: [Unit(sigla="SECRETARIA", descricao="Secretaria", link=target_id)])
    monkeypatch.setattr(client_module, "parse_unit_switch_form", lambda *args: ("controlador.php?acao=infra_trocar_unidade", {}))
    monkeypatch.setattr(client_module, "parse_menu_links", lambda *args: {})

    assert client.switch_unit(target_id) is target
    assert client._current_unit_id == target_id
    client._post.assert_called_once()
    assert client._post.call_args.args[1]["selInfraUnidades"] == target_id
    client._persist_session.assert_called_once_with()
    client.login.assert_not_called()
