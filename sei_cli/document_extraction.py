"""Text and visual extraction for SEI external documents."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


@dataclass(slots=True)
class DocumentExtraction:
    """Extraction result with OCR and visual-review provenance."""

    text: str
    extraction_method: str
    page_count: int = 0
    image_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    visual_artifacts: list[str] = field(default_factory=list)
    visual_analysis_required: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SEI_PAGE_HEADER = re.compile(
    r"(?im)^\s*Anexo\s+\([^\n)]*\)\s+SEI\s+\S+\s*/\s*pg\.\s*\d+\s*$"
)


def _without_sei_page_header(text: str) -> str:
    return _SEI_PAGE_HEADER.sub("", text).strip()


def _safe_stem(label: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
    return stem[:80] or "documento"


def _ocr_png(png: bytes, *, language: str) -> tuple[str, str | None]:
    executable = shutil.which("tesseract")
    if not executable:
        return "", "Tesseract não está instalado; análise visual permanece pendente."
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "-l", language, "--psm", "6"],
            input=png,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "", "OCR excedeu o limite de 90 segundos nesta página."
    except OSError as exc:
        return "", f"Não foi possível executar Tesseract: {exc}"
    if result.returncode != 0 and language != "eng":
        try:
            fallback = subprocess.run(
                [executable, "stdin", "stdout", "-l", "eng", "--psm", "6"],
                input=png,
                capture_output=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "", "OCR excedeu o limite de 90 segundos nesta página."
        except OSError as exc:
            return "", f"Não foi possível executar Tesseract: {exc}"
        if fallback.returncode == 0:
            text = fallback.stdout.decode("utf-8", errors="replace").strip()
            return text, f"Idioma OCR '{language}' indisponível; usado fallback 'eng'."
        result = fallback
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        return "", f"Tesseract falhou nesta página: {error[:300]}"
    return result.stdout.decode("utf-8", errors="replace").strip(), None


def _extract_image_document(
    data: bytes,
    *,
    document_label: str,
    output_dir: str | Path | None,
    ocr_language: str,
) -> DocumentExtraction:
    """Extract OCR and preserve a standalone image for visual review."""
    if data.startswith(b"\x89PNG"):
        suffix = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    else:
        suffix = ".bin"

    root = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="sei-document-images-"))
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / f"{_safe_stem(document_label)}-page-001{suffix}"
    image_path.write_bytes(data)
    ocr_text, warning = _ocr_png(data, language=ocr_language)
    ocr_text = _without_sei_page_header(ocr_text)
    warnings = [warning] if warning else []
    text_parts = [f"[Imagem disponível para análise visual: {image_path}]"]
    if ocr_text:
        text_parts.append("[OCR página 1]\n" + ocr_text)
    return DocumentExtraction(
        text="\n\n".join(text_parts),
        extraction_method="image_ocr" if ocr_text else "image_visual_pending",
        page_count=1,
        image_pages=[1],
        ocr_pages=[1] if ocr_text else [],
        visual_artifacts=[str(image_path)],
        visual_analysis_required=True,
        warnings=warnings,
    )


def extract_pdf_content(
    data: bytes,
    *,
    document_label: str = "documento",
    output_dir: str | Path | None = None,
    ocr_language: str = "por+eng",
    dpi: int = 200,
) -> DocumentExtraction:
    """Extract text, OCR image pages, and visual-review artifacts from a PDF."""
    if data.startswith(b"TEXT:"):
        return DocumentExtraction(
            text=data[5:].decode("utf-8").strip(),
            extraction_method="test_text",
            page_count=1,
        )

    if data.startswith((b"\x89PNG", b"\xff\xd8\xff")):
        return _extract_image_document(
            data,
            document_label=document_label,
            output_dir=output_dir,
            ocr_language=ocr_language,
        )

    try:
        fitz = __import__("fitz")
    except ImportError as exc:
        raise RuntimeError("PyMuPDF (fitz) não está disponível para ler o documento.") from exc

    try:
        document = fitz.open(stream=io.BytesIO(data), filetype="pdf")
    except Exception as exc:
        raise RuntimeError(f"Falha ao abrir PDF: {exc}") from exc

    image_pages: list[int] = []
    ocr_pages: list[int] = []
    visual_artifacts: list[str] = []
    warnings: list[str] = []
    text_parts: list[str] = []
    page_count = len(document)
    artifact_root: Path | None = Path(output_dir) if output_dir else None

    try:
        for page_number, page in enumerate(document, start=1):
            raw_text = page.get_text("text").strip()
            effective_text = _without_sei_page_header(raw_text)
            image_count = len(page.get_images(full=True))
            if image_count:
                image_pages.append(page_number)
                if artifact_root is None:
                    artifact_root = Path(tempfile.mkdtemp(prefix="sei-document-images-"))
                artifact_root.mkdir(parents=True, exist_ok=True)
                image_path = artifact_root / f"{_safe_stem(document_label)}-page-{page_number:03d}.png"
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(dpi / 72, dpi / 72),
                    alpha=False,
                )
                pixmap.save(str(image_path))
                visual_artifacts.append(str(image_path))
                ocr_text, warning = _ocr_png(
                    pixmap.tobytes("png"),
                    language=ocr_language,
                )
                ocr_text = _without_sei_page_header(ocr_text)
                if ocr_text:
                    ocr_pages.append(page_number)
                    page_parts = []
                    if effective_text:
                        page_parts.append(effective_text)
                    page_parts.append(f"[OCR página {page_number}]\n{ocr_text}")
                    text_parts.append("\n".join(page_parts))
                else:
                    if effective_text:
                        text_parts.append(effective_text)
                    text_parts.append(
                        f"[Imagem da página {page_number} disponível para análise visual: {image_path}]"
                    )
                if warning:
                    warnings.append(f"página {page_number}: {warning}")
            elif effective_text:
                text_parts.append(effective_text)
            elif raw_text:
                text_parts.append(raw_text)
    finally:
        document.close()

    text = "\n\n".join(part for part in text_parts if part).strip()
    if image_pages and ocr_pages:
        method = "pdf_text_ocr"
    elif image_pages:
        method = "pdf_image_visual_pending"
    else:
        method = "pdf_text"
    return DocumentExtraction(
        text=text,
        extraction_method=method,
        page_count=page_count,
        image_pages=image_pages,
        ocr_pages=ocr_pages,
        visual_artifacts=visual_artifacts,
        visual_analysis_required=bool(image_pages),
        warnings=warnings,
    )


def extract_document_content(
    data: bytes,
    *,
    document_label: str = "documento",
    output_dir: str | Path | None = None,
    ocr_language: str = "por+eng",
    dpi: int = 200,
) -> DocumentExtraction:
    """Extract a PDF or raw image attachment with provenance."""
    return extract_pdf_content(
        data,
        document_label=document_label,
        output_dir=output_dir,
        ocr_language=ocr_language,
        dpi=dpi,
    )
