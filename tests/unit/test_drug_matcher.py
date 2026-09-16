"""
Tests for backend/app/drug_matcher.py -- the fuzzy medicine-name corrector added to fix
AI-extraction-introduced brand-name misspellings (see that module's docstring, and
_find_correction's, for the full rationale including two real bug classes found and fixed
during development: form/route swaps, and look-alike-sound-alike brand collisions on bare
generic names). Runs against the REAL dataset (backend/app/data/medicine_names.csv, ~249k
names) rather than a mocked/tiny fixture, since the whole point of these cases is pinning down
real behavior against the real data -- a fake tiny dataset would validate the algorithm but not
the actual threshold/quality tradeoff made here.

Every positive (should-correct) case below states an explicit form word ("Tablet X"), matching
this app's own OPD prescribing convention (see tests/from_data/fixtures.py) -- drug_matcher
only ever acts when a form is stated; see test_bare_generic_names_are_never_touched for why.
"""
import pytest

from app import drug_matcher


def test_dataset_loads_and_has_a_realistic_number_of_entries():
    full_names, bases, forms, phonetics = drug_matcher._load()
    assert len(full_names) > 200_000
    assert len(full_names) == len(bases) == len(forms) == len(phonetics)


@pytest.mark.parametrize("misspelled,expected_substring", [
    ("Tablet Zanocine", "Zanocin"),
    ("Tablet Calpoll", "Calpol"),
    ("Tablet Paracetmol 500mg", "Paracetamol"),
])
def test_real_typos_are_corrected_to_the_right_brand(misspelled, expected_substring):
    result = drug_matcher.closest_medicine_name(misspelled)
    assert result is not None, f"expected a confident correction for {misspelled!r}, got None"
    assert expected_substring.lower() in result.lower()


@pytest.mark.parametrize("form_stated_name", ["Tablet Zanocin", "Tablet Calpol"])
def test_known_brand_names_from_the_reference_prescriptions_resolve_to_a_real_product(form_stated_name):
    result = drug_matcher.closest_medicine_name(form_stated_name)
    assert result is not None
    # the corrected name must still carry the same brand token, not drift to an unrelated product
    base_token = form_stated_name.replace("Tablet", "").strip().split()[0].lower()
    assert base_token in result.lower()


@pytest.mark.parametrize("query,expected_substring", [
    # These are common generics whose ONLY dataset entries (verified directly against
    # medicine_names.csv) come from a reseller chain that prefixes every SKU with its own
    # house name ("DavaIndia Pantoprazole 40mg Tablet", "DavaIndia Azithromycin 250mg
    # Tablet", ...) or a combination product. Before _RESELLER_RE existed, that unstripped
    # prefix bloated the stripped base's length enough that fuzz.ratio scored the correct
    # entry too low to even place in the nearest-candidates list -- a mild ASR-style misspelling
    # of any of these resolved to None (left uncorrected) even though the right drug was in the
    # dataset the whole time. Pins down the fix, not just the reseller-stripping mechanism.
    ("Tablet Panthoprazole", "pantoprazole"),
    ("Tablet Azithromycine", "azithromycin"),
    ("Tablet Levocetrizine", "levocetirizine"),
])
def test_reseller_prefixed_generics_are_found_despite_the_house_name_prefix(query, expected_substring):
    result = drug_matcher.closest_medicine_name(query)
    assert result is not None, f"expected a confident correction for {query!r}, got None"
    assert expected_substring in result.lower()


@pytest.mark.parametrize("query,expected_substring", [
    # ASR-style mishearings, not simple typos: spelling drifts further than
    # DEFAULT_MATCH_THRESHOLD tolerates on fuzz.ratio alone (each scores 78-89, verified
    # live), but the SOUND didn't drift -- double-metaphone gives an exact code match against
    # the correct dataset entry. Before PHONETIC_RESCUE_FLOOR existed, every one of these
    # resolved to None (left uncorrected) despite the right drug being in the dataset.
    ("Tablet Panthoprazole", "pantoprazole"),
    ("Tablet Rebeprazol", "rabeprazole"),
    ("Tablet Montilucast", "montelukast"),
    ("Tablet Ofloxasin", "ofloxacin"),
])
def test_phonetic_rescue_catches_asr_style_mishearings_plain_fuzz_ratio_misses(query, expected_substring):
    result = drug_matcher.closest_medicine_name(query)
    assert result is not None, f"expected a phonetic-rescue correction for {query!r}, got None"
    assert expected_substring in result.lower()


