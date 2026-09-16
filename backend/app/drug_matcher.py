"""
Fuzzy-corrects AI-extracted medication names against a canonical Indian medicines database
(backend/app/data/medicine_names.csv -- ~249k unique brand/generic product names, derived from
a user-supplied "A-Z medicines dataset of India" export; only the `name` column is kept, since
that's all this feature uses).

Why this exists: the voice -> LLM extraction pipeline (scribe.scribe_transcript) misspells
brand names a meaningful fraction of the time (e.g. "Zanocine" for "Zanocin", "Calpoll" for
"Calpol") -- ASR/LLM noise, not a data-entry error a doctor would catch by eye on a short
drug name they already trust. This module looks up the closest real product name and swaps it
in before the prescription is ever shown to the doctor, the same way a pharmacy system's
autocomplete would.

When a drugName states an explicit form/route ("Tablet Zanocin", "Tab Aceclofenac" -- the
convention this app's own OPD pipeline actually uses; see tests/from_data/fixtures.py),
correction never crosses to a different form than the one stated, and requires a high
same-form match score (see _find_correction's docstring for the two real bug classes --
dangerous form/route swaps, and look-alike-sound-alike (LASA) brand collisions -- this
conservatism exists to avoid, both found live during development).

Bare names (no stated form, e.g. "Diclofenac", "Ofloxil") go through a SEPARATE, deliberately
unguarded path (_find_bare_name_correction): always take the single highest-scoring dataset
match above a low noise floor, no confidence threshold. This is a conscious product decision,
not an oversight -- accepted after being shown concretely that it CAN reopen a LASA collision
risk on common drugs not covered by the reseller-prefix fix below (e.g. "Ofloxil" is a genuine
85.7-score tie between two unrelated real brands, "Ofloxin" and "Floxsil" -- see
test_ofloxil_resolves_to_floxsil_not_ofloxin_a_genuine_tie). Chosen anyway because catching
real typos on unguarded bare names (e.g. Whisper mis-transcribing "Ofloxin" as "Ofloxil") was
judged worth more than that residual collision risk for this app's use case. (An earlier version
of this risk analysis also cited "Diclofenac" colliding with "Dicofenac Injection" at 94.7 vs
only 74.1 for the correct "Diclofenac Sodium Injection" -- that specific case is now fixed: see
_RESELLER_RE, which strips the "StayHappi"/"DavaIndia"/"Genericart" reseller-house prefix that
was diluting the correct entry's score, so "Diclofenac" now matches its own real dataset entry
at a perfect 100 instead.) If the remaining tradeoff ever needs revisiting, the fix is a curated
LASA confusion list (out of scope here), not a threshold tweak -- see _find_correction's
docstring for why no threshold can separate the two classes.

Matching strategy: dataset names embed strength/form in the string itself
("Zanocin 200 Tablet", "Calpol 500mg Tablet"), which would dominate a naive full-string fuzzy
score far more than the brand name itself does (verified empirically -- see the two rejected
approaches below). So both the query and every candidate are reduced to a "base" (brand-name-
only, dose/form words stripped) before scoring, and the ORIGINAL full canonical name is what
gets substituted back in -- never the stripped base -- so the output still carries a real
strength/form, not a bare brand name.

Two approaches were tried and rejected before this one (see the conversation this shipped in
for the actual benchmark numbers, not repeated here):
  1. `rapidfuzz.fuzz.WRatio` / `token_set_ratio` against the full (unstripped) name strings --
     ~250-400ms per query against all ~249k candidates (too slow to run per-drug on every
     prescription), and prone to high-confidence WRONG matches: "Electral Powder" scored 85.5
     against "Abitate 250mg Powder for Injection" purely because both strings share "Powder"
     and similar overall length -- nothing to do with the actual drug.
  2. Stripped bases with `fuzz.WRatio` -- still slow (WRatio computes several sub-metrics per
     comparison) for no accuracy gain over plain `fuzz.ratio` once the dose/form noise is
     already removed.
The shipped approach (stripped bases + `fuzz.ratio`) benchmarked at ~15ms per query and did not
reproduce that false-positive class in the same test set.
"""
import re
import threading
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process
from metaphone import doublemetaphone

DATA_PATH = Path(__file__).resolve().parent / "data" / "medicine_names.csv"
CUSTOM_DATA_PATH = Path(__file__).resolve().parent / "data" / "custom_medicines.csv"

