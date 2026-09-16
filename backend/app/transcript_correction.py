"""
Deterministic, curated correction pass for known Sarvam ASR misrecognitions in CCA oncology
consultation transcripts. Runs client-side-triggered (see routers/cca.py's
POST /api/cca/transcript/correct) right after a recording is transcribed and BEFORE the raw
text is shown in the doctor's editable transcript box -- see the three CCA oncologist pages'
stopScribeRecording() for the call site.

Why this exists, and why it's this narrow: Sarvam's saaras:v3 batch API (this app's current ASR
model -- see sarvam_batch_transcriber.py) has no vocabulary/hotword biasing at all. Sarvam does
offer a `keyterms` parameter that biases recognition toward a supplied word list, but ONLY on
model=saaras:v4 (verified against Sarvam's own batch job API reference, docs.sarvam.ai/api-
reference/speech-to-text/stt/job/initiate) -- switching models is a separate, bigger decision
requiring live audio comparison before it could be trusted in production, not something this
module attempts. Given that, real oncology terminology gets misheard as similar-sounding
common English -- every pattern below is a SPECIFIC, empirically-observed confusion (from two
real documented test transcripts, not a guess or a general phonetic/fuzzy matcher):
"HER2 positive" -> "heart is 2 positive", "neoadjuvant" -> "new adjuvant", "axillary" ->
"auxiliary", "radiation oncology" -> "radiation Psychology", "CEA" -> "CDA", "colon" -> "Pollen",
"wound" -> "wool". Grown only from real evidence, the same discipline this codebase already
applies elsewhere (cca_engine.FACT_TYPES's OTHER_CLINICAL_FINDING, drug_matcher.py's dataset-
bound corrections) -- never add a pattern here on a guess.

Deliberately excluded, and why (do not "fix" these by adding a pattern without new evidence):
- "stool" -> "stone": REAL documented case, excluded anyway -- "stone" is itself a legitimate,
  common clinical word (kidney stone, gallstone), so a blind substitution would corrupt a
  transcript that genuinely discusses one.
- "neoadjuvant" -> "systemic replacement": too large a phonetic/structural leap from the
  correct term to safely pattern-match without risking an unrelated false positive.
- "fractions" -> "number of actions", "MDT/tumour board" -> "NDD/Q1 board": both too generic/
  imprecisely-documented a surface form to encode as a safe, specific pattern.
- "Meera ji" -> "Neera ji" (a patient's own name misheard): needs the PATIENT'S OWN registered
  name as the comparison target, not a static dictionary entry (a static "Neera"->"Meera" rule
  would wrongly rewrite a transcript about a different, real patient actually named Neera) --
  a separate, encounter-aware feature, not yet built.
- "Glimepiride 2mg" -> "Tracholine/Achcoline 2mg": the two strings share essentially no real
  phonetic/character overlap -- no text-level correction, curated or fuzzy, can bridge this.
  Only ASR-side vocabulary biasing (ties back to the saaras:v4/keyterms decision above) could
  ever catch a case like this.
- A phrase the ASR dropped entirely, or content it hallucinated/added that was never spoken:
  there is no text to correct in either case -- nothing here can detect an absence or an
  invention, only a substitution.

Every correction is returned ALONGSIDE the corrected text, never applied invisibly -- see
correct_transcript()'s return shape. The doctor's transcript box is freely editable either way,
so a wrong correction costs nothing more than a raw ASR error would (the doctor can just retype
it) PROVIDED they can actually see that a correction happened. Silently baking a correction into
what's supposed to be the verbatim record is the one thing this module must never do: a wrong
correction that reads as fluent, plausible medical text is more dangerous than a visibly garbled
ASR error, because it's more likely to be believed without question.
"""
import re
from typing import Dict, List, Tuple

# (compiled pattern, replacement, human-readable label for the "what changed" list). Patterns
# are case-insensitive and word-boundary-anchored so a match can never land mid-word. Order
# matters only in that each pattern is applied independently against the ORIGINAL text's match
# positions are recomputed after each substitution, so two patterns are never applied to
# overlapping text is not a real concern here -- see _apply_confusions.
_KNOWN_ASR_CONFUSIONS: List[Tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\bheart is 2 (positive|negative)\b", re.IGNORECASE),
        r"HER2 \1",
        "heart is 2 <result> -> HER2 <result>",
    ),
    (
        re.compile(r"\bnew adjuvant\b", re.IGNORECASE),
        "neoadjuvant",
        "new adjuvant -> neoadjuvant",
    ),
    (
        re.compile(r"\bauxiliary\b", re.IGNORECASE),
        "axillary",
        "auxiliary -> axillary",
    ),
    (
        re.compile(r"\bradiation psychology\b", re.IGNORECASE),
        "radiation oncology",
        "radiation psychology -> radiation oncology",
    ),
    (
        re.compile(r"\bCDA\b", re.IGNORECASE),
        "CEA",
        "CDA -> CEA",
    ),
    (
        re.compile(r"\bpollen\b", re.IGNORECASE),
        "colon",
        "pollen -> colon",
    ),
    (
        re.compile(r"\bwool\b", re.IGNORECASE),
        "wound",
        "wool -> wound",
    ),
]


def correct_transcript(text: str) -> Dict:
    """
    Applies every pattern in _KNOWN_ASR_CONFUSIONS to `text` once each, left to right.

    Returns {"corrected_text": str, "corrections": List[{"before": str, "after": str, "label":
    str}]} -- `corrections` lists every individual match actually found and replaced (not just
    which patterns exist), with the exact original and replacement text for that occurrence, so
    a caller can show the doctor precisely what changed. Empty `corrections` (corrected_text ==
    text) is the common, expected case for any transcript that doesn't happen to contain one of
    these specific confusions.
    """
    if not text:
        return {"corrected_text": text, "corrections": []}

    corrected = text
    corrections: List[Dict] = []
    for pattern, replacement, label in _KNOWN_ASR_CONFUSIONS:
        def _record(match: re.Match, _replacement=replacement, _label=label) -> str:
            before = match.group(0)
            after = match.expand(_replacement)
            corrections.append({"before": before, "after": after, "label": _label})
            return after
        corrected = pattern.sub(_record, corrected)

    return {"corrected_text": corrected, "corrections": corrections}
