from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import Mock

import fitz
import pytest

from sei_cli.document_extraction import _ocr_png, extract_document_content, extract_pdf_content


def _image_pdf() -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 900, 600), False)
    image.clear_with(255)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text(
        (40, 40),
        "Anexo (123) SEI 90000000.000025/2025-00 / pg. 1",
    )
    page.insert_image(fitz.Rect(40, 70, 550, 420), pixmap=image)
    output = pdf.tobytes()
    pdf.close()
    image = None
    return output


def test_extracts_ocr_and_preserves_visual_artifact(monkeypatch: Any, tmp_path: Path) -> None:
    def fake_ocr(_png: bytes, *, language: str) -> tuple[str, str | None]:
        assert language == "por+eng"
        return "Anexo (123) SEI 90000000.000025/2025-00 / pg. 1\nTexto do print", None

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


@pytest.mark.parametrize("image_format,extension", [("jpeg", "jpg"), ("png", "png")])
def test_raw_image_attachment_is_ocr_capable(
    monkeypatch: Any, tmp_path: Path, image_format: str, extension: str,
) -> None:
    monkeypatch.setattr(
        "sei_cli.document_extraction._ocr_png",
        lambda _png, *, language: ("texto de foto", None),
    )

    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
    image.clear_with(255)
    data = image.tobytes(image_format)
    result = extract_document_content(
        data,
        document_label="foto externa",
        output_dir=tmp_path,
    )

    assert result.extraction_method == "image_ocr"
    assert result.ocr_pages == [1]
    artifact = Path(result.visual_artifacts[0])
    assert artifact.name == f"foto-externa-page-001.{extension}"
    assert artifact.read_bytes() == data
    assert "texto de foto" in result.text


def test_text_pdf_needs_no_ocr_or_visual_artifacts(monkeypatch: Any, tmp_path: Path) -> None:
    ocr = Mock(side_effect=AssertionError("PDF textual não precisa de OCR"))
    monkeypatch.setattr("sei_cli.document_extraction._ocr_png", ocr)
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "Conteúdo da primeira página")
        pdf.new_page().insert_text((40, 40), "Conteúdo da segunda página")
        data = pdf.tobytes()

    result = extract_document_content(data, output_dir=tmp_path)

    assert result.text == "Conteúdo da primeira página\n\nConteúdo da segunda página"
    assert result.extraction_method == "pdf_text"
    assert result.page_count == 2
    assert result.visual_artifacts == []
    assert result.visual_analysis_required is False
    assert list(tmp_path.iterdir()) == []
    ocr.assert_not_called()


@pytest.mark.parametrize("data", [b"TEXT:isto nao e um PDF", b"invalid PDF", b""])
def test_invalid_pdf_is_rejected(data: bytes) -> None:
    with pytest.raises(RuntimeError, match="Falha ao abrir PDF"):
        extract_document_content(data)


@pytest.mark.parametrize(
    "language,returncodes,expected_attempts,expected_text,warning_fragment",
    [
        ("por+eng", [0], ["por+eng"], "texto OCR", None),
        ("por+eng", [1, 0], ["por+eng", "eng"], "texto OCR", "fallback 'eng'"),
        ("por+eng", [1, 1], ["por+eng", "eng"], "", "Tesseract falhou"),
        ("eng", [1], ["eng"], "", "Tesseract falhou"),
    ],
)
def test_ocr_language_attempts(
    monkeypatch: Any,
    language: str,
    returncodes: list[int],
    expected_attempts: list[str],
    expected_text: str,
    warning_fragment: str | None,
) -> None:
    monkeypatch.setattr("sei_cli.document_extraction.shutil.which", lambda _: "/tesseract")
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], code, b" texto OCR \n", b"OCR error")
        for code in returncodes
    ])
    monkeypatch.setattr("sei_cli.document_extraction.subprocess.run", run)

    text, warning = _ocr_png(b"image", language=language)

    assert text == expected_text
    assert [call.args[0][4] for call in run.call_args_list] == expected_attempts
    if warning_fragment:
        assert warning is not None and warning_fragment in warning
    else:
        assert warning is None


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize(
    "error,warning_fragment",
    [
        (subprocess.TimeoutExpired("tesseract", 90), "90 segundos"),
        (OSError("cannot execute"), "Não foi possível executar"),
    ],
)
def test_ocr_execution_error_preserves_warning(
    monkeypatch: Any, fallback: bool, error: Exception, warning_fragment: str,
) -> None:
    monkeypatch.setattr("sei_cli.document_extraction.shutil.which", lambda _: "/tesseract")
    outcomes = [subprocess.CompletedProcess([], 1, b"", b"failed")] if fallback else []
    run = Mock(side_effect=[*outcomes, error])
    monkeypatch.setattr("sei_cli.document_extraction.subprocess.run", run)

    text, warning = _ocr_png(b"image", language="por+eng")

    assert text == ""
    assert warning is not None and warning_fragment in warning
    assert run.call_count == (2 if fallback else 1)


def test_missing_tesseract_preserves_warning(monkeypatch: Any) -> None:
    monkeypatch.setattr("sei_cli.document_extraction.shutil.which", lambda _: None)
    run = Mock()
    monkeypatch.setattr("sei_cli.document_extraction.subprocess.run", run)

    text, warning = _ocr_png(b"image", language="por+eng")

    assert text == ""
    assert warning is not None and "não está instalado" in warning
    run.assert_not_called()