# Below this score (0-100, rapidfuzz's `fuzz.ratio` scale, computed on the stripped "base" --
# see module docstring), among same-form candidates (see _find_correction), the closest dataset
# entry is judged more likely to be a different real product sharing that form than a
# misspelling of the query, so the original extracted name is left untouched. Chosen
# empirically against a mixed test set of real typos with a form stated (Tablet Zanocine/Tablet
# Calpoll/Tablet Paracetmol, all scored 92-95 and were correctly fixed) versus a missing-from-
# dataset query (Electral Powder, scored 87.5 against a wrong product and must NOT be
# "corrected" into one) -- 92 sits in the gap. Not a universal constant; a different/larger
# dataset would need this re-validated the same way.
DEFAULT_MATCH_THRESHOLD = 92.0

# Floor for the form-gated phonetic-rescue pass in _find_correction: a candidate scoring below
# `threshold` on spelling (fuzz.ratio) alone is only rescued if it ALSO shares a double-
# metaphone code with the query -- this is the floor for even being considered for that second
# check, not a threshold in its own right (matches BARE_NAME_NOISE_FLOOR's reasoning: below
# this, a candidate is noise regardless of what else agrees with it). Verified live: real
# ASR-drift cases this rescues -- "Panthoprazole"/"Rebeprazol"/"Montilucast"/"Ofloxasin" for
# Pantoprazole/Rabeprazole/Montelukast/Ofloxacin -- score 78-89 on fuzz.ratio alone (below
# `threshold`) but have an EXACT double-metaphone code match with the correct entry. Checked
# against every existing LASA-collision/false-positive regression case in this test suite
# (Diclofenac/Dicofenac, Azithromycin/Zithromycin, Electral Powder, the form-swap cases) --
# none of them share a phonetic code with their wrong near-match, so this pass doesn't
# reintroduce them. Chloroquine/Chloroquin is the one documented exception: they share both a
# high fuzz.ratio AND an identical phonetic code (they're genuinely near-homophones), so
# phonetic agreement cannot and does not resolve that specific pair -- it was never claimed to
# solve LASA collisions in general, only to safely recover cases where the SPELLING drifted
# further than typo-distance but the SOUND didn't drift at all.
PHONETIC_RESCUE_FLOOR = 80.0

# A stripped base shorter than this is almost certainly a stray fragment (dataset entries that
# are mostly/entirely dose+form words) rather than a real brand name, and short strings produce
# unreliably high `fuzz.ratio` scores against other short strings by chance alone -- excluded
# from the candidate pool entirely rather than risking a confident match to a fragment.
MIN_BASE_LENGTH = 2

_FORM_WORDS = (
    r"tablets?|capsules?|syrups?|injections?|creams?|ointments?|powders?|gels?|drops?|"
    r"suspensions?|solutions?|liquids?|sprays?|inhalers?|sachets?|patches?|soaps?|shampoos?|"
    r"lozenges?|granules?|kits?|strips?|suppositor(?:y|ies)|chewable|effervescent|paediatric|"
    r"redi|mouthwash|toothpaste|lotions?|elixir|gargle|infant|infants|oral|nasal|eye|ear|topical|"
    # Standard Indian Rx shorthand ("Tab X", "Cap X", "Inj X" -- this app's OWN OPD pipeline's
    # actual real-world convention, per tests/from_data/fixtures.py and this module's own
    # docstrings) -- verified live these were NEVER actually matched by the words above ("tab"
    # is not "tablets?"), so every abbreviated-form medication was silently treated as a BARE
    # name and fell through to the unguarded bare-name path instead of the form-gated,
    # same-form-only one. That's not a cosmetic gap: verified live, "Tab Vitamin C 500 mg"
    # matched a real "Vitamin C Injection" dataset entry through the unguarded path -- a
    # silent oral-to-injectable route swap, exactly the danger class the form gate exists to
    # prevent, on what is this app's DOMINANT real prescribing format. See _FORM_ALIASES for
    # how these normalize against the dataset's own full-word form labels.
    r"tabs?|caps?|inj|syr|syp|susp|oint|sol|soln|drp|amp|supp"
)
# Maps every literal form word/abbreviation _FORM_RE can match to one canonical label, so a
# query's abbreviated "Tab"/"Cap"/"Inj" is recognized as the SAME form as a dataset entry's
# spelled-out "Tablet"/"Capsule"/"Injection" -- without this, adding the abbreviations above to
# _FORM_WORDS would be useless: _find_correction's `forms[i] == query_form` comparison is a
# literal string match, and "tab" != "tablet" even though they mean the same thing.
_FORM_ALIASES = {
    "tab": "tablet", "tabs": "tablet",
    "cap": "capsule", "caps": "capsule",
    "inj": "injection",
    "syr": "syrup", "syp": "syrup",
    "susp": "suspension",
    "oint": "ointment",
    "sol": "solution", "soln": "solution",
    "drp": "drops",
    "amp": "injection",
    "supp": "suppository", "suppository": "suppository", "suppositories": "suppository",
}


