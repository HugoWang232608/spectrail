"""Regenerate the deterministic PDF table fixture used by M5 acceptance tests.

Run in the fixture-generation environment, which includes ReportLab:

    python3 tests/fixtures/build_pdf_table_fixture.py
"""

from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas


OUTPUT = Path(__file__).with_name("pdf_table_requirements.pdf")
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
TABLE_LEFT = 72
TABLE_TOP = 650
ROW_HEIGHT = 38
COLUMN_WIDTHS = (120, 270, 78)
ROWS = (
    ("Requirement ID", "Acceptance criterion", "Owner"),
    ("REQ-001", "Approved within 2 seconds", "Safety"),
    ("REQ-002", "Audit source evidence", "QA"),
)


def main() -> None:
    canvas = Canvas(
        OUTPUT.as_posix(),
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        invariant=1,
        pageCompression=1,
    )
    canvas.setTitle("SpecTrail PDF table evidence fixture")
    canvas.setAuthor("SpecTrail")
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(72, 720, "M5 PDF Table Evidence")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(
        72,
        690,
        "The table below is the canonical structured evidence fixture.",
    )

    table_bottom = TABLE_TOP - len(ROWS) * ROW_HEIGHT
    x_positions = [TABLE_LEFT]
    for width in COLUMN_WIDTHS:
        x_positions.append(x_positions[-1] + width)
    y_positions = [
        TABLE_TOP - row_index * ROW_HEIGHT
        for row_index in range(len(ROWS) + 1)
    ]

    canvas.setLineWidth(1)
    for x in x_positions:
        canvas.line(x, table_bottom, x, TABLE_TOP)
    for y in y_positions:
        canvas.line(TABLE_LEFT, y, x_positions[-1], y)

    for row_index, row in enumerate(ROWS):
        font_name = "Helvetica-Bold" if row_index == 0 else "Helvetica"
        canvas.setFont(font_name, 10)
        baseline = TABLE_TOP - row_index * ROW_HEIGHT - 24
        for column_index, value in enumerate(row):
            available = COLUMN_WIDTHS[column_index] - 16
            if stringWidth(value, font_name, 10) > available:
                raise ValueError(f"fixture cell text is too wide: {value}")
            canvas.drawString(x_positions[column_index] + 8, baseline, value)

    canvas.setFont("Helvetica", 10)
    canvas.drawString(
        72,
        table_bottom - 34,
        "The system shall retain the selected PDF table cells.",
    )
    canvas.showPage()
    canvas.save()


if __name__ == "__main__":
    main()
