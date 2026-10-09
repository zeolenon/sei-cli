"""Parser tests using HTML fixtures (no network calls)."""
from __future__ import annotations

from sei_cli.parsers import (
    parse_block_documents,
    parse_blocks,
    parse_acompanhamento_especial,
    parse_expanded_folder,
    parse_marcador_form,
    parse_marcadores_list,
    parse_menu_links,
    parse_processes,
    parse_system_status,
    parse_tramitar_form,
)


BASE = "https://sei.example.test/sei/"


def test_parse_acompanhamento_especial_keeps_description_and_markers() -> None:
    content = """
    <table id="tblAcompanhamentos"><tbody>
      <tr class="infraTrClara">
        <td></td><td>
          <a aria-label="Marcador / Material / Escadas emprestadas" href="x?id_acompanhamento=77"></a>
        </td>
        <td><a title="Material: Material" href="controlador.php?acao=procedimento_trabalhar&amp;id_procedimento=123">90000000.000044/2025-00</a></td>
        <td>usuario.teste47</td><td>02/09/2026 10:00:00</td>
        <td>Material / Logística</td><td>Escada emprestada do quartel para o quartel em Cidade Sintética 1; conferir PDF.</td><td></td>
      </tr>
    </tbody></table>
    """

    records = parse_acompanhamento_especial(content, BASE)

    assert len(records) == 1
    assert records[0].numero == "90000000.000044/2025-00"
    assert records[0].descricao.startswith("Escada emprestada")
    assert records[0].marcadores == ["Marcador / Material / Escadas emprestadas"]
    assert records[0].id_acompanhamento == "77"


def test_parse_status(controle_html: str) -> None:
    status = parse_system_status(controle_html)
    assert status.valid is True
    assert status.unidade_sigla is not None
    assert "UNIDADE TESTE" in status.unidade_sigla
    assert status.usuario is not None
    assert 'Sintético Pessoa 06' in status.usuario


def test_parse_processes_counts(controle_html: str) -> None:
    result = parse_processes(controle_html, BASE)
    assert len(result.recebidos) == 33
    assert len(result.gerados) == 15


def test_process_has_id(controle_html: str) -> None:
    result = parse_processes(controle_html, BASE)
    assert result.recebidos[0].id_procedimento is not None
    assert result.recebidos[0].numero != ""


def test_parse_menu_links(controle_html: str) -> None:
    links = parse_menu_links(controle_html, BASE)
    assert "blocos_assinatura" in links
    assert "marcadores" in links


def test_parse_expanded_folder_extracts_document_origin_unit() -> None:
    html = '''
    <script>
    Nos[1] = new infraArvoreNo("DOCUMENTO","81000115","81000111","controlador.php?acao=arvore_visualizar&id_documento=81000115","ifrConteudoVisualizacao","Memorando 5 (81000048)","Memorando 5","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,false,null,null,"noVisitado","81000048");
    Nos[1].src = 'controlador.php?acao=documento_visualizar&id_documento=81000115';
    NosAcoes[1] = new infraArvoreAcao("UNIDADE_GERADORA","UG81000115","81000115","#",null,"Comando da Unidade Sintética",null,true,"CMDO UNIDADE TESTE");
    </script>
    '''

    docs = parse_expanded_folder(html, BASE)

    assert len(docs) == 1
    assert docs[0].origin_unit == "CMDO UNIDADE TESTE"
    assert docs[0].origin_description == "Comando da Unidade Sintética"