def test_phonetic_rescue_still_rejects_same_score_band_candidates_that_sound_different():
    """
    Proves the phonetic gate is discriminating, not a rubber stamp for "anything in the
    PHONETIC_RESCUE_FLOOR..threshold band": "Xantoprazole" and "Wantoprazole" score exactly the
    same fuzz.ratio (91.7) against the real "Pantoprazole" as "Bantoprazole" does -- pure edit
    distance treats a leading X/W/B swap identically -- but only "Bantoprazole" is rescued.
    Double-metaphone encodes B and P as the same phonetic value (both labial-stop consonants,
    "PNTPRSL" for both "bantoprazole" and "pantoprazole") while X and W are not, so those two
    correctly stay unmatched (None) even though nothing about their fuzz.ratio score
    distinguishes them from the case that IS rescued.
    """
    assert drug_matcher.closest_medicine_name("Tablet Bantoprazole") is not None
    assert "pantoprazole" in drug_matcher.closest_medicine_name("Tablet Bantoprazole").lower()
    assert drug_matcher.closest_medicine_name("Tablet Xantoprazole") is None
    assert drug_matcher.closest_medicine_name("Tablet Wantoprazole") is None


@pytest.mark.parametrize("bare_name", [
    "Zanocin", "Calpol", "Augmentin", "Crocin", "Metrogyl",  # already correctly spelled brands
    "Diclofenac", "Aspirin", "Metformin", "Chloroquine",     # already correctly spelled generics
])
def test_closest_medicine_name_never_touches_bare_names(bare_name):
    """
    closest_medicine_name/_find_correction's own hard design constraint: a drugName with NO
    stated pharmaceutical form/route is never corrected by THIS function, even when it's
    already spelled perfectly (Zanocin, Aspirin) or misspelled (see
    test_real_typos_are_corrected_to_the_right_brand, which uses the SAME brand names but with
    a form word present and gets a real correction). This is what eliminates the look-alike-
    sound-alike (LASA) collision risk documented in _find_correction's docstring -- but see
    TestBareNameCorrection below: correct_medication_names (the function actually used on real
    prescriptions) now has a SEPARATE bare-name fallback that deliberately re-accepts this risk
    for bare names specifically. This test only covers the lower-level, still-fully-guarded
    closest_medicine_name/_find_correction.
    """
    assert drug_matcher.closest_medicine_name(bare_name) is None


def test_a_real_drug_missing_from_the_dataset_is_left_unchanged_rather_than_force_matched():
    # "Electral" (a very common Indian ORS brand) does not appear anywhere in the source
    # dataset (verified directly against the CSV) -- the nearest entries are a different,
    # unrelated product family. Forcing a substitution here would be a clinically wrong
    # correction, not a fix, so this must return None rather than the closest-but-wrong entry.
    assert drug_matcher.closest_medicine_name("Electral Powder") is None


def test_empty_or_garbage_input_returns_none_without_raising():
    assert drug_matcher.closest_medicine_name("") is None
    assert drug_matcher.closest_medicine_name(None) is None
    assert drug_matcher.closest_medicine_name("   ") is None
    assert drug_matcher.closest_medicine_name("x") is None  # below MIN_BASE_LENGTH after stripping


@pytest.mark.parametrize("stated_form_drug", [
    "Antacid Syrup", "Diclofenac Gel", "Antacid Suspension", "Salbutamol Inhaler",
])
def test_stated_form_is_never_silently_changed_to_a_different_form(stated_form_drug):
    """
    Regression test for a real, patient-safety-relevant bug caught during development: brand-
    name-only similarity, with no form awareness, matched each of these (a correctly-spelled
    generic name, or a generic category term) to a WRONG, differently-formed product purely on
    character closeness -- "Diclofenac Gel" (topical) to "Dicofenac Injection" (injectable),
    "Antacid Syrup"/"Antacid Suspension" to "Antacid ... Tablet", "Salbutamol Inhaler" to a
    tablet product. A form/route swap is never a legitimate "spelling correction" -- the
    correct behavior is no match at all (this asserts None), not a differently-formed one.
    """
    assert drug_matcher.closest_medicine_name(stated_form_drug) is None


def test_a_stated_form_restricts_candidates_to_that_same_form():
    """
    Positive-side check for the same constraint: "Tablet Zanocin" must never resolve to
    "Zanocin 100 Liquid" (a real dataset entry, and previously a possible outcome before the
    disambiguation/form-constraint fixes) -- only tablet variants are eligible at all.
    """
    result = drug_matcher.closest_medicine_name("Tablet Zanocin")
    assert result is not None
    assert "tablet" in result.lower()
    assert "liquid" not in result.lower()