def _canonical_form(matched_text: str) -> str:
    """Normalizes a raw _FORM_RE match (whatever literal word/abbreviation it found) to one
    canonical per-form-family label via _FORM_ALIASES, falling back to the lowercased match
    itself for words with no alias entry (e.g. "gel", "spray" -- already canonical as-is)."""
    lowered = matched_text.lower()
    return _FORM_ALIASES.get(lowered, lowered)


# Deliberately NOT stripped, unlike the packaging words above: "Plus"/"Forte" mark a genuinely
# different combination formulation in Indian pharma brand naming (e.g. "Calpol" plain
# paracetamol vs "Calpol Plus" a different active-ingredient combination), and "ER/SR/CR/XR/
# DT/OD/LA"-type suffixes mark a real release-mechanism difference (extended- vs immediate-
# release) with actual dosing/safety consequences -- collapsing either into the same "base" as
# the plain product caused exactly this kind of wrong match during testing (a misspelled
# "Calpoll" was "corrected" into "Calpol Plus", a different formulation) and was removed for
# that reason, not stripped by omission.
_DOSE_RE = re.compile(r"\d+(\.\d+)?\s*(mg|gm|g|ml|mcg|iu|%|w/w|w/v|/ml|/5ml)?", re.IGNORECASE)
_FORM_RE = re.compile(rf"\b(?:{_FORM_WORDS})\b", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s-]")
_WS_RE = re.compile(r"\s+")

# Generic-medicine reseller chains that prefix EVERY one of their SKUs with their own house
# name in this dataset (e.g. "DavaIndia Pantoprazole 40mg Tablet", "StayHappi Rabeprazole
# 40mg Tablet") -- verified directly against medicine_names.csv: for common plain generics
# (Pantoprazole, Azithromycin, Levocetirizine, Rabeprazole, ...) EVERY tablet-form dataset
# entry comes from one of these three resellers or is a multi-drug combination product; there
# is no bare "Pantoprazole Tablet" entry at all. Left unstripped, that house-name token bloats
# the base string's length, and fuzz.ratio is length-normalized -- so a query like "Tablet
# Panthoprazole" (ASR drift, not even a bad typo) scored the correct "DavaIndia Pantoprazole
# 40mg Tablet" so low it didn't even place in the top-5 nearest bases; an unrelated short
# brand name won instead. Stripping the reseller name here (verified live: fixes Panthoprazole
# -> Pantoprazole, Azithromycine -> Azithromycin, Levocetrizine -> Levocetirizine, with zero
# change to any existing positive/negative test case) restores the intended "brand/generic name
# only" comparison this function's docstring already promises.
_RESELLER_RE = re.compile(r"\b(?:stayhappi|davaindia|genericart)\b", re.IGNORECASE)
# Matches _RESELLER_RE plus any whitespace immediately after it, so stripping from a display
# string ("StayHappi Aceclofenac 100mg Tablet") doesn't leave a leading space behind.
_RESELLER_PREFIX_RE = re.compile(r"^(?:stayhappi|davaindia|genericart)\s+", re.IGNORECASE)


def _for_display(full_name: str) -> str:
    """
    Strips a reseller house-name prefix (see _RESELLER_RE) from a dataset name right before
    it's substituted into a doctor-facing prescription. _strip_to_base already ignores this
    prefix for MATCHING purposes, but until this existed the matched candidate's raw,
    unstripped name (reseller prefix included) was substituted back in verbatim -- so a
    correctly-spelled "Aceclofenac" or a fixed typo would come back as "StayHappi Aceclofenac
    100mg Tablet", silently attributing the prescription to a specific retail pharmacy chain's
    SKU that the doctor never said and that has no clinical meaning. This only ever removes a
    LEADING reseller name (dataset entries never have one mid-string), leaving the real
    strength/form information -- the whole reason the full name is substituted instead of the
    bare base -- untouched.
    """
    return _RESELLER_PREFIX_RE.sub("", full_name)


def _strip_to_base(name: str) -> str:
    """Reduces a medicine name to just its brand/generic-name tokens: strips dose numbers+units,
    common form/pack words, and known reseller house-name prefixes, drops punctuation,
    lowercases, collapses whitespace."""
    s = _RESELLER_RE.sub(" ", name)
    s = _DOSE_RE.sub(" ", s)
    s = _FORM_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip().lower()