def test_parse_tramitar_form() -> None:
    html = """
    <html><body>
      <form id="frmProcedimentoEnviar" action="controlador.php?acao=procedimento_enviar_executar">
        <input type="hidden" name="infra_hash" value="abc"/>
        <select name="selUnidades">
          <option value="">Selecione</option>
          <option value="910000012">BETA DESTINO</option>
          <option value="910000015" selected>CMDO UNIDADE TESTE A</option>
        </select>
        <input type="checkbox" name="chkSinManterAberto" value="S" />
        <input type="radio" name="rdoPrazoRetornoProgramado" value="1" />
        <input type="text" name="txtPrazoRetornoProgramado" value="" />
        <input type="radio" name="rdoPrazoRetornoProgramado" value="2" />
        <input type="text" name="txtDiasRetornoProgramado" value="" />
        <input type="checkbox" name="chkSinDiasUteisRetornoProgramado" value="S" />
        <input type="radio" name="rdoPrazoReaberturaProgramada" value="1" />
        <input type="text" name="txtPrazoReaberturaProgramada" value="" />
        <input type="radio" name="rdoPrazoReaberturaProgramada" value="2" />
        <input type="text" name="txtDiasReaberturaProgramada" value="" />
        <input type="checkbox" name="chkSinDiasUteisReaberturaProgramada" value="S" />
      </form>
    </body></html>
    """
    form = parse_tramitar_form(html, BASE, BASE + "controlador.php?acao=procedimento_enviar")
    assert form.destino_field == "selUnidades"
    assert form.manter_aberto_field == "chkSinManterAberto"
    assert len(form.destinos) == 2
    assert any(d.id_unidade == "910000015" for d in form.destinos)
    assert form.retorno_programado_fields["radio"] == "rdoPrazoRetornoProgramado"
    assert form.retorno_programado_fields["data"] == "txtPrazoRetornoProgramado"
    assert form.retorno_programado_fields["dias"] == "txtDiasRetornoProgramado"
    assert form.retorno_programado_fields["uteis"] == "chkSinDiasUteisRetornoProgramado"
    assert form.reabertura_programada_fields["radio"] == "rdoPrazoReaberturaProgramada"
    assert form.reabertura_programada_fields["data"] == "txtPrazoReaberturaProgramada"
    assert form.reabertura_programada_fields["dias"] == "txtDiasReaberturaProgramada"
    assert form.reabertura_programada_fields["uteis"] == "chkSinDiasUteisReaberturaProgramada"


def test_parse_marcadores_list() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="radio" name="chkInfraItem" value="12"/></td>
          <td>LIVROS</td>
          <td>Livro Fiscal</td>
          <td><img src="svg/marcador_preto.svg?18"/></td>
        </tr>
      </table>
    </body></html>
    """
    marcadores = parse_marcadores_list(html, BASE)
    assert len(marcadores) == 1
    assert marcadores[0].marcador_id == "12"
    assert marcadores[0].nome == "LIVROS"
    assert marcadores[0].cor == "marcador_preto"


def test_parse_marcadores_list_ignores_numeric_id_column_as_description() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="checkbox" value="66588"/></td>
          <td><img src="svg/marcador_ciano.svg?18"/></td>
          <td>Informações</td>
          <td>66588</td>
        </tr>
      </table>
    </body></html>
    """
    marcadores = parse_marcadores_list(html, BASE)
    assert len(marcadores) == 1
    assert marcadores[0].nome == "Informações"
    assert marcadores[0].descricao == ""


def test_parse_marcador_form() -> None:
    html = """
    <html><body>
      <form id="frmAndamentoMarcadorCadastro" action="controlador.php?acao=andamento_marcador_salvar">
        <input type="hidden" name="infra_hash" value="xyz"/>
        <select name="selMarcador">
          <option value="">Selecione</option>
          <option value="1" selected>LIVROS</option>
          <option value="2">ALMOX</option>
        </select>
        <textarea name="txaTexto"></textarea>
      </form>
    </body></html>
    """
    form = parse_marcador_form(html, BASE, BASE + "controlador.php?acao=andamento_marcador_cadastrar")
    assert form.marcador_field == "selMarcador"
    assert form.texto_field == "txaTexto"
    assert len(form.marcadores) == 2


