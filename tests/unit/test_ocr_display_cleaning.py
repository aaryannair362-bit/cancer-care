"""
Unit tests for ocr_service.strip_markup_for_display -- the "Ingested Documents" excerpt cleaner.

Regression coverage for a real, reported bug: Sarvam Document AI's markdown output renders a
table as literal HTML (`<table><tbody><tr><td>...`) rather than markdown pipes, which showed up
verbatim in the Patient History "Ingested Documents" preview instead of readable text.
"""
from backend.app.ocr_service import strip_markup_for_display


def test_strips_html_table_tags():
    raw = (
        "## SIELabs *To Be Filled By Patient / Insured* <table> <tbody> <tr> <td>Name of Patient</td> "
        "<td>A - Paramesh</td> </tr> <tr> <td>Age</td> <td>50 y/m</td> </tr> </tbody> </table>"
    )
    cleaned = strip_markup_for_display(raw)
    assert "<" not in cleaned and ">" not in cleaned
    assert "Name of Patient" in cleaned
    assert "A - Paramesh" in cleaned


def test_strips_markdown_heading_and_emphasis_decoration():
    cleaned = strip_markup_for_display("# Diagnosis\n**Breast carcinoma**, Grade *II*")
    assert "#" not in cleaned
    assert "*" not in cleaned
    assert "Breast carcinoma" in cleaned
    assert "Grade" in cleaned


def test_strips_markdown_table_separator_rows():
    cleaned = strip_markup_for_display("| Test | Result |\n|---|---|\n| Hemoglobin | 11.2 |")
    assert "---" not in cleaned
    assert "Hemoglobin" in cleaned
    assert "11.2" in cleaned


def test_collapses_whitespace():
    cleaned = strip_markup_for_display("Line one\n\n\n   Line   two")
    assert cleaned == "Line one Line two"


def test_empty_and_none_input_returns_empty_string():
    assert strip_markup_for_display("") == ""
    assert strip_markup_for_display(None) == ""


def test_plain_text_is_left_essentially_unchanged():
    assert strip_markup_for_display("Diagnosis: Breast carcinoma, Grade II") == "Diagnosis: Breast carcinoma, Grade II"
