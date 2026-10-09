"""Tests for relatorio_parser — extracts structured data from SEI relatórios."""

import pytest
from pathlib import Path

from sei_cli.relatorio_parser import (
    parse_relatorio,
    summarize_batch,
    summarize,
    to_dict,
    _parse_rank_name,
    _mes_to_num,
)


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def relatorio_html():
    return (FIXTURES / "relatorio_body.html").read_text()


@pytest.fixture
def parsed(relatorio_html):
    return parse_relatorio(relatorio_html)


# --- Utility tests ---

class TestParseRankName:
    def test_sd(self):
        assert _parse_rank_name("SD BM Sintético Militar 03") == ("SD BM", "Sintético Militar 03")

    def test_sgt_degree(self):
        assert _parse_rank_name("2° SGT BM Sintético Fiscal") == ("2° SGT BM", "Sintético Fiscal")

    def test_sgt_ordinal(self):
        assert _parse_rank_name("3º SGT BM Sintético Militar 02") == ("3º SGT BM", "Sintético Militar 02")

    def test_1_sgt_qpbm(self):
        posto, nome = _parse_rank_name("1° SGT QPBM Sintético Substituto")
        assert "SGT" in posto
        assert nome == "Sintético Substituto"

    def test_no_rank(self):
        assert _parse_rank_name("XXXX") == ("", "XXXX")

    def test_empty(self):
        assert _parse_rank_name("") == ("", "")


class TestMesToNum:
    def test_basic(self):
        assert _mes_to_num("março") == 3
        assert _mes_to_num("marco") == 3
        assert _mes_to_num("dezembro") == 12

    def test_unknown(self):
        assert _mes_to_num("xyzzy") == 0


# --- Header tests ---

class TestHeader:
    def test_fiscal(self, parsed):
        assert parsed.fiscal == "Sintético Fiscal"
        assert parsed.posto_fiscal == "2° SGT BM"

    def test_dates(self, parsed):
        assert parsed.data_inicio == "04/03/2026"
        assert parsed.data_fim == "05/03/2026"

    def test_unidade(self, parsed):
        assert "UNIDADE TESTE" in parsed.unidade
        assert "Cidade Sintética" in parsed.unidade


# --- Personnel tests ---

class TestMilitares:
    def test_count(self, parsed):
        assert len(parsed.militares) == 8

    def test_fiscal_present(self, parsed):
        fiscal = next(m for m in parsed.militares if m.nome == "Sintético Fiscal")
        assert fiscal.posto == "2° SGT BM"
        assert fiscal.funcao == "Fiscal de Operações"
        assert fiscal.status == "ordinario"

    def test_vtr_assignments(self, parsed):
        abt = [m for m in parsed.militares if m.viatura == "ABT-90"]
        assert len(abt) == 3
        names = {m.nome for m in abt}
        assert "Sintético Militar 02" in names
        assert "Sintético Militar 03" in names
        assert "Sintético Militar 04" in names

    def test_as42_crew(self, parsed):
        crew = [m for m in parsed.militares if m.viatura == "AS-91"]
        assert len(crew) == 2
        assert any(m.nome == "Sintético Militar 05" for m in crew)

    def test_ur28_crew(self, parsed):
        crew = [m for m in parsed.militares if m.viatura == "UR-95"]
        assert len(crew) == 2

    def test_status_distribution(self, parsed):
        by_status = {}
        for m in parsed.militares:
            by_status.setdefault(m.status, []).append(m)
        assert len(by_status["ordinario"]) == 3
        assert len(by_status["extraordinario"]) == 5


# --- Viaturas tests ---

class TestViaturas:
    def test_count(self, parsed):
        assert len(parsed.viaturas) >= 4  # at least ABT, AS, UR, ABTS

    def test_abt_inoperante(self, parsed):
        abt = next(v for v in parsed.viaturas if "ABT-90" in v.prefixo)
        assert "inoperante" in abt.situacao.lower()

    def test_as42_operante(self, parsed):
        asv = next(v for v in parsed.viaturas if "AS-91" in v.prefixo)
        assert "operante" in asv.situacao.lower()
        assert asv.hodometro == "10.000"


# --- Sections tests ---

class TestSections:
    def test_simple_sections(self, parsed):
        assert "alterações" in parsed.parada.lower() or parsed.parada == ""
        assert "alterações" in parsed.rancho.lower() or parsed.rancho == ""

    def test_armamento(self, parsed):
        assert len(parsed.armamento) >= 3  # pistola, carregadores, munições

    def test_hts(self, parsed):
        assert len(parsed.hts) == 2  # APX 2000 radios


# --- Ocorrências ---

class TestOcorrencias:
    def test_count(self, parsed):
        assert len(parsed.ocorrencias) == 2

    def test_first(self, parsed):
        o = parsed.ocorrencias[0]
        assert o.boletim == "99000001/2026"
        assert o.codigo == "9901"
        assert "TREINAMENTO" in o.natureza

    def test_second(self, parsed):
        o = parsed.ocorrencias[1]
        assert "HOSPITALAR" in o.natureza


# --- Guarda/Ronda ---

class TestGuardaRonda:
    def test_count(self, parsed):
        assert len(parsed.guarda_ronda) == 14  # 7 guarda + 7 ronda

    def test_types(self, parsed):
        guardas = [g for g in parsed.guarda_ronda if g.tipo == "guarda"]
        rondas = [g for g in parsed.guarda_ronda if g.tipo == "ronda"]
        assert len(guardas) == 7
        assert len(rondas) == 7


# --- Passagem ---

class TestPassagem:
    def test_passagem_para(self, parsed):
        assert "Sintético Substituto" in parsed.passagem_para


# --- Output ---

class TestOutput:
    def test_summarize(self, parsed):
        s = summarize(parsed)
        assert "Sintético Fiscal" in s
        assert "04/03/2026" in s
        assert "ABT-90" in s
        assert "Ocorrências" in s

    def test_to_dict(self, parsed):
        d = to_dict(parsed)
        assert isinstance(d, dict)
        assert d["fiscal"] == "Sintético Fiscal"
        assert len(d["militares"]) == 8


def test_summarize_batch_markdown(parsed):
    md = summarize_batch([parsed, parsed])
    assert md.startswith("# Resumo Semanal")
    assert "## Viaturas" in md
    assert "## Ocorrências por Dia" in md
