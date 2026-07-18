"""Regenerate deterministic PDF merged-table fixtures for M5.1.

Run with:

    python3 tests/fixtures/build_pdf_merged_table_fixtures.py
"""

from pathlib import Path

from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
PAGE_WIDTH = 400
PAGE_HEIGHT = 250
TABLE_X0 = 50
TABLE_X1 = 300
TABLE_Y0 = 50
TABLE_Y1 = 150
ROW_BOUNDARY = 100
COLUMN_BOUNDARY = 175


def _pdf_y(top_left_y: float) -> float:
    return PAGE_HEIGHT - top_left_y


def _line(
    output: canvas.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> None:
    output.line(x0, _pdf_y(y0), x1, _pdf_y(y1))


def _text(
    output: canvas.Canvas,
    x: float,
    baseline_y: float,
    value: str,
) -> None:
    output.drawString(x, _pdf_y(baseline_y), value)


def _new_canvas(path: Path, title: str) -> canvas.Canvas:
    output = canvas.Canvas(
        path.as_posix(),
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        pageCompression=0,
        invariant=1,
    )
    output.setTitle(title)
    output.setAuthor("SpecTrail")
    output.setLineWidth(1)
    output.setFont("Helvetica", 11)
    return output


def _finish(output: canvas.Canvas) -> None:
    output.showPage()
    output.save()


def build_horizontal_merge() -> None:
    output = _new_canvas(
        ROOT / "pdf_table_horizontal_merge.pdf",
        "SpecTrail horizontal merged PDF table",
    )
    for x in (TABLE_X0, TABLE_X1):
        _line(output, x, TABLE_Y0, x, TABLE_Y1)
    _line(
        output,
        COLUMN_BOUNDARY,
        ROW_BOUNDARY,
        COLUMN_BOUNDARY,
        TABLE_Y1,
    )
    for y in (TABLE_Y0, ROW_BOUNDARY, TABLE_Y1):
        _line(output, TABLE_X0, y, TABLE_X1, y)
    _text(output, 118, 80, "Merged requirement header")
    _text(output, 65, 130, "REQ-H")
    _text(output, 190, 130, "Approved")
    _finish(output)


def build_vertical_merge() -> None:
    output = _new_canvas(
        ROOT / "pdf_table_vertical_merge.pdf",
        "SpecTrail vertical merged PDF table",
    )
    for x in (TABLE_X0, COLUMN_BOUNDARY, TABLE_X1):
        _line(output, x, TABLE_Y0, x, TABLE_Y1)
    for y in (TABLE_Y0, TABLE_Y1):
        _line(output, TABLE_X0, y, TABLE_X1, y)
    _line(
        output,
        COLUMN_BOUNDARY,
        ROW_BOUNDARY,
        TABLE_X1,
        ROW_BOUNDARY,
    )
    _text(output, 65, 95, "Shared control")
    _text(output, 190, 80, "First state")
    _text(output, 190, 130, "Second state")
    _finish(output)


def build_ambiguous_merge() -> None:
    output = _new_canvas(
        ROOT / "pdf_table_ambiguous_merge.pdf",
        "SpecTrail ambiguous merged PDF table",
    )
    for x in (TABLE_X0, TABLE_X1):
        _line(output, x, TABLE_Y0, x, TABLE_Y1)
    for x in (COLUMN_BOUNDARY, COLUMN_BOUNDARY + 0.8):
        _line(output, x, ROW_BOUNDARY, x, TABLE_Y1)
    for y in (TABLE_Y0, ROW_BOUNDARY, TABLE_Y1):
        _line(output, TABLE_X0, y, TABLE_X1, y)
    _text(output, 115, 80, "Ambiguous header")
    _text(output, 65, 130, "A")
    _text(output, 176, 130, "?")
    _text(output, 200, 130, "B")
    _finish(output)


if __name__ == "__main__":
    build_horizontal_merge()
    build_vertical_merge()
    build_ambiguous_merge()