_load_lock = threading.Lock()
_full_names: Optional[list] = None
_bases: Optional[list] = None
_forms: Optional[list] = None
_phonetics: Optional[list] = None


def _read_names(path: Path) -> list:
    if not path.exists():
        return []
    import csv
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        return [(row.get("name") or "").strip() for row in csv.DictReader(f)]


def _load() -> tuple:
    """Lazily loads and indexes the dataset (primary + any admin-added custom entries) on
    first use, cached for the process lifetime. Never raises: a missing/unreadable dataset
    file just means every lookup finds nothing, same as the "no confident match" case --
    callers keep whatever name they already had. Precomputes each entry's form word
    (Tablet/Syrup/Gel/...), if any, alongside its stripped base -- see the hard form
    constraint in _find_correction for why this matters -- and its double-metaphone phonetic
    codes, used by the phonetic-rescue path (see PHONETIC_RESCUE_FLOOR)."""
    global _full_names, _bases, _forms, _phonetics
    if _full_names is not None:
        return _full_names, _bases, _forms, _phonetics
    with _load_lock:
        if _full_names is not None:  # another thread may have finished loading while we waited
            return _full_names, _bases, _forms, _phonetics
        _rebuild()
    return _full_names, _bases, _forms, _phonetics


def _rebuild() -> None:
    """(Re)builds the in-memory index from disk. Called on first use, and again by
    invalidate_cache() after an admin adds a custom medicine, so the new entry is matchable
    immediately without restarting the server."""
    global _full_names, _bases, _forms, _phonetics
    full_names, bases, forms, phonetics = [], [], [], []
    try:
        names = _read_names(DATA_PATH) + _read_names(CUSTOM_DATA_PATH)
    except OSError:
        names = []
    for name in names:
        if not name:
            continue
        base = _strip_to_base(name)
        if len(base) < MIN_BASE_LENGTH:
            continue
        form_match = _FORM_RE.search(name)
        full_names.append(name)
        bases.append(base)
        forms.append(_canonical_form(form_match.group(0)) if form_match else None)
        # doublemetaphone() on ~249k names costs ~5s, paid once at first lookup (or on an admin
        # custom-medicine add, which is rare), not per query -- see PHONETIC_RESCUE_FLOOR for
        # why this is worth precomputing rather than computed lazily per candidate per query.
        phonetics.append(doublemetaphone(base))
    _full_names, _bases, _forms, _phonetics = full_names, bases, forms, phonetics


def invalidate_cache() -> None:
    """Forces the next lookup to reload from disk -- call after writing a new row to
    custom_medicines.csv so an admin-added medicine is usable in the same process without a
    restart."""
    global _full_names, _bases, _forms, _phonetics
    with _load_lock:
        _full_names = _bases = _forms = _phonetics = None


def _phonetic_agrees(query_codes: tuple, candidate_codes: tuple) -> bool:
    """True if the query and a candidate share a non-empty double-metaphone code (primary or
    secondary, in either combination) -- independent, different-mechanism evidence they're the
    same SPOKEN word, not just similarly spelled (see PHONETIC_RESCUE_FLOOR). Empty-string
    codes (very short/degenerate bases produce these) never count as a match against each
    other, or two unrelated short bases would trivially "agree" on phonetic grounds."""
    q = {c for c in query_codes if c}
    c = {c for c in candidate_codes if c}
    return bool(q & c)


# How close two candidates' scores must be to count as "tied" for dose/form disambiguation
# purposes, rather than one simply being the clear winner. fuzz.ratio scores on the same base
# string are exactly equal for genuine ties (e.g. every "Zanocin ..." variant scores identically
# against query base "zanocin"), so this only exists as a defensive float-comparison margin.
_TIE_MARGIN = 0.5
_DOSE_NUMBER_RE = re.compile(r"\d+(\.\d+)?")


def _extract_dose_number(text) -> Optional[str]:
    if not text or not isinstance(text, str):
        return None
    m = _DOSE_NUMBER_RE.search(text)
    return m.group(0) if m else None