def test_look_alike_sound_alike_collision_is_eliminated_for_bare_names_by_the_form_requirement():
    """
    "Azithromycin 500" has no stated form/route, so _find_correction/closest_medicine_name
    never even considers it for correction -- eliminating (not just documenting) the look-
    alike-sound-alike collision with the real, different brand "Zithromycin" for THIS function
    specifically. See TestBareNameCorrection::
    test_bare_name_correction_knowingly_reintroduces_the_lasa_collision_for_common_drugs --
    correct_medication_names (what real prescriptions actually go through) no longer has this
    protection for bare names, by explicit product decision.
    """
    assert drug_matcher.closest_medicine_name("Azithromycin 500") is None


def test_threshold_is_respected():
    # A very low threshold should find *some* tablet-form match for almost any garbled input.
    assert drug_matcher.closest_medicine_name("Tablet Zzzznocinnn", threshold=1) is not None
    # A threshold of exactly 100 should only match something that reduces to an identical base.
    assert drug_matcher.closest_medicine_name("Tablet Zanocin", threshold=100) is not None
    assert drug_matcher.closest_medicine_name("Tablet Zanocine", threshold=100) is None  # not identical


class TestCorrectMedicationNames:
    def test_corrects_in_place_and_records_the_original(self):
        meds = [{"drugName": "Tablet Zanocine", "dose": "200 mg", "frequency": "BID", "route": "Oral", "duration": "5 days"}]
        result = drug_matcher.correct_medication_names(meds)
        assert result[0]["drugName"] != "Tablet Zanocine"
        assert "Zanocin" in result[0]["drugName"]
        assert result[0]["original_drug_name"] == "Tablet Zanocine"
        # non-name fields must survive untouched
        assert result[0]["dose"] == "200 mg"
        assert result[0]["frequency"] == "BID"

    def test_leaves_unmatchable_or_missing_drugs_alone(self):
        meds = [{"drugName": "Electral Powder", "dose": "1 sachet"}]
        result = drug_matcher.correct_medication_names(meds)
        assert result[0]["drugName"] == "Electral Powder"
        assert "original_drug_name" not in result[0]

    def test_bare_names_now_go_through_the_unguarded_fallback(self):
        """
        Behavior changed by explicit product decision (see drug_matcher.py's module docstring
        and _find_bare_name_correction's docstring): bare names inside a real medications list
        are no longer left untouched -- "Aspirin" cleanly self-matches (100% on its own
        stripped base, no ambiguity) to a real dosed/formed product. This is the benign end of
        the accepted tradeoff; see test_bare_name_correction_knowingly_reintroduces_the_lasa_
        collision_for_common_drugs for the risky end.
        """
        meds = [{"drugName": "Aspirin", "dose": "325mg", "route": "Oral"}]
        result = drug_matcher.correct_medication_names(meds)
        assert result[0]["drugName"] != "Aspirin"
        assert "aspirin" in result[0]["drugName"].lower()
        assert result[0]["original_drug_name"] == "Aspirin"

    def test_tolerates_malformed_entries_without_raising(self):
        meds = [
            {"dose": "no drugName key at all"},
            {"drugName": ""},
            {"drugName": None},
            "not even a dict",
            {"drugName": "Tablet Calpoll"},
        ]
        result = drug_matcher.correct_medication_names(meds)
        assert result[4]["drugName"] != "Tablet Calpoll"

    def test_non_list_input_is_returned_unchanged(self):
        assert drug_matcher.correct_medication_names(None) is None
        assert drug_matcher.correct_medication_names("not a list") == "not a list"

    def test_empty_list_is_a_no_op(self):
        assert drug_matcher.correct_medication_names([]) == []

    def test_dose_disambiguates_same_brand_ties_to_the_right_strength_and_form(self):
        """
        Regression test for a real bug caught during development: "Zanocin"/"Calpol" have
        several dataset entries (different strengths -- 200mg vs 100mg) that all reduce to the
        identical stripped base and tie on score. Without using the medication's own dose to
        break the tie, the first tied entry in dataset order won arbitrarily -- e.g. "Tablet
        Zanocin" at 200mg once resolved to a 100mg variant, contradicting the medication's own
        dose field.
        """
        meds = [
            {"drugName": "Tablet Zanocin", "dose": "200 mg", "route": "Oral"},
            {"drugName": "Tablet Calpol", "dose": "500 mg", "route": "Oral"},
        ]
        result = drug_matcher.correct_medication_names(meds)
        assert result[0]["drugName"] == "Zanocin 200 Tablet"
        assert result[1]["drugName"] == "Calpol 500mg Tablet"

    def test_dose_number_matching_is_digit_adjacent_not_word_boundary(self):
        """
        Regression test for the specific bug behind the fix above: `\\b500\\b` never matches
        inside "Calpol 500mg Tablet" because "500" and "mg" are both regex word characters
        with no boundary between them -- verified live, this silently picked "Calpol 1000mg
        Tablet" instead before the digit-adjacency fix.
        """
        meds = [{"drugName": "Tablet Calpol", "dose": "500 mg"}]
        assert drug_matcher.correct_medication_names(meds)[0]["drugName"] == "Calpol 500mg Tablet"


