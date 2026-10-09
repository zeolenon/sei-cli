from __future__ import annotations

from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from sei_cli.client import SEIClient


def _make_process_create_client(
    response_html: str,
    response_url: str = "https://sei.example.test/sei/controlador.php",
) -> SEIClient:
    client = SEIClient.__new__(SEIClient)
    client._control_html = "cached"

    form_html = """
    <form id="frmProcedimentoCadastro">
      <input name="hdnAssuntos" value="">
      <select id="selAssuntos" name="selAssuntos">
        <option value="402">082.7.1 - REQUERIMENTOS</option>
        <option value="400">082.6.1 - REQUERIMENTOS</option>
      </select>
    </form>
    """
    soup = BeautifulSoup(form_html, "lxml")

    def fake_load(_tipo_processo_id: str):
        return soup, {"hdnAssuntos": ""}, "controlador.php?acao=procedimento_cadastrar"

    def fake_post(_action: str, _data: dict[str, str]):
        client.submitted_data = dict(_data)
        return SimpleNamespace(text=response_html, url=response_url)

    client._load_process_creation_form = fake_load
    client._post = fake_post
    client.submitted_data = {}
    return client


def test_create_process_detects_sei_alert_error() -> None:
    client = _make_process_create_client(
        """
        <html>
          <body>
            <script>alert('Assuntos não são suficientes para classificação');</script>
          </body>
        </html>
        """
    )

    with pytest.raises(RuntimeError, match="Assuntos não são suficientes"):
        client.create_process("910000004", especificacao="Informacao a PRF")


def test_create_process_rejects_unconfirmed_empty_response() -> None:
    client = _make_process_create_client(
        """
        <html>
          <head><title>SEI - Controle de Processos</title></head>
          <body>Controle de Processos</body>
        </html>
        """
    )

    with pytest.raises(RuntimeError, match="nao confirmou a criacao"):
        client.create_process("910000004", especificacao="Informacao a PRF")


def test_create_process_serializes_all_preloaded_subjects_like_sei_lupa_select() -> None:
    client = _make_process_create_client(
        """
        <html>
          <head><title>SEI - 90000000.000002/2026-00</title></head>
          <body><a href="controlador.php?id_procedimento=81000136">Processo</a></body>
        </html>
        """
    )

    result = client.create_process("910000004", especificacao="Informacao a PRF")

    assert result["numero"] == "90000000.000002/2026-00"
    assert client.submitted_data["hdnAssuntos"] == (
        "402±082.7.1 - REQUERIMENTOS¥400±082.6.1 - REQUERIMENTOS"
    )
    assert client.submitted_data["selAssuntos"] == "402"
