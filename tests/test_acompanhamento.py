from __future__ import annotations

from sei_cli.client import SEIClient


BASE = "https://sei.rn.gov.br"


def _page(*, description: str, marker: str, total: int = 1, page: int = 0) -> str:
    return f"""
    <html><body>
      <form id="frmAcompanhamentoLista"
            action="/sei/controlador.php?acao=acompanhamento_listar">
        <input type="hidden" name="hdnInfraNroItens" value="50">
        <input type="hidden" name="hdnInfraPaginaAtual" value="{page}">
        <input type="text" name="txtPalavrasPesquisaAcompanhamento" value="">
        <select name="selGrupoAcompanhamento">
          <option value="">Todos</option>
          <option value="123">Material / Logística</option>
        </select>
      </form>
      <div>{total} registros - 1 a {min(total, 50)}</div>
      <table id="tblAcompanhamentos"><tbody>
        <tr class="infraTrClara">
          <td></td>
          <td><a aria-label="{marker}" href="x?id_acompanhamento=77"></a></td>
          <td><a title="Material: Material"
                 href="/sei/controlador.php?acao=procedimento_trabalhar&amp;id_procedimento=123">
            08810254.000239/2025-78
          </a></td>
          <td>11199338702</td><td>02/09/2026 10:00:00</td>
          <td>Material / Logística</td><td>{description}</td><td></td>
        </tr>
      </tbody></table>
    </body></html>
    """


class OfflineSearchClient(SEIClient):
    def __init__(self) -> None:
        self.base_url = BASE
        self.posts: list[tuple[int, dict[str, str] | None]] = []

    def _get_acompanhamento_page(self) -> str:
        return _page(
            description="Movimentação de material",
            marker="Marcador / Material / Escada emprestada",
        )

    def _post_acompanhamento_page(
        self,
        content: str,
        page: int,
        overrides: dict[str, str] | None = None,
    ) -> str:
        self.posts.append((page, overrides))
        return _page(
            description="Movimentação de material",
            marker="Marcador / Material / Escada emprestada",
        )


def test_acompanhamento_page_info_reads_total_pages() -> None:
    content = _page(description="x", marker="Marcador / x", total=187)
    assert SEIClient._acompanhamento_page_info(content) == (0, 4)


def test_search_acompanhamento_matches_marker_without_opening_process() -> None:
    client = OfflineSearchClient()

    records = client.search_acompanhamento_especial("escada emprestada")

    assert len(records) == 1
    assert records[0].numero == "08810254.000239/2025-78"
    assert records[0].marcadores == ["Marcador / Material / Escada emprestada"]
    assert client.posts == [
        (
            0,
            {
                "txtPalavrasPesquisaAcompanhamento": "escada emprestada",
                "selGrupoAcompanhamento": "",
            },
        )
    ]