def _dose_number_appears(dose_number: str, text: str) -> bool:
    """
    True if `dose_number` (e.g. "5", "500", "0.5") appears in `text` as a standalone number,
    not as part of a longer one. NOT just a digit-adjacency check: `(?<!\\d)` alone still
    treats "5" as "standalone" inside "0.5" (the character immediately before is ".", not a
    digit) -- verified live, this exact gap let "Tab Sorbitrate 5 mg" (an angina medication)
    match a real "...0.5mg..." dataset entry, a 10x dosing error, right after the digit-
    adjacency check was added specifically to prevent silent dose changes. Excluding "." on
    both sides too closes that: "5" no longer falsely matches inside "0.5" or "5.5".
    """
    return bool(re.search(rf"(?<![\d.]){re.escape(dose_number)}(?![\d.])", text))


def _adds_unstated_combination_ingredient(query_name: str, candidate_full_name: str) -> bool:
    """
    True if the candidate is a multi-active-ingredient combination product (dataset names
    join actives with "+", e.g. "Gabapentin+Methylcobalamin") but the query itself never
    indicated more than one ingredient -- accepting it would silently add a whole extra drug
    to the patient's medication list, not correct a spelling. Verified live as a real, active
    bug: plain "Methylcobalamin" (a vitamin) matched a real "Gabapentin+Methylcobalamin"
    combination product at the bare-name noise floor, silently adding Gabapentin -- a
    prescription anticonvulsant nobody prescribed -- to the prescription. A query that's
    already itself stated as a combination ("X + Y") is unaffected; this only protects
    single-ingredient queries.
    """
    return "+" in candidate_full_name and "+" not in query_name


# Matches "Vitamin" followed immediately (after optional whitespace) by exactly one letter and
# optional digits, then a real word boundary -- i.e. a vitamin letter/number code (A, B6, C, D3,
# K2, ...), never a following form word: "Vitamin Syrup"/"Vitamin Tablet" can never match here,
# because the single captured letter ("s"/"t") is immediately followed by more letters with no
# boundary in between (see _mismatched_vitamin_letter's docstring for why this matters).
_VITAMIN_LETTER_RE = re.compile(r"\bvitamin\s*([a-z]\d*)\b", re.IGNORECASE)


def _mismatched_vitamin_letter(query_name: str, candidate_full_name: str) -> bool:
    """
    True if both the query and a candidate name a "Vitamin <letter>" product (A/B6/C/D3/...)
    but state a DIFFERENT letter/number code -- these are chemically unrelated substances, not
    spelling variants of each other, so no fuzz.ratio score however high should ever justify
    substituting one for another.

    Verified live as a real bug, not hypothetical: a correctly-spelled bare "Vitamin D3" query
    (no real "Vitamin D3" dataset entry exists -- the only dataset row containing that string is
    a StayHappi "Atorvastatin+Vitamin D3" COMBINATION product, already excluded separately by
    _adds_unstated_combination_ingredient) fuzzy-matched "Vitamin A 100000IU Syrup" via the
    unguarded bare-name path (_find_bare_name_correction) -- the shared "vitamin " prefix alone
    was enough to clear BARE_NAME_NOISE_FLOOR, silently substituting a chemically unrelated
    vitamin into the patient's medication list. The dataset also has a "Vitamin B6" and a
    "Vitamin C" entry, so the same collision risk exists for any vitamin letter, not just D3/A.

    Same shape of protection as _adds_unstated_combination_ingredient above: a hard pre-filter,
    not a threshold tweak -- per this module's own extensively-documented reasoning, no
    fuzz.ratio threshold can distinguish "genuine typo" from "coincidentally similar unrelated
    product" once similarity is this high (the two strings differ only in the single letter/
    digit right after "vitamin "). Operates on the RAW (unstripped) name strings, not the
    stripped "base" _find_correction/_find_bare_name_correction score against -- _strip_to_base's
    dose-number regex would otherwise eat the distinguishing digit out of "D3"/"B6" before this
    check ever saw it.
    """
    q = _VITAMIN_LETTER_RE.search(query_name)
    c = _VITAMIN_LETTER_RE.search(candidate_full_name)
    if not q or not c:
        return False
    return q.group(1).lower() != c.group(1).lower()


