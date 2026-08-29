"""
Generates fictional, one-page "Field: Value" source documents for CCA QA scenarios --
matching the exact style of the user-supplied CCA_Demo_Case_Maya_Iyer_All_In_One.pdf source
documents (page 40 onward of that pack) and the seeded demo documents already used elsewhere
in this app. Deliberately plain (title + disclaimer callout + a field/value table) rather than
reusing generate_master_pdf.py's full multi-section report styling -- these are meant to look
like real single-purpose clinical documents (a referral letter, a pathology report), not a
specification document.

Usage: python tools/generate_scenario_docs.py
Writes PDFs to data/scenarios/<scenario_key>/*.pdf per the SCENARIOS data below.
"""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C_PRIMARY = colors.HexColor("#1b4332")
C_MUTED = colors.HexColor("#525e57")
C_BORDER = colors.HexColor("#d8dedb")
C_CALLOUT_BG = colors.HexColor("#fff8e6")
C_CALLOUT_BORDER = colors.HexColor("#e0b84a")
C_TABLE_HEADER_BG = colors.HexColor("#1b4332")

TITLE_STYLE = ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=C_PRIMARY, spaceAfter=4)
SUBTITLE_STYLE = ParagraphStyle("Subtitle", fontName="Helvetica", fontSize=10, leading=13, textColor=C_MUTED, spaceAfter=12)
CALLOUT_STYLE = ParagraphStyle("Callout", fontName="Helvetica", fontSize=8.5, leading=12, textColor=colors.HexColor("#6b5000"))
CELL_STYLE = ParagraphStyle("Cell", fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#1C2621"))
HEADER_CELL_STYLE = ParagraphStyle("HeaderCell", fontName="Helvetica-Bold", fontSize=9, leading=13, textColor=colors.white)


def _build_pdf(path: str, title: str, subtitle: str, rows: list[tuple[str, str]]):
    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    story = [
        Paragraph(title, TITLE_STYLE),
        Paragraph(subtitle, SUBTITLE_STYLE),
    ]

    callout_table = Table(
        [[Paragraph(
            "<b>Fictional source document.</b> This PDF is a test input for document ingestion, "
            "extraction, classification, provenance and verification. It is not a real medical "
            "record and must never be treated as one.", CALLOUT_STYLE
        )]],
        colWidths=[7.0 * inch],
    )
    callout_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 1, C_CALLOUT_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(callout_table)
    story.append(Spacer(1, 14))

    table_data = [[Paragraph("Field", HEADER_CELL_STYLE), Paragraph("Value", HEADER_CELL_STYLE)]]
    for field, value in rows:
        table_data.append([Paragraph(field, CELL_STYLE), Paragraph(value, CELL_STYLE)])
    field_table = Table(table_data, colWidths=[1.8 * inch, 5.2 * inch])
    field_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_TABLE_HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.75, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8faf9")]),
    ]))
    story.append(field_table)
    doc.build(story)


def generate_scenario(scenario_key: str, documents: list[dict]):
    out_dir = os.path.join(REPO_ROOT, "data", "scenarios", scenario_key)
    os.makedirs(out_dir, exist_ok=True)
    for doc in documents:
        path = os.path.join(out_dir, doc["filename"])
        _build_pdf(path, doc["title"], doc["subtitle"], doc["rows"])
        print(f"  wrote {path}")


if __name__ == "__main__":
    from scenario_data import SCENARIOS
    for key, documents in SCENARIOS.items():
        print(f"Scenario: {key}")
        generate_scenario(key, documents)