def test_parse_blocks_extracts_div_based_destination_units() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="checkbox"/></td>
          <td><a href="controlador.php?acao=rel_bloco_protocolo_listar&id_bloco=773617">773617</a></td>
          <td></td>
          <td></td>
          <td>Disponibilizado</td>
          <td>CBM - COBM - GAB CMDO</td>
          <td class="d-none d-md-table-cell" align="center">
            <div class="divUnidade"><div class="divUnidadeIcone"><img src="svg/bloco_aguardando_devolucao.svg?18" height="16" width="16" title="Aguardando Devolução"></div><div class="divUnidadeRotulo"><a href="javascript:void(0);" class="ancoraSigla">CMDO UNIDADE TESTE</a></div></div>
            <div class="divUnidade"><div class="divUnidadeIcone"><img src="svg/bloco_aguardando_devolucao.svg?18" height="16" width="16" title="Aguardando Devolução"></div><div class="divUnidadeRotulo"><a href="javascript:void(0);" class="ancoraSigla">CBM - COBM - CMDO 2ºSGB/3ºGBM</a></div></div>
          </td>
          <td></td>
          <td>Bloco de teste</td>
          <td></td>
        </tr>
      </table>
    </body></html>
    """

    blocks = parse_blocks(html, BASE)

    assert len(blocks) == 1
    assert blocks[0].numero == "773617"
    assert blocks[0].unidades_destino == [
        "CMDO UNIDADE TESTE",
        "CBM - COBM - CMDO 2ºSGB/3ºGBM",
    ]
    assert blocks[0].unidade_destino == (
        "CMDO UNIDADE TESTE; CBM - COBM - CMDO 2ºSGB/3ºGBM"
    )


def test_parse_block_documents_extracts_div_based_signers() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="checkbox"/></td>
          <td>1</td>
          <td>90000000.000007/2026-00</td>
          <td>81000115</td>
          <td>Relatório do Fiscal</td>
          <td align="justified">
            <div class="divItemCelula"><div class="divIconeItemCelula"><img src="svg/tabela_item_celula.svg?18" height="16" width="16" title="Assinatura"></div><div class="divRotuloItemCelula">SINTÉTICO AUTOR UM / Soldado QPBM</div></div>
            <div class="divItemCelula"><div class="divIconeItemCelula"><img src="svg/tabela_item_celula.svg?18" height="16" width="16" title="Assinatura"></div><div class="divRotuloItemCelula">Sintético Pessoa 05 / 2º Tenente QOEM BM</div></div>
            <div class="divItemCelula"><div class="divIconeItemCelula"><img src="svg/tabela_item_celula.svg?18" height="16" width="16" title="Assinatura"></div><div class="divRotuloItemCelula">SINTÉTICO AUTOR QUATRO 1 / Soldado QPBM</div></div>
            <div class="divItemCelula"><div class="divIconeItemCelula"><img src="svg/tabela_item_celula.svg?18" height="16" width="16" title="Assinatura"></div><div class="divRotuloItemCelula">SINTÉTICO AUTOR TRÊS / Soldado QPBM</div></div>
          </td>
          <td><img title="Assinatura"/></td>
        </tr>
      </table>
    </body></html>
    """

    docs = parse_block_documents(html, BASE)

    assert len(docs) == 1
    assert docs[0].assinantes == [
        "SINTÉTICO AUTOR UM / Soldado QPBM",
        "Sintético Pessoa 05 / 2º Tenente QOEM BM",
        "SINTÉTICO AUTOR QUATRO 1 / Soldado QPBM",
        "SINTÉTICO AUTOR TRÊS / Soldado QPBM",
    ]
    assert docs[0].assinante == (
        "SINTÉTICO AUTOR UM / Soldado QPBM; "
        "Sintético Pessoa 05 / 2º Tenente QOEM BM; "
        "SINTÉTICO AUTOR QUATRO 1 / Soldado QPBM; "
        "SINTÉTICO AUTOR TRÊS / Soldado QPBM"
    )
    assert docs[0].assinado is True


def test_parse_block_documents_unsigned_row_keeps_empty_signers() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrEscura">
          <td><input type="checkbox"/></td>
          <td>1</td>
          <td>90000000.000007/2026-00</td>
          <td>81000115</td>
          <td>Relatório do Fiscal</td>
          <td align="justified"></td>
          <td><img title="Pendente"/></td>
        </tr>
      </table>
    </body></html>
    """

    docs = parse_block_documents(html, BASE)

    assert len(docs) == 1
    assert docs[0].assinantes == []


def test_parse_block_documents_with_existing_signature_and_sign_action_stays_signable() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrEscura">
          <td><input type="checkbox" value="81000121-871299"/></td>
          <td>2</td>
          <td>90000000.000034/2026-00</td>
          <td><a href="#ID-81000121-871299">81000052</a></td>
          <td>Despacho</td>
          <td align="justified">
            <div class="divRotuloItemCelula">JORGE WAGNER / Cabo QPBM</div>
          </td>
          <td>
            <a href="#" onclick="return acaoAssinar('81000121-871299', 'controlador.php?acao=documento_assinar&id_documento=81000121&infra_hash=abc');">Assinar</a>
            <img title="Assinatura"/>
          </td>
        </tr>
      </table>
    </body></html>
    """

    docs = parse_block_documents(html, BASE)

    assert len(docs) == 1
    assert docs[0].assinado is False
    assert docs[0].can_sign is True
    assert docs[0].assinante == "JORGE WAGNER / Cabo QPBM"
    assert docs[0].assinado is False


