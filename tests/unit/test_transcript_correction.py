"""
Unit tests for transcript_correction.correct_transcript -- the curated, narrow ASR-
misrecognition correction pass for CCA oncology consultation transcripts.
"""
import pytest

from backend.app.transcript_correction import correct_transcript


def test_no_known_confusions_returns_text_unchanged():
    text = "Patient reports mild fatigue and reduced appetite for two weeks."
    result = correct_transcript(text)
    assert result["corrected_text"] == text
    assert result["corrections"] == []


def test_empty_transcript_returns_unchanged():
    result = correct_transcript("")
    assert result["corrected_text"] == ""
    assert result["corrections"] == []


@pytest.mark.parametrize("raw,expected", [
    ("the report shows heart is 2 positive on IHC", "the report shows HER2 positive on IHC"),
    ("heart is 2 negative", "HER2 negative"),
    ("HEART IS 2 POSITIVE", "HER2 POSITIVE"),  # case-insensitive match, replacement is fixed-case
])
def test_her2_confusion_corrected(raw, expected):
    result = correct_transcript(raw)
    assert result["corrected_text"] == expected
    assert len(result["corrections"]) == 1
    assert result["corrections"][0]["after"].upper().startswith("HER2")


def test_neoadjuvant_confusion_corrected():
    result = correct_transcript("planning new adjuvant chemotherapy before surgery")
    assert result["corrected_text"] == "planning neoadjuvant chemotherapy before surgery"
    assert result["corrections"] == [{
        "before": "new adjuvant", "after": "neoadjuvant", "label": "new adjuvant -> neoadjuvant",
    }]


def test_axillary_confusion_corrected():
    result = correct_transcript("auxiliary lymph node dissection was performed")
    assert result["corrected_text"] == "axillary lymph node dissection was performed"


def test_axillary_does_not_touch_a_different_word_sharing_the_prefix():
    """Word-boundary anchored -- "auxiliaries" is a different word entirely and must never be
    partially rewritten into nonsense."""
    text = "the nursing auxiliaries assisted during the procedure"
    result = correct_transcript(text)
    assert result["corrected_text"] == text
    assert result["corrections"] == []


def test_radiation_psychology_confusion_corrected():
    result = correct_transcript("referred to radiation psychology for planning")
    assert result["corrected_text"] == "referred to radiation oncology for planning"


def test_radiation_psychology_does_not_touch_bare_psychology():
    """A real, unrelated mention of psycho-oncology/psychology support must never be rewritten --
    only the specific "radiation psychology" phrase is a known confusion."""
    text = "also referred to psychology for coping support"
    result = correct_transcript(text)
    assert result["corrected_text"] == text
    assert result["corrections"] == []


def test_cda_confusion_corrected():
    result = correct_transcript("CDA level was elevated at 12")
    assert result["corrected_text"] == "CEA level was elevated at 12"


def test_pollen_confusion_corrected():
    result = correct_transcript("mass noted in the pollen on imaging")
    assert result["corrected_text"] == "mass noted in the colon on imaging"


def test_wool_confusion_corrected():
    result = correct_transcript("the wool healed well post-operatively")
    assert result["corrected_text"] == "the wound healed well post-operatively"


def test_multiple_confusions_all_corrected_and_all_listed():
    text = "heart is 2 positive, new adjuvant therapy planned, auxiliary nodes clear"
    result = correct_transcript(text)
    assert result["corrected_text"] == "HER2 positive, neoadjuvant therapy planned, axillary nodes clear"
    assert len(result["corrections"]) == 3
    labels = {c["label"] for c in result["corrections"]}
    assert labels == {
        "heart is 2 <result> -> HER2 <result>",
        "new adjuvant -> neoadjuvant",
        "auxiliary -> axillary",
    }


def test_known_excluded_case_stone_for_stool_is_never_touched():
    """Deliberately excluded per the module's own docstring -- "stone" is a legitimate clinical
    word (kidney stone, gallstone) and must never be blindly rewritten to "stool"."""
    text = "patient reports a kidney stone last year"
    result = correct_transcript(text)
    assert result["corrected_text"] == text
    assert result["corrections"] == []


def test_known_excluded_case_medication_name_is_never_touched():
    """Deliberately excluded -- no text-level correction can bridge a phonetically-unrelated
    medication misrecognition like this one (see module docstring)."""
    text = "prescribed Tracholine 2mg once daily"
    result = correct_transcript(text)
    assert result["corrected_text"] == text
    assert result["corrections"] == []
