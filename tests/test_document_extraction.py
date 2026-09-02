from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from sei_cli.document_extraction import extract_document_content, extract_pdf_content


def _image_pdf() -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 900, 600), False)
    image.clear_with(255)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text(
        (40, 40),
        "Anexo (123) SEI 08810116.003584/2025-48 / pg. 1",
    )
    page.insert_image(fitz.Rect(40, 70, 550, 420), pixmap=image)
    output = pdf.tobytes()
    pdf.close()
    image = None
    return output


def test_extracts_ocr_and_preserves_visual_artifact(monkeypatch: Any, tmp_path: Path) -> None:
    def fake_ocr(_png: bytes, *, language: str) -> tuple[str, str | None]:
        assert language == "por+eng"
        return "Anexo (123) SEI 08810116.003584/2025-48 / pg. 1\nTexto do print", None

    monkeypatch.setattr("sei_cli.document_extraction._ocr_png", fake_ocr)

    result = extract_pdf_content(
        _image_pdf(),
        document_label="fotos externas",
        output_dir=tmp_path,
    )

    assert result.extraction_method == "pdf_text_ocr"
    assert result.page_count == 1
    assert result.image_pages == [1]
    assert result.ocr_pages == [1]
    assert result.visual_analysis_required is True
    assert len(result.visual_artifacts) == 1
    assert Path(result.visual_artifacts[0]).is_file()
    assert "Texto do print" in result.text
    assert "Anexo (123) SEI" not in result.text


def test_image_without_ocr_is_not_reported_as_empty(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "sei_cli.document_extraction._ocr_png",
        lambda _png, *, language: ("", "Tesseract indisponível"),
    )

    result = extract_pdf_content(
        _image_pdf(),
        document_label="fotos externas",
        output_dir=tmp_path,
    )

    assert result.extraction_method == "pdf_image_visual_pending"
    assert result.page_count == 1
    assert result.image_pages == [1]
    assert result.ocr_pages == []
    assert result.visual_analysis_required is True
    assert result.visual_artifacts
    assert "Imagem da página 1 disponível para análise visual" in result.text
    assert result.warnings == ["página 1: Tesseract indisponível"]


def test_raw_jpeg_attachment_is_ocr_capable(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "sei_cli.document_extraction._ocr_png",
        lambda _png, *, language: ("texto de foto", None),
    )

    result = extract_document_content(
        b"\xff\xd8\xfffake-jpeg",
        document_label="foto externa",
        output_dir=tmp_path,
    )

    assert result.extraction_method == "image_ocr"
    assert result.ocr_pages == [1]
    assert result.visual_artifacts[0].endswith("foto-externa-page-001.jpg")
    assert "texto de foto" in result.text


def test_text_fixture_remains_supported() -> None:
    result = extract_document_content("TEXT:conteúdo de fixture".encode())

    assert result.text == "conteúdo de fixture"
    assert result.extraction_method == "test_text"
    assert result.page_count == 1
    assert result.visual_artifacts == []