def test_parse_block_documents_extracts_div_based_document_metadata() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="checkbox"/></td>
          <td>1</td>
          <td>90000000.000007/2026-00</td>
          <td>
            <div class="divItemCelula"><div class="divRotuloItemCelula">81000050</div></div>
            <div class="divItemCelula"><div class="divRotuloItemCelula">81000034</div></div>
            <div class="divItemCelula"><div class="divRotuloItemCelula">30/03/2026</div></div>
          </td>
          <td>Despacho</td>
          <td align="justified"><div class="divItemCelula"><div class="divRotuloItemCelula">Fulano / 2º Ten BM</div></div></td>
          <td><img title="Assinatura"/></td>
        </tr>
      </table>
    </body></html>
    """

    docs = parse_block_documents(html, BASE)

    assert len(docs) == 1
    assert docs[0].documento_id == "81000050"
    assert docs[0].numero_documento == "81000034"
    assert docs[0].numero_sei == "81000034"
    assert docs[0].data_documento == "30/03/2026"


def test_parse_block_documents_extracts_metadata_from_attributes() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="checkbox"/></td>
          <td>1</td>
          <td>90000000.000007/2026-00</td>
          <td>
            <a
              href="controlador.php?acao=documento_visualizar&id_documento=81000050"
              title="Documento SEI 81000034 elaborado em 30/03/2026"
            >81000050</a>
          </td>
          <td>Despacho</td>
          <td align="justified"><div class="divItemCelula"><div class="divRotuloItemCelula">Fulano / 2º Ten BM</div></div></td>
          <td><img title="Assinatura"/></td>
        </tr>
      </table>
    </body></html>
    """

    docs = parse_block_documents(html, BASE)

    assert len(docs) == 1
    assert docs[0].documento_id == "81000050"
    assert docs[0].numero_documento == "81000034"
    assert docs[0].numero_sei == "81000034"
    assert docs[0].data_documento == "30/03/2026"


def test_parse_block_documents_ignores_infra_sistema_false_positive() -> None:
    html = """
    <html><body>
      <table>
        <tr class="infraTrClara">
          <td><input type="checkbox"/></td>
          <td>1</td>
          <td>90000000.000007/2026-00</td>
          <td>
            <a
              href="controlador.php?acao=documento_visualizar&id_documento=81000050&infra_sistema=100000100"
              title="Abrir documento"
            >81000050</a>
          </td>
          <td>Despacho</td>
          <td align="justified"><div class="divItemCelula"><div class="divRotuloItemCelula">Fulano / 2º Ten BM</div></div></td>
          <td><img title="Assinatura"/></td>
        </tr>
      </table>
    </body></html>
    """

    docs = parse_block_documents(html, BASE)

    assert len(docs) == 1
    assert docs[0].documento_id == "81000050"
    assert docs[0].numero_documento is None
    assert docs[0].numero_sei is None


def test_parse_process_history_ignores_header_and_pager_rows() -> None:
    html = """
    <table id="tblHistorico">
      <tr><th>Data/Hora</th><th>Unidade</th><th>Usuário</th><th>Descrição</th></tr>
      <tr><td>01/09/2026 11:03</td><td>CBM - DF - CAF/CPO</td><td>Fulano</td>
          <td>Bloco 614662 retornado para CBM - DAL - DAL/1</td></tr>
      <tr><td>31/08/2026 09:37:18</td><td>CBM - DAL - DAL/1</td><td>Ciclano</td>
          <td>Documento 81000078 assinado</td></tr>
      <tr><td>128 registros - 1 a 2</td><td></td><td></td><td></td></tr>
    </table>
    """

    from sei_cli.parsers import parse_process_history

    entries = parse_process_history(html)

    assert len(entries) == 2
    assert entries[0].date_time == "01/09/2026 11:03"
    assert entries[0].unit == "CBM - DF - CAF/CPO"
    assert entries[0].description.startswith("Bloco 614662")
    assert entries[1].user == "Ciclano"
