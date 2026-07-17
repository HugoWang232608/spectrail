from __future__ import annotations

import logging
from pathlib import Path


PDF_PREVIEW_MAX_DIMENSION = 2000
PDF_PREVIEW_MAX_SCALE = 2.0
LOGGER = logging.getLogger(__name__)


class PdfPagePreviewError(Exception):
    pass


class PdfPagePreviewUnavailableError(PdfPagePreviewError):
    pass


class PdfPagePreviewNotFoundError(PdfPagePreviewError):
    pass


def render_pdf_page(
    document_path: Path,
    page_number: int,
) -> tuple[bytes, int, int]:
    if page_number < 1:
        raise PdfPagePreviewNotFoundError("page number must be 1-based")

    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - parser uses the same dependency
        raise PdfPagePreviewUnavailableError(
            "PyMuPDF is required for PDF page previews"
        ) from exc

    try:
        document = fitz.open(document_path)
    except Exception as exc:
        raise PdfPagePreviewUnavailableError(
            f"failed to open PDF preview source: {document_path.name}"
        ) from exc
    primary_error: BaseException | None = None
    try:
        if page_number > document.page_count:
            raise PdfPagePreviewNotFoundError(
                f"PDF page does not exist: {page_number}"
            )
        page = document[page_number - 1]
        width = float(page.rect.width)
        height = float(page.rect.height)
        if width <= 0 or height <= 0:
            raise PdfPagePreviewUnavailableError(
                f"PDF page has invalid dimensions: {page_number}"
            )
        scale = min(
            PDF_PREVIEW_MAX_SCALE,
            PDF_PREVIEW_MAX_DIMENSION / width,
            PDF_PREVIEW_MAX_DIMENSION / height,
        )
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        return pixmap.tobytes("png"), pixmap.width, pixmap.height
    except (PdfPagePreviewNotFoundError, PdfPagePreviewUnavailableError) as exc:
        primary_error = exc
        raise
    except Exception as exc:
        preview_error = PdfPagePreviewUnavailableError(
            f"failed to render PDF page preview: {page_number}"
        )
        primary_error = preview_error
        raise preview_error from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            document.close()
        except Exception as exc:
            if primary_error is None:
                raise PdfPagePreviewUnavailableError(
                    f"failed to close PDF page preview source: {page_number}"
                ) from exc
            LOGGER.warning(
                "failed to close PDF page preview source %s after %s",
                page_number,
                type(primary_error).__name__,
                exc_info=True,
            )
