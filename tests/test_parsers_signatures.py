from __future__ import annotations

from pathlib import Path

from sei_cli.client import SEIClient
from sei_cli.parsers import parse_expanded_folder, parse_tree_signatures


FIXTURE = Path("tests/fixtures/arvore_with_signatures.js").read_text()
BASE = "https://sei.example.test/sei/"


def test_parse_assinatura() -> None:
    sigs = parse_tree_signatures(FIXTURE)

    assert "81000127" in sigs
    entry = sigs["81000127"][0]
    assert entry.kind == "assinatura"
    assert entry.signer == "Sintético Pessoa 05"
    assert entry.role == "2º Tenente QOEM BM"


def test_parse_autenticacao() -> None:
    sigs = parse_tree_signatures(FIXTURE)

    assert "81000129" in sigs
    entry = sigs["81000129"][0]
    assert entry.kind == "autenticacao"
    assert entry.signer == "Sintético Pessoa 05"


def test_multiple_signers() -> None:
    content = """
    NosAcoes[20] = new infraArvoreAcao("ASSINATURA","A81000124","81000124","javascript:alert('Assinado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE\\n\\nGEORGE WAGNER GUEDES BEZERRA\\nCabo QPBM\\nUNIDADE TESTE');",null,"Assinado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE\\n\\nGEORGE WAGNER GUEDES BEZERRA\\nCabo QPBM\\nUNIDADE TESTE","svg/assinatura2.svg?18",true);
    """

    sigs = parse_tree_signatures(content)

    assert len(sigs["81000124"]) == 2
    assert sigs["81000124"][0].signer == "Sintético Pessoa 05"
    assert sigs["81000124"][1].signer == "GEORGE WAGNER GUEDES BEZERRA"


def test_no_signatures() -> None:
    content = 'Nos[1] = new infraArvoreNo("DOCUMENTO","1","P","url","t","Doc sem assinatura","Doc sem assinatura","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,true,null,null,"noVisitado","123");'

    sigs = parse_tree_signatures(content)

    assert sigs == {}


def test_integration_tree_document() -> None:
    docs = parse_expanded_folder(FIXTURE, BASE)
    doc = next(d for d in docs if d.id_documento == "81000127")
    pdf = next(d for d in docs if d.id_documento == "81000129")
    unsigned = next(d for d in docs if d.id_documento == "81000128")

    assert doc.assinado is True
    assert doc.autenticado is False
    assert len(doc.assinaturas) == 1
    assert pdf.assinado is False
    assert pdf.autenticado is True
    assert len(pdf.assinaturas) == 1
    assert unsigned.assinado is False
    assert unsigned.autenticado is False
    assert unsigned.assinaturas == []


def test_mixed_process_lazy_loaded_folder_merge() -> None:
    root = """
    Pastas[1]['link'] = 'controlador.php?acao=expandir';
    Pastas[1]['protocolos'] = '81000124,81000125';
    Nos[10] = new infraArvoreNo("PASTA","PASTA1","81000126","#","ifrArvore","Pasta I (10)","Pasta I (10)","svg/pasta_fechada.svg?18","svg/pasta_fechada.svg?18","svg/pasta_fechada.svg?18",true,true,null,null,"noVisitado","");
    Nos[10].carregado = false;
    Nos[1] = new infraArvoreNo("DOCUMENTO","81000127","81000126","controlador.php?acao=arvore_visualizar&id_documento=81000127","ifrConteudoVisualizacao","Exposição de Motivos 191 (81000057)","Exposição de Motivos 191 (81000057)","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,true,null,null,"noVisitado","81000057");
    Nos[2] = new infraArvoreNo("DOCUMENTO","81000129","81000126","controlador.php?acao=arvore_visualizar&id_documento=81000129","ifrConteudoVisualizacao","Escala Ordinária Março (81000059)","Escala Ordinária Março (81000059)","svg/documento_pdf.svg?18","svg/documento_pdf.svg?18","svg/documento_pdf.svg?18",true,true,null,null,"noVisitado","81000059");
    NosAcoes[1] = new infraArvoreAcao("ASSINATURA","A81000127","81000127","javascript:alert('Assinado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE');",null,"Assinado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE","svg/assinatura2.svg?18",true);
    NosAcoes[2] = new infraArvoreAcao("ASSINATURA","A81000129","81000129","javascript:alert('Autenticado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE');",null,"Autenticado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE","svg/autenticacao2.svg?18",true);
    """
    expansion = """OK
    Nos[3] = new infraArvoreNo("DOCUMENTO","81000124","PASTA1","controlador.php?acao=arvore_visualizar&id_documento=81000124","ifrConteudoVisualizacao","Encaminhamento 232 (81000052)","Encaminhamento 232 (81000052)","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,true,null,null,"noVisitado","81000052");
    Nos[4] = new infraArvoreNo("DOCUMENTO","81000125","PASTA1","controlador.php?acao=arvore_visualizar&id_documento=81000125","ifrConteudoVisualizacao","Informação 10 (81000053)","Informação 10 (81000053)","svg/documento_interno.svg?18","svg/documento_interno.svg?18","svg/documento_interno.svg?18",true,true,null,null,"noVisitado","81000053");
    NosAcoes[20] = new infraArvoreAcao("ASSINATURA","A81000124","81000124","javascript:alert('Assinado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE\\n\\nGEORGE WAGNER GUEDES BEZERRA\\nCabo QPBM\\nUNIDADE TESTE');",null,"Assinado por:\\nSintético Pessoa 05\\n2º Tenente QOEM BM\\nUNIDADE TESTE\\n\\nGEORGE WAGNER GUEDES BEZERRA\\nCabo QPBM\\nUNIDADE TESTE","svg/assinatura2.svg?18",true);
    """

    client = SEIClient.__new__(SEIClient)
    client._sei_url = lambda path="": BASE + path.lstrip("/")
    client._navigate_to_arvore = lambda _id: root

    class Resp:
        def __init__(self, text: str) -> None:
            self.text = text

    client._post = lambda *_args, **_kwargs: Resp(expansion)

    docs = client.get_full_document_tree("81000126", expand_all=True)

    assert len(docs) == 4
    root_signed = next(d for d in docs if d.id_documento == "81000127")
    root_pdf = next(d for d in docs if d.id_documento == "81000129")
    lazy_signed = next(d for d in docs if d.id_documento == "81000124")
    lazy_unsigned = next(d for d in docs if d.id_documento == "81000125")

    assert root_signed.assinado is True
    assert root_pdf.autenticado is True
    assert lazy_signed.assinado is True
    assert len(lazy_signed.assinaturas) == 2
    assert lazy_unsigned.assinado is False