def _disambiguate_tied_candidates(tied: list, full_names: list, drug_name: str, dose) -> str:
    """
    Among dataset entries that tied on base-name similarity (and, if the query stated a form,
    already all share that form -- see _find_correction), prefers whichever one's full name
    actually contains the same dose number the medication was recorded with -- so "Tablet
    Zanocin" prescribed at "200 mg" resolves to "Zanocin 200 Tablet", not an arbitrary
    same-brand different-strength tablet variant that would silently contradict the
    medication's own dose field. Falls back to the first tied entry (dataset order) if the dose
    doesn't narrow it down either.
    """
    dose_number = _extract_dose_number(dose) or _extract_dose_number(drug_name)

    def _preference_score(candidate) -> int:
        _, _, index = candidate
        name_lower = full_names[index].lower()
        # Not a `\b...\b` word-boundary match: "500" and "mg" in "500mg" are both `\w`
        # characters with no boundary between them, so `\b500\b` would never match inside a
        # dataset name like "Calpol 500mg Tablet" -- checked live, this silently picked the
        # wrong strength (1000mg) before being caught. _dose_number_appears is digit- AND
        # decimal-point-adjacency-safe (plain digit-adjacency alone still matches "5" inside
        # "0.5" -- verified live as a real 10x dosing error before that helper existed).
        if dose_number and _dose_number_appears(dose_number, name_lower):
            return 1
        return 0

    best = max(tied, key=_preference_score)
    _, _, index = best
    return _for_display(full_names[index])


def _find_correction(drug_name: str, dose=None, threshold: float = DEFAULT_MATCH_THRESHOLD) -> Optional[str]:
    """
    Shared implementation behind both closest_medicine_name and correct_medication_names.

    Only attempts a correction at all when `drug_name` states an explicit pharmaceutical
    form/route word (Tablet/Syrup/Injection/Gel/Inhaler/...) -- and even then, only ever
    considers dataset entries stating that SAME form, never substituted with a different one no
    matter how high the brand-name similarity scores. A bare generic/brand name with no stated
    form (e.g. "Diclofenac", "Aspirin", "Metformin", "Azithromycin") is left untouched entirely.

    This is deliberately more conservative than "fix anything that scores high enough," and two
    real, distinct bug classes found live during development are why:
      1. Form/route swaps: brand-name-only similarity matched "Diclofenac Gel" (correctly
         spelled) to "Dicofenac Injection" (gel -> injection), "Antacid Syrup"/"Antacid
         Suspension" to "Antacid ... Tablet", "Salbutamol Inhaler" to a tablet product -- none
         of these are spelling corrections, they're silent, dangerous route changes.
      2. Look-alike-sound-alike (LASA) brand collisions on bare generic names: "Diclofenac"
         (correctly spelled) vs the real, different brand "Dicofenac" scores 94.7 on
         fuzz.ratio; "Azithromycin" vs the real, different brand "Zithromycin" scores 95.7;
         "Chloroquine" vs "Chloroquin" scores 95.2. These sit in the SAME score band as
         genuine, wanted typo fixes ("Zanocine"->"Zanocin" scores 93.3, "Calpoll"->"Calpol"
         scores 92.3) -- verified directly, there is no threshold that separates the two
         classes, because pure string similarity cannot distinguish "the query is misspelled"
         from "a different real product happens to be similarly spelled" (a well-known hard
         problem in pharmacy safety -- production systems solve it with curated LASA
         confusion lists, which is out of scope here). Requiring a stated form/route
         eliminates this whole risk category rather than trying to threshold around it: in
         practice, real prescriptions from this app's own OPD pipeline consistently state a
         form ("Tab X", "Tablet X" -- see tests/from_data/fixtures.py), so this still covers
         the dominant real-world case without gambling on bare generic names.

    Below `threshold`, one more path runs before giving up: PHONETIC_RESCUE_FLOOR (see its own
    docstring) -- catches ASR-style mishearings (e.g. "Panthoprazole", "Rebeprazol",
    "Montilucast" for Pantoprazole/Rabeprazole/Montelukast) that plain fuzz.ratio scores too low
    to trust on spelling alone, but whose double-metaphone phonetic code is IDENTICAL to the
    query's -- independent, different-mechanism corroboration that lets these be accepted
    without lowering the spelling-only bar (which would reopen the LASA risk above).
    """
    if not drug_name or not isinstance(drug_name, str):
        return None
    query_base = _strip_to_base(drug_name)
    if len(query_base) < MIN_BASE_LENGTH:
        return None

    form_match = _FORM_RE.search(drug_name)
    query_form = _canonical_form(form_match.group(0)) if form_match else None
    if not query_form:
        return None

    full_names, bases, forms, phonetics = _load()
    if not bases:
        return None

    eligible = [i for i, f in enumerate(forms) if f == query_form]
    if not eligible:
        return None
    subset_bases = [bases[i] for i in eligible]
    # score_cutoff is min(threshold, PHONETIC_RESCUE_FLOOR), not just `threshold` -- candidates
    # between the two are still fetched so the phonetic-rescue pass below has something to
    # check (they're only ACCEPTED there if phonetics also agree, so this doesn't by itself
    # loosen anything); min(...) rather than the floor outright so a caller passing a custom
    # threshold BELOW the floor (e.g. tests probing threshold=1) still gets candidates that low
    # instead of the floor silently overriding it.
    raw = process.extract(query_base, subset_bases, scorer=fuzz.ratio,
                           score_cutoff=min(threshold, PHONETIC_RESCUE_FLOOR), limit=50)
    matches = [(text, score, eligible[local_index]) for text, score, local_index in raw]
    if not matches:
        return None

    # Safety filter BEFORE any score-based selection -- a candidate that would silently add an
    # unstated combination ingredient is never eligible, regardless of how high it scores on
    # spelling alone (see _adds_unstated_combination_ingredient's docstring). Dose safety is
    # handled differently, in _disambiguate_tied_candidates below, not here: same-drug
    # different-strength dataset entries always score IDENTICALLY (dose is stripped before
    # scoring), so they're always genuine ties, never a "best match has the wrong dose, fall
    # back to a worse-but-safer one" situation -- a hard pre-filter here was tried and reverted
    # after it caused exactly that failure mode live ("Aspirin" prescribed at "325mg", a real
    # but not dataset-listed strength, fell through past every real Aspirin entry to an
    # unrelated "Spirodin Injection" because none of the real Aspirin entries stated exactly
    # "325"). Preferring a dose-matching candidate only AMONG genuine ties has no equivalent
    # failure mode.
    matches = [m for m in matches if not _adds_unstated_combination_ingredient(drug_name, full_names[m[2]])]
    matches = [m for m in matches if not _mismatched_vitamin_letter(drug_name, full_names[m[2]])]
    if not matches:
        return None

    high_confidence = [m for m in matches if m[1] >= threshold]
    if high_confidence:
        top_score = high_confidence[0][1]
        tied = [m for m in high_confidence if m[1] >= top_score - _TIE_MARGIN]
        if len(tied) == 1:
            return _for_display(full_names[tied[0][2]])
        return _disambiguate_tied_candidates(tied, full_names, drug_name, dose)

    # Phonetic rescue operates in a FIXED [PHONETIC_RESCUE_FLOOR, DEFAULT_MATCH_THRESHOLD) band,
    # independent of a caller-supplied `threshold` override -- a custom threshold changes how
    # strict the plain spelling-only path above is, not this separate, empirically-calibrated
    # mechanism (verified live: without this cap, a test passing threshold=100 to mean "only an
    # exact base match" could still get a sub-100 phonetic-rescue result).
    query_codes = doublemetaphone(query_base)
    phonetic_matches = [
        m for m in matches
        if m[1] < DEFAULT_MATCH_THRESHOLD and _phonetic_agrees(query_codes, phonetics[m[2]])
    ]
    if not phonetic_matches:
        return None
    top_score = phonetic_matches[0][1]
    tied = [m for m in phonetic_matches if m[1] >= top_score - _TIE_MARGIN]
    if len(tied) == 1:
        return _for_display(full_names[tied[0][2]])
    return _disambiguate_tied_candidates(tied, full_names, drug_name, dose)


