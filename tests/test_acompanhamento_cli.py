from __future__ import annotations

import json
from typing import Any

from click.testing import CliRunner

from sei_cli.cli import cli
from sei_cli.models import AcompanhamentoEspecial


class FakeSearchClient:
    def __init__(self) -> None:
        self.switched_to: str | None = None

    def __enter__(self) -> "FakeSearchClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def switch_unit(self, unit: str) -> None:
        self.switched_to = unit

    def search_acompanhamento_especial(
        self, palavras: str | None = None, *, grupo: str | None = None
    ) -> list[AcompanhamentoEspecial]:
        assert palavras == "escada"
        assert grupo == "Material / Logística"
        return [
            AcompanhamentoEspecial(
                numero="08810254.000239/2025-78",
                tipo="Material: Movimentação de Material Permanente",
                descricao="Report: Escada extensível danificada.",
                id_procedimento="123",
                link="https://sei.rn.gov.br/sei/controlador.php?id_procedimento=123",
                grupo="Material / Logística",
                marcadores=["Marcador / Equipamentos Operacional"],
                id_acompanhamento="77",
            )
        ]


def test_cli_acompanhamento_search_emits_structured_metadata(monkeypatch: Any) -> None:
    monkeypatch.setattr("sei_cli.cli.SEIClient", FakeSearchClient)

    result = CliRunner().invoke(
        cli,
        [
            "acompanhamento-search",
            "--palavras",
            "escada",
            "--grupo",
            "Material / Logística",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == 1
    assert payload["resultados"][0]["descricao"] == "Report: Escada extensível danificada."
    assert payload["resultados"][0]["marcadores"] == ["Marcador / Equipamentos Operacional"]