class TestBareNameCorrection:
    """
    _find_bare_name_correction / its wiring into correct_medication_names -- the unguarded
    sibling of the form-gated path above, added by explicit product decision to catch typos
    like "Ofloxil" (a doctor said "Ofloxin", ASR/LLM produced "Ofloxil" with no stated form)
    that the form-gated path structurally can never see. See drug_matcher.py's module
    docstring for the accepted tradeoff this carries.
    """

    def test_real_reported_case_ofloxil_gets_corrected_not_left_alone(self):
        # The original bug report: "Ofloxil" (no form stated) reached the doctor's screen
        # completely unchecked under the old form-gated-only behavior. It must now change.
        result = drug_matcher._find_bare_name_correction("Ofloxil")
        assert result is not None
        assert result != "Ofloxil"

    def test_ofloxil_resolves_to_floxsil_not_ofloxin_a_genuine_tie(self):
        """
        Pins down the ACTUAL resolved value, not just "some correction happened" -- worth
        being explicit about because it's counter-intuitive. "Ofloxil" scores an EXACT tie
        (85.71 on fuzz.ratio) between two different real products, "Ofloxin 50mg Oral
        Suspension" and "Floxsil 500 Tablet" -- neither is a form/dose variant of the other,
        they're unrelated brands that happen to be equidistant. With no dose provided to
        disambiguate (see _disambiguate_tied_candidates), the tie resolves to whichever
        appears first in the dataset -- an artifact of file order, not a considered choice.
        If a doctor actually meant "Ofloxin", this "corrects" to the wrong real drug just as
        readily as it can happen to land on the right one -- exactly the accepted risk.
        """
        assert drug_matcher._find_bare_name_correction("Ofloxil") == "Floxsil 500 Tablet"

    def test_bare_name_correction_no_longer_collides_on_reseller_listed_generics(self):
        """
        Was pinned as a KNOWN-BAD tradeoff (bare "Diclofenac"/"Azithromycin", correctly
        spelled, silently rewritten to a different, real, wrong look-alike-sound-alike brand
        -- "Dicofenac Injection", "Zithromycin ..."), caused by _strip_to_base not stripping
        the reseller house-name prefix ("StayHappi"/"DavaIndia"/"Genericart") that every
        plain-generic dataset entry for these drugs carries. That bloated those entries'
        stripped-base length enough that fuzz.ratio scored the WRONG, unprefixed LASA brand
        higher than the CORRECT, reseller-prefixed generic. Stripping the reseller prefix
        (see _RESELLER_RE) lets "Diclofenac"/"Azithromycin" match their own correct,
        real dataset entry at a perfect/near-perfect score instead, which now legitimately
        outscores the LASA collision -- verified live, not just for these two names. This does
        NOT eliminate the LASA-collision risk category in general (see
        test_ofloxil_resolves_to_floxsil_not_ofloxin_a_genuine_tie, and any generic not
        distributed by these 3 resellers is still exposed), only for the specific
        reseller-prefix-dilution mechanism that was causing it here.
        """
        result = drug_matcher._find_bare_name_correction("Diclofenac")
        assert result is not None and "diclofenac" in result.lower()
        result = drug_matcher._find_bare_name_correction("Azithromycin")
        assert result is not None and "azithromycin" in result.lower()

    def test_vitamin_d3_resolves_to_a_real_vitamin_d3_product_not_a_different_vitamin(self):
        """
        Real reported bug, live-verified against production data: bare "Vitamin D3" (no real
        standalone "Vitamin D3" entry existed in medicine_names.csv -- the only row containing
        that string was a "Atorvastatin+Vitamin D3" COMBINATION product, already excluded by
        _adds_unstated_combination_ingredient) fuzzy-matched "Vitamin A 100000IU Syrup" via this
        unguarded path: the shared "vitamin " prefix alone was enough to clear
        BARE_NAME_NOISE_FLOOR, silently substituting a chemically unrelated vitamin. Fixed two
        ways: (1) _mismatched_vitamin_letter hard-rejects any "Vitamin <letter>" candidate whose
        letter/number code differs from the query's (defense-in-depth for every vitamin letter
        the dataset has -- A/B6/C/D3 as of this writing -- not just this one pair); (2) real
        "Vitamin D3 60000IU ..." entries were added to custom_medicines.csv (the same "admin adds
        a real medicine the bulk dataset is missing" mechanism this module already documents),
        since (1) alone only stops the WRONG vitamin match, it doesn't by itself make a RIGHT one
        exist to match instead -- confirmed live that blocking the Vitamin A match without adding
        a real entry just fell through to a second, unrelated wrong match ("Talmin D...").
        """
        result = drug_matcher._find_bare_name_correction("Vitamin D3")
        assert result is not None
        assert "vitamin d3" in result.lower()

    def test_vitamin_letter_mismatch_blocks_cross_vitamin_correction_even_with_no_dataset_gap(self):
        """Pins down the safety filter itself (_mismatched_vitamin_letter), independent of
        whether a real matching entry exists: a bare "Vitamin C" query must never resolve to
        the unrelated "Vitamin A"/"Vitamin D3" entries just because they share a "vitamin "
        prefix -- it has its own real dataset entry ("Vitamin C Injection") to match instead."""
        result = drug_matcher._find_bare_name_correction("Vitamin C")
        assert result is not None
        assert "vitamin c" in result.lower()
        assert "vitamin a" not in result.lower()
        assert "vitamin d3" not in result.lower()

    def test_noise_floor_rejects_input_unrelated_to_anything_in_the_dataset(self):
        assert drug_matcher._find_bare_name_correction("Xyzzyxqqqq") is None

    def test_empty_or_garbage_input_returns_none_without_raising(self):
        assert drug_matcher._find_bare_name_correction("") is None
        assert drug_matcher._find_bare_name_correction(None) is None
        assert drug_matcher._find_bare_name_correction("   ") is None
        assert drug_matcher._find_bare_name_correction("x") is None

    def test_correct_medication_names_applies_the_bare_name_fallback_and_records_original(self):
        meds = [{"drugName": "Ofloxil", "dose": "", "frequency": "", "route": "", "duration": ""}]
        result = drug_matcher.correct_medication_names(meds)
        assert result[0]["drugName"] == "Floxsil 500 Tablet"
        assert result[0]["original_drug_name"] == "Ofloxil"

    def test_form_gated_path_still_takes_priority_over_the_bare_name_fallback(self):
        """A drugName that DOES state a form goes through _find_correction only -- the
        bare-name fallback must never override a real, gated correction (or override the
        gated path's decision to return None for an unmatchable formed name)."""
        meds = [{"drugName": "Tablet Zanocine", "dose": "200 mg"}]
        result = drug_matcher.correct_medication_names(meds)
        assert "Zanocin" in result[0]["drugName"]
        assert "tablet" in result[0]["drugName"].lower()