# Floor for the bare-name path (_find_bare_name_correction) -- NOT a confidence threshold like
# DEFAULT_MATCH_THRESHOLD (that distinction is the whole point: see this module's docstring).
# This exists only to reject the CLEARLY-unrelated end of the score range -- it does NOT (and
# per the empirical evidence below, structurally cannot) separate a genuine typo of a dataset
# drug from a different, unrelated drug that's simply missing from the dataset, or from a
# different real LASA-colliding drug.
#
# Calibrated empirically, not guessed: "Ofloxil" (the reported bug, should correct) scores 85.7
# against BOTH real dataset entries "Ofloxin"/"Floxsil" it's plausibly a typo of. "Electral"
# (missing from the dataset entirely -- see test_a_real_drug_missing_from_the_dataset_is_left_
# unchanged_rather_than_force_matched, the FORM-gated path's own calibration example) scores an
# EXACT TIE at 85.7 against the unrelated "Eletra" -- i.e. there is NO floor value that admits
# the wanted case and rejects this one; they are mathematically identical here. What a floor
# CAN still usefully reject is the clearly-lower-confidence band below that: "Warfarin"
# (missing from the dataset, a narrow-therapeutic-index anticoagulant) scored 71.4 against the
# entirely unrelated "Aarcin"; "Rivaroxaban" (also missing) scored 77.8 against "Rivaban" --
# both real, live false-positive matches caught while calibrating this. 80.0 sits above both of
# those and comfortably below 85.7, so it removes that lower band of clearly-worse false
# positives without narrowing (or widening) the already-accepted risk at 85.7+ at all.
BARE_NAME_NOISE_FLOOR = 80.0


