"""Regenerate the deterministic M5.2 multi-page PDF table fixture.

Run in the locked fixture environment:

    python3 -m pip install \
      -c constraints-pdf-fixtures.txt -e ".[fixtures]"
    python3 tests/fixtures/build_pdf_table_continuation_fixture.py
"""

from pathlib import Path

from reportlab.pdfgen import canvas


OUTPUT = Path(__file__).with_name("pdf_table_continuation.pdf")
PAGE_WIDTH = 400
PAGE_HEIGHT = 300
TABLE_X = (40, 250, 360)
ROW_HEIGHT = 86
HEADER = ("Requirement", "Status")
PAGE_ROWS = (
    (
        ("REQ-CONT-001", "Open"),
        ("REQ-CONT-002", "Closed"),
    ),
    (
        ("REQ-CONT-003", "Open"),
        ("REQ-CONT-004", "Closed"),
    ),
    (
        ("REQ-CONT-005", "Open"),
        ("REQ-CONT-006", "Closed"),
    ),
)
PAGE_TABLE_TOPS = (24, 24, 24)


def _pdf_y(top_left_y: float) -> float:
    return PAGE_HEIGHT - top_left_y


def _draw_table(
    output: canvas.Canvas,
    *,
    top: float,
    rows: tuple[tuple[str, str], ...],
) -> None:
    values = (HEADER, *rows)
    bottom = top + len(values) * ROW_HEIGHT
    output.setLineWidth(1)
    for x in TABLE_X:
        output.line(x, _pdf_y(top), x, _pdf_y(bottom))
    for row_index in range(len(values) + 1):
        y = top + row_index * ROW_HEIGHT
        output.line(TABLE_X[0], _pdf_y(y), TABLE_X[-1], _pdf_y(y))
    for row_index, row in enumerate(values):
        output.setFont(
            "Helvetica-Bold" if row_index == 0 else "Helvetica",
            10,
        )
        baseline = top + row_index * ROW_HEIGHT + 25
        output.drawString(TABLE_X[0] + 8, _pdf_y(baseline), row[0])
        output.drawString(TABLE_X[1] + 8, _pdf_y(baseline), row[1])


def main() -> None:
    output = canvas.Canvas(
        OUTPUT.as_posix(),
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        pageCompression=0,
        invariant=1,
    )
    output.setTitle("SpecTrail multi-page PDF table continuation")
    output.setAuthor("SpecTrail")
    for page_index, rows in enumerate(PAGE_ROWS):
        output.setFont("Helvetica-Bold", 9)
        output.drawString(
            TABLE_X[0],
            _pdf_y(12),
            "Table 1" if page_index == 0 else "Table 1 (continued)",
        )
        _draw_table(
            output,
            top=PAGE_TABLE_TOPS[page_index],
            rows=rows,
        )
        output.showPage()
    output.save()


if __name__ == "__main__":
    main()
