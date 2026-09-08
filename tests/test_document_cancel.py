from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from click.testing import CliRunner

from sei_cli.cli import cli
from sei_cli.client import SEIClient
from sei_cli.models import TreeDocument
from sei_cli.operations import document_cancel_confirm, document_cancel_preview


PROCESS_ID = "process-test"
DOCUMENT_ID = "document-test"


class CancelClient:
    def __init__(self, *, form_ok: bool = True) -> None:
        self.cancel_calls: list[tuple[str, str, str]] = []
        self.form_ok = form_ok
        self.tree = [
            TreeDocument(
                id_documento=DOCUMENT_ID,
                nome="Despacho de teste",
                tipo="interno",
                sei_number="SEI-TEST-1",
                origin_unit="Unidade Teste",
                assinado=True,
            )
        ]

    def status(self) -> Any:
        return SimpleNamespace(
            valid=True,
            unidade_sigla="Unidade Teste",
            unidade_descricao="Unidade Teste",
            usuario="Usuário Teste",
            ultimo_acesso=None,
        )

    def __enter__(self) -> "CancelClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_full_document_tree(
        self, id_procedimento: str, *, expand_all: bool = True
    ) -> list[TreeDocument]:
        assert id_procedimento == PROCESS_ID
        return self.tree

    def get_document_cancel_form_info(
        self, id_documento: str, id_procedimento: str
    ) -> dict[str, Any]:
        assert (id_documento, id_procedimento) == (DOCUMENT_ID, PROCESS_ID)
        if not self.form_ok:
            return {"ok": False, "error": "Cancelamento não disponível"}
        return {
            "ok": True,
            "form_id": "frmDocumentoCancelar",
            "method": "post",
            "reason_field": "txaMotivo",
        }

    def cancel_document(
        self, id_documento: str, id_procedimento: str, motivo: str
    ) -> dict[str, Any]:
        self.cancel_calls.append((id_documento, id_procedimento, motivo))
        return {
            "ok": True,
            "cancelled": True,
            "verified": True,
            "document_remained_in_tree": True,
            "cancelled_marker": True,
        }


class Guard:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def _patch_resolution(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "sei_cli.operations.writing._resolve_document_ids",
        lambda client, numero_ou_id, *, id_procedimento=None: (
            DOCUMENT_ID,
            PROCESS_ID,
            "SEI-TEST-1",
        ),
    )
    monkeypatch.setattr(
        "sei_cli.operations.writing._process_unit_preflight",
        lambda client, id_procedimento: ({"required": False}, Guard()),
    )


def test_preview_requires_reason(monkeypatch: Any) -> None:
    _patch_resolution(monkeypatch)
    result = document_cancel_preview(CancelClient(), DOCUMENT_ID, process_id=PROCESS_ID)
    assert result["ok"] is False
    assert result["error"]["code"] == "workflow_violation"
    assert "obrigatório" in result["error"]["message"]


def test_confirm_requires_both_flags_without_calling_cancel(monkeypatch: Any) -> None:
    _patch_resolution(monkeypatch)
    client = CancelClient()
    result = document_cancel_confirm(
        client,
        DOCUMENT_ID,
        process_id=PROCESS_ID,
        motivo="Teste controlado",
        confirm=True,
        confirm_impact=False,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "workflow_violation"
    assert client.cancel_calls == []


def test_confirm_with_both_flags_calls_native_cancel(monkeypatch: Any) -> None:
    _patch_resolution(monkeypatch)
    client = CancelClient()
    result = document_cancel_confirm(
        client,
        DOCUMENT_ID,
        process_id=PROCESS_ID,
        motivo="Teste controlado",
        confirm=True,
        confirm_impact=True,
    )
    assert result["ok"] is True
    assert client.cancel_calls == [(DOCUMENT_ID, PROCESS_ID, "Teste controlado")]
    assert result["data"]["result"]["document_remained_in_tree"] is True


def test_cli_exposes_two_confirmation_flags(monkeypatch: Any) -> None:
    client = CancelClient()
    _patch_resolution(monkeypatch)
    monkeypatch.setattr("sei_cli.cli.SEIClient", lambda: client)

    result = CliRunner().invoke(
        cli,
        [
            "document-cancel-confirm",
            DOCUMENT_ID,
            "--process-id",
            PROCESS_ID,
            "--motivo",
            "Teste controlado",
            "--confirm",
            "--json",
        ],
    )
    assert result.exit_code == 1
    assert "--confirm-impact" in result.output
    assert client.cancel_calls == []


def test_extract_document_cancel_action_from_serialized_tree() -> None:
    tree = (
        "Nos[1].acoes = '<a href=\\\"controlador.php?acao=documento_cancelar&"
        "acao_origem=arvore_visualizar&id_procedimento=process-test&"
        "id_documento=document-test&infra_hash=abc123\\\" >Cancelar</a>';"
    )
    result = SEIClient._extract_document_action_url(
        tree,
        "documento_cancelar",
        PROCESS_ID,
        DOCUMENT_ID,
    )
    assert result is not None
    assert "acao=documento_cancelar" in result
    assert "id_documento=document-test" in result


def test_client_cancel_posts_native_reason_and_preserves_document() -> None:
    client = object.__new__(SEIClient)
    client._control_html = None
    client.get_document_cancel_form_info = Mock(
        return_value={
            "ok": True,
            "form_action": "https://sei.test/controlador.php?acao=documento_cancelar",
            "reason_field": "txaMotivo",
            "form_fields": [("hdnInfraTipoPagina", "2"), ("txaMotivo", "")],
        }
    )
    response = SimpleNamespace(
        url="https://sei.test/sei/controlador.php?acao=arvore_visualizar",
        text="<html><body>ok</body></html>",
    )
    client._post_pairs = Mock(return_value=response)
    client._navigate_to_arvore = Mock(
        return_value=(
            'Nos[1] = new infraArvoreNo("DOCUMENTO","document-test",'
            '"process-test","controlador.php?acao=documento_visualizar",'
            '"ifr","Despacho","Despacho","svg/documento_cancelado.svg?18");'
        )
    )

    result = client.cancel_document(DOCUMENT_ID, PROCESS_ID, "Motivo real")

    assert result["verified"] is True
    pairs = client._post_pairs.call_args.args[1]
    assert ("hdnInfraTipoPagina", "2") in pairs
    assert ("txaMotivo", "Motivo real") in pairs
    assert ("sbmSalvar", "Salvar") in pairs
    assert pairs.count(("txaMotivo", "Motivo real")) == 1