def _find_bare_name_correction(drug_name: str, dose=None, threshold: float = BARE_NAME_NOISE_FLOOR) -> Optional[str]:
    """
    Unguarded sibling of _find_correction, for drugNames with no stated pharmaceutical
    form/route. Always returns the single highest-scoring dataset match (by the same
    stripped-base fuzz.ratio scoring _find_correction uses) above `threshold`, searched across
    candidates of ANY form (there's no stated form to filter by) -- never gated on a minimum
    "confidence" score the way the form-gated path is, because no such threshold exists that
    would catch real typos (e.g. Ofloxil->Ofloxin, 85.7) without also catching the documented
    LASA collisions (94.7-95.7) this app's form-gate was originally built to exclude. See the
    module docstring for why this tradeoff was accepted anyway. Ties broken the same way as
    _find_correction (prefer whichever candidate's dose matches, else dataset order).
    """
    if not drug_name or not isinstance(drug_name, str):
        return None
    query_base = _strip_to_base(drug_name)
    if len(query_base) < MIN_BASE_LENGTH:
        return None

    full_names, bases, forms, _phonetics_unused = _load()
    if not bases:
        return None

    raw = process.extract(query_base, bases, scorer=fuzz.ratio, score_cutoff=threshold, limit=50)
    matches = [(text, score, index) for text, score, index in raw]
    if not matches:
        return None

    # Same combination-ingredient safety filter as _find_correction -- see its docstring and
    # _adds_unstated_combination_ingredient's. Especially important here: this path is
    # UNGUARDED (no confidence threshold beyond the noise floor), so a wrong-combination
    # candidate winning on spelling alone had nothing else standing in its way before this
    # existed. Dose safety is handled via tie-break preference below, not a hard filter here --
    # see the comment in _find_correction for why a hard dose filter was tried and reverted.
    matches = [m for m in matches if not _adds_unstated_combination_ingredient(drug_name, full_names[m[2]])]
    matches = [m for m in matches if not _mismatched_vitamin_letter(drug_name, full_names[m[2]])]
    if not matches:
        return None

    top_score = matches[0][1]
    tied = [m for m in matches if m[1] >= top_score - _TIE_MARGIN]
    if len(tied) == 1:
        return _for_display(full_names[tied[0][2]])
    return _disambiguate_tied_candidates(tied, full_names, drug_name, dose)


def closest_medicine_name(query: str, threshold: float = DEFAULT_MATCH_THRESHOLD) -> Optional[str]:
    """
    Returns the closest canonical medicine name to `query` from the dataset (with its real
    strength/form intact), or None if nothing scores at/above `threshold` (including when
    `query` states a form and no same-form entry qualifies -- see _find_correction) --
    callers should keep the original name in that case rather than treat None as an error.
    """
    return _find_correction(query, dose=None, threshold=threshold)


def correct_medication_names(medications: list, threshold: float = DEFAULT_MATCH_THRESHOLD) -> list:
    """
    Applied to a prescription's `medications` list (each a dict with a "drugName" key) right
    after AI extraction, before the draft ever reaches the doctor: replaces each drugName with
    its closest canonical match from the medicines dataset. Two paths, in order:
      1. Form-gated (_find_correction): drugName states a form/route -- never crosses to a
         different form, requires a high same-form confidence score, disambiguates same-
         brand/same-form ties using the medication's own dose.
      2. Bare-name fallback (_find_bare_name_correction), only tried when (1) found nothing
         AND drugName states no form at all: always takes the single highest-scoring match
         across the whole dataset, no confidence gate beyond a noise floor -- see that
         function's docstring and this module's docstring for the accepted LASA-collision
         tradeoff this carries.
    Leaves dose/frequency/route/duration untouched, and leaves drugName alone (no key added)
    whenever neither path finds a match -- including when the dataset itself is unavailable,
    so this can never turn a working prescription flow into a broken one.
    """
    if not isinstance(medications, list):
        return medications
    for med in medications:
        if not isinstance(med, dict):
            continue
        original = med.get("drugName")
        if not original or not isinstance(original, str):
            continue
        corrected = _find_correction(original, dose=med.get("dose"), threshold=threshold)
        if corrected is None and not _FORM_RE.search(original):
            corrected = _find_bare_name_correction(original, dose=med.get("dose"))
        if corrected and corrected != original:
            med["drugName"] = corrected
            med["original_drug_name"] = original
    return medications