class TestScribeIntegration:
    """Confirms scribe.scribe_transcript and generate_discharge_summary actually apply the
    correction to their real output, not just that drug_matcher works in isolation."""

    def test_scribe_transcript_corrects_medication_names(self, monkeypatch):
        from app.scribe import scribe
        import json as _json

        monkeypatch.setattr(scribe, "_call_groq_api", lambda *a, **k: _json.dumps({
            "chiefComplaint": "fever", "medications": [
                {"drugName": "Tablet Zanocine", "dose": "200 mg", "frequency": "BID", "route": "Oral", "duration": "5 days"},
            ],
        }))
        result = scribe.scribe_transcript("doctor patient transcript long enough to pass validation")
        assert result["medications"][0]["drugName"] != "Tablet Zanocine"
        assert "Zanocin" in result["medications"][0]["drugName"]

    def test_discharge_summary_corrects_medications_at_discharge(self, monkeypatch):
        from app.scribe import scribe
        import json as _json

        monkeypatch.setattr(scribe, "_call_groq_api", lambda *a, **k: _json.dumps({
            "admissionSummary": "x", "medicationsAtDischarge": [
                {"drugName": "Tablet Calpoll", "dose": "500 mg", "frequency": "SOS", "duration": "5 days"},
            ],
        }))
        result = scribe.generate_discharge_summary({"patient_name": "Test"})
        assert result["medicationsAtDischarge"][0]["drugName"] != "Tablet Calpoll"
        assert "Calpol" in result["medicationsAtDischarge"][0]["drugName"]
