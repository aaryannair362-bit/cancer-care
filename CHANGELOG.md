# CHANGELOG.md

Chronological log of test-suite and bug-fix work. See ARCHITECTURE_NOTES.md for the codebase
map and TEST_NOTES.md for ambiguities/gaps that were deliberately documented rather than fixed.

## 2026-08-25 - CCA security lockdown, OPD voice restoration, financial-precision fix, deployment config

Documentation note: the large HMS expansion (pharmacy/inventory/billing/appointments/nursing
charting/assessments/MAR/patient documents/TPA routers) and the CCA Cancer Care OS module were
both built between the 2026-08-03 entry above and this one, with no CHANGELOG entry of their
own -- this entry starts by closing that gap, then covers this pass's own fixes.

### Critical: CCA Oncology OS had no authentication and no tenant isolation

All 37 endpoints in `backend/app/routers/cca.py` depended only on a DB session -- no
`get_current_user`, no `organization_id` filtering anywhere in the CCA table set
(`CCAPatient.organization_id` defaulted to `1` and was never read or filtered on), and
`POST /api/cca/demo/reset` could wipe the CCA database with no auth check at all. A strictly
worse recurrence of this codebase's own "most severe finding of the entire engagement" (the
2026-08-01 user-management cross-tenant bug) -- that one at least required a valid token.

Fixed: every endpoint now requires `Depends(get_current_user)` and resolves/validates the
target `CCAPatient` against the caller's `organization_id` before touching it (404, not 403, on
a cross-org patient, matching this codebase's no-enumeration convention).
`CCAPatient.organization_id` is now a proper `NOT NULL` foreign key (`models_cca.py`);
`cca_seed.py`'s demo seeder is now org-aware (and MRN-suffixes non-default orgs to avoid
colliding with org 1's hardcoded demo MRNs). `/demo/reset`, `/demo/simulate-result`, and
`/demo/advance-clock` are now Admin-only. Hardcoded scripted identities ("Dr. Sarah Varma",
"Nurse Rekha Menon", ...) stamped into `verified_by`/`recorded_by`/`decided_by`/journey-event
actor fields regardless of who actually called the endpoint are now the real authenticated
caller's email. Several endpoints silently defaulted a missing `patient_id` to `1` (a malformed
request would corrupt patient #1's chart); these now reject with 422. `complete_nurse_intake`
did bare `float()`/`int()` on request values with no error handling (a non-numeric value raised
an uncaught 500); now returns a clean 400. `POST /treatment/clearance` defaulted `decision` to
`"CLEARED"` and `reason` to a canned string on an empty body -- an unreviewed request could
mark a chemotherapy session administered with a fabricated justification; both fields are now
required, and the endpoint 422s if the patient has no treatment session to attach the decision
to (previously silently attached to session id `1` regardless of patient). Both
`/treatment/clearance`'s and `/care-plans`' write paths are now Doctor/Admin-only
(`staging/confirm`, `mdt/.../recommendation`, `care-plans` create/update) -- irreversible
clinical decisions, matching the seriousness of the action.

19 new/updated tests (`tests/integration/test_cca_api_workflow.py`,
`tests/unit/test_cca_engines.py`), including a dedicated
`test_cca_endpoints_require_auth_and_are_org_scoped` regression test.

### `cca_engine.py`: AI brief/care-plan generation fabricated specific clinical content

`synthesize_nexus_brief()` substituted specific invented values when real data was missing
(a fixed `'Invasive Breast Carcinoma, NOS'` diagnosis, a fabricated `'cT2 cN0 cM0 (Provisional
Stage IIA...)'` stage, an invented `'Hypertension (controlled on Amlodipine 5mg OD)'`
comorbidity applied to every patient regardless of their real history, and `'Baseline CBC/LFT/
KFT normal'` asserted with no actual lab data) -- directly contradicting the module's own "zero
autonomous generation" docstring. `generate_care_plan_prefill()` was worse: it returned one
fixed, fully-dosed AC-T chemotherapy regimen (real drug names and doses) for every patient
regardless of diagnosis, staging, or biomarkers, formatted identically to genuine chart data.

Fixed: missing-data sections now say `[NOT_RECORDED]`/`[NOT_STAGED]` explicitly instead of
inventing plausible text. `generate_care_plan_prefill()` now returns `{"ready": false, ...}`
with no drug/dose content at all until a real, finalized `MDTDecision` exists for the patient --
a specific regimen is only ever shown once an actual tumor-board recommendation is on record.

### OPD voice-drafted consultations restored (IPD deliberately left as plain-form, per product decision)

The working tree's `main.py` had dropped `POST /api/scribe`, `GET /api/transcription-provider`,
`POST /api/transcribe-audio`, and `POST /api/translate` entirely, while `frontend/opd.html` and
`frontend/js/voice-capture.js` were untouched and still called all four -- every voice-drafted
OPD consultation was broken (network/404 errors), IPD's own voice endpoints
(`voice-to-vitals`, `nurse-consult`) had been removed too and were confirmed **not** wanted back.
Restored the four OPD endpoints from git history (`scribe.py`/`sarvam_transcriber.py` were
untouched and already fully working, just orphaned with zero callers), adapted to run the
AI-extracted medications/labs through the same deterministic `drug_matcher`/`lab_test_matcher`
correction `POST /api/consultations` already applies to manually-entered ones, and gated to
Doctor-only for consistency with that same endpoint (both write to `Consultation`). 10 tests
passing (`tests/integration/test_scribe_input_edge_cases.py`,
`test_opd_scribe_patient_linkage.py`, `test_transcribe_audio_endpoint.py`,
`test_sarvam_transcription_provider.py`).

### Money stored as `Float` in billing/pharmacy/inventory

`Invoice`/`InvoiceLine`/`Payment`/`Refund`/`BillingClaim`/`Tariff`/`BillingPackage`/`Drug`/
`DispensingRecord`/`PurchaseOrderLine` all stored money as `Float` -- binary floating-point,
not exact decimal, a real (if usually small) rounding-drift risk for financial data. Changed
every money column to `Numeric(12, 2)` and every corresponding Pydantic request field from
`float` to `Decimal` (`billing.py`, `pharmacy.py`, `inventory.py`) -- the existing
`round(x, 2)` arithmetic throughout those routers already worked correctly against `Decimal`
without further changes, since Python's `Decimal` supports the same operators. 5 tests
re-verified passing (payment/refund balance arithmetic, dispensing totals).

### Frontend: `InventoryManager` login stuck in an infinite redirect loop

`inventory.html`'s `Auth.requirePage(['Pharmacist', 'Admin'])` omitted the `InventoryManager`
role that `js/api.js`'s own `ROLE_HOME`/`NAV_ITEMS` already routed to this exact page --
logging in as that role redirected to `/inventory.html`, which then redirected right back to
`/inventory.html` (its own configured home), forever. Fixed by adding the role to the allowed
list.

### Frontend: stored-XSS risk and missing authentication in the CCA Oncology OS UI

`cca-app.js` made every one of its ~20 API calls with zero authentication (raw `fetch()`, no
`Authorization` header) and had no `escapeHtml()`/output-encoding anywhere, unlike every other
frontend page -- ~15 `innerHTML` sites interpolated patient/document-derived data directly,
including OCR/LLM-extracted document text (attacker-influenced content from an uploaded file).
Once the backend now requires authentication (above), this file would have been entirely
non-functional without a matching frontend fix.

Fixed: `cca_os.html` now loads `js/api.js` and gates itself with `Auth.requirePage(...)`;
`cca-app.js`'s ~20 raw `fetch()` calls now go through `Api.get`/`Api.post` (authenticated,
with the existing token-refresh-on-401 handling); every interpolated value in an `innerHTML`
template is now `escapeHtml()`-wrapped or `Number()`-coerced as appropriate. Also fixed in
passing: the "Reject" button in the document-verification workspace called a `rejectFact()`
function that was never defined anywhere in the file (the backend endpoint already existed
with no caller) -- implemented, mirroring the existing `acceptFact()`.

### Cross-platform OCR (Tesseract path resolution)

`ocr_service.py` fell back to a hardcoded Windows path
(`C:\Program Files\Tesseract-OCR\tesseract.exe`) when `TESSERACT_CMD` wasn't set -- silently
non-functional on Render/Linux with no clear error. Added `_resolve_tesseract_cmd()`: explicit
`TESSERACT_CMD` config, then `shutil.which("tesseract")` (works on any platform where the
system package is installed and on `PATH`), then the Windows path as a last-resort local-dev
convenience, then `None` (letting pytesseract raise its own clear `TesseractNotFoundError`
instead of this silently doing nothing).

### Deployment configuration added (previously none existed in-repo)

Added `Dockerfile` (installs the system `tesseract-ocr` package `pip` cannot install),
`render.yaml` (Render Blueprint: env var list, health check, Docker runtime), `.env.example`
(every setting `config.py` reads, documented), and fixed `.gitignore`'s `.env*` pattern, which
was also silently excluding `.env.example` itself from version control.

### Documentation drift

`README.md`, `SCHEMA.md`, and `QUICKSTART.md` all still described the original, unrelated
Supabase/voice-agent scaffold this repo started from (`dashboard/`, `voice-agent/`,
`doctors`/`patients`/`appointments`/`call_logs` tables) -- none of which is wired into
`backend/app`/`frontend`. Rewritten to describe the system that actually exists;
`ARCHITECTURE_NOTES.md`'s core-module section corrected where it had also gone stale (drug
interactions are a static curated table now, not a Groq call; PHI-in-logs via `print()` in
`scribe.py` was already fixed to `logger.debug`; discharge summaries are deterministic
templating in `discharge_summary.py`, not `scribe.generate_discharge_summary()`).

## 2026-08-24 - External-record OCR and longitudinal case review

- Added registration-desk upload of PDF, JPEG, PNG, and TIFF patient records (25 MB limit),
  duplicate detection, tenant isolation, audit logging, and durable database storage of the
  original bytes, page text, OCR metadata, and conservatively extracted clinical signals.
- Added embedded-text extraction for digital PDFs and Tesseract OCR for scanned PDF pages and
  images. Processing failures retain the original file with an explicit `NeedsReview` state.
- Added a doctor case-summary API combining imported findings with existing consultations,
  vitals, nursing notes, and procedures. The UI presents the summary beside the authenticated
  original-document viewer and keeps an explicit source-verification warning.
- OPD patient lookup now includes standalone-registered patients, supports name/phone/MRN
  search, accepts appointment deep links, and can launch a consultation directly from case
  review. A doctor's appointment list exposes the same review-and-consult entry point.
- Added 8 OCR/document integration tests covering real image OCR, upload/read/summary flow,
  duplicate rejection, role and tenant boundaries, unsafe content types, and OCR failure
  recovery.
- Extended the Insurance/TPA desk with patient search -> complete case review -> original-report
  viewing -> `Push for Pre-Approval`. TPA access is read-only and tenant-scoped; submitted
  pre-approval snapshots now freeze imported-report metadata, checksums, OCR status, and
  extracted clinical findings without duplicating the source-file binary into the snapshot.

## 2026-08-03 pass: medicine/lab-test name correction + custom-data admin UI, frontend visual
## refresh, and a 4-phase OPD/IPD/HeadNurse rebuild matching 3 supplied reference designs

Three requests handled back to back: (1) fuzzy-correct AI-extracted medicine and lab-test names
against real reference datasets instead of trusting the model's spelling verbatim; (2) a visual
refresh of all three frontend pages plus a small login-page touch-up; (3) a full rebuild of the
OPD/IPD/HeadNurse frontends to match the layout and feature set of three supplied reference
designs (`AIVANA_OPDScribe.html`, `AIVANA_IPDNurse.html`, `AIVANA_HeadNurse.html`), while
preserving the doctor's real voice-transcript pipeline and the nurse's real vitals pipeline
exactly as they were, and building every genuinely-new concept (ward capacity, nurse shift
scheduling, reports aggregation) for real rather than as reference-fidelity mock data.

### Added -- medicine and lab-test name correction (`backend/app/drug_matcher.py`,
### `backend/app/lab_test_matcher.py`)

Both run inside `scribe.scribe_transcript()` (medications + labTests) and the IPD
`nurse_consult` endpoint (labs), correcting AI-extracted names against real reference datasets
(a ~249k-name Indian medicines list; the supplied `IPD_Lab_Master_Starter` Test Name/Common
Alias columns) via `rapidfuzz`.

- **Drug matcher**: only corrects a `drugName` when it explicitly states a pharmaceutical
  form/route (e.g. "Tablet Zanocin") -- bare generic names are never touched. This gate exists
  because two real bug classes were found and are provably unsolvable by threshold-tuning
  alone: form/route swaps on bare names (e.g. "Diclofenac Gel" incorrectly corrected to
  "Dicofenac Injection") and look-alike-sound-alike collisions scoring in the same range as
  genuine typo fixes (e.g. "Diclofenac"->"Dicofenac", "Azithromycin"->"Zithromycin").
- **Lab test matcher**: exact alias/name lookup first; fuzzy fallback (`fuzz.WRatio`) only for
  non-exact matches, excluding short candidates (<5 chars normalized) from the fuzzy pool to
  prevent `partial_ratio` false positives (e.g. "HBV DNA"->"Hemoglobin" via the "Hb" substring,
  "Fasting Blood Sugar"->"Antibiotic Sensitivity Testing" via "ast" embedded in "fASTing").
- **Custom-addition system**: `backend/app/data/custom_medicines.csv` /
  `custom_lab_tests.csv`, editable via new Admin-only `POST/GET/DELETE /api/admin/medicines`
  and `/api/admin/lab-tests`, surfaced as a "Medicine List"/"Lab Test List" section in
  `frontend/admin.html` -- the raw Excel/PDF reference sources are cumbersome to hand-edit, so
  gaps in the dataset can now be added directly through the app instead.

### Changed -- frontend visual refresh (all four pages)

Mechanical color/font swap across `index.html`/`opd.html`/`ipd.html`/`admin.html`: sage green
(`#2F6F52`/`#234E39`) primary, clay/terracotta (`#C1694F`) danger, amber (`#8A5B22`) warning,
cream (`#FBFAF7`/`#F3F2EE`/`#FFFFFF`) backgrounds, Fraunces serif + Inter sans + IBM Plex Mono
fonts. No structural/id/JS changes in this pass -- see the rebuild below for the structural
work. Also: the OPD page no longer shows which AI model/provider is powering it anywhere in the
UI (was `✅ Groq: llama-3.1-8b-instant`); every new Analytics/Reports surface built afterward
carries the same rule forward (aggregate latency/token metrics and a connectivity boolean only,
never a model name).

### Fixed -- stale-frontend caching

`serve_index()`/`serve_html()` (`backend/app/main.py`) returned `FileResponse` with no
`Cache-Control` header, so browsers fell back to heuristic caching and kept serving a stale
cached copy of `opd.html`/`ipd.html`/etc. indefinitely after a deploy, with no revalidation, even
though the new file was live on the server the whole time -- surfaced when the visual refresh
above appeared to "not take" for a returning user. Fixed by adding
`Cache-Control: no-cache, must-revalidate` (not `no-store`, so a revalidated cached copy is still
usable) to every served frontend page.

### Added -- Phase 1: backend foundation for the OPD/IPD/HeadNurse rebuild

New models (`backend/app/models.py`): `Ward` (per-org bed-capacity config, matched
case-insensitively against the existing free-text `Patient.ward` -- no FK, no `Bed` entity, so
no backfill is needed and admission still works normally for any org with no wards configured
yet) and `NurseShift` (per-org nurse x date shift assignment, unique on `(nurse_id,
shift_date)`). One additive migration column: `consultations.finalized_at`, set the first time
`PATCH /api/consultations/{id}/finalize` is called, giving the OPD History view's Draft/
Finalized status real backing data.

New/extended endpoints (`backend/app/main.py`), all through the existing
`is_admin`/`is_head_nurse`/`is_nursing_station`/`is_nurse`/`is_doctor` + org-scoping + `log_audit`
conventions:

- `GET /api/consultations` gains `search`/`offset`/a capped `limit`, and `total`/`finalized` in
  the response, for a real search + pager in OPD History.
- `GET /api/consultations/analytics?days=30` (Doctor-only, per-doctor scoped): consultations-
  per-day, latency trend, token-usage trend, period totals -- from columns every real
  `POST /api/scribe` call already populates. Deliberately no "success rate" (a failed Groq call
  never commits a row, so it isn't derivable) and no model/provider name. Must be registered
  *before* `GET /api/consultations/{id}` in route order, or FastAPI's `{id}: int` coercion
  greedily 422s on the literal path segment "analytics" before ever reaching this handler.
- `GET/POST/PATCH/DELETE /api/wards` (HeadNurse/Admin): capacity CRUD with computed live
  occupancy (a COUNT query, not a stored/cached number, so it can never drift).
- `POST /api/ipd/patients` gains real validation: case-insensitive duplicate-*active*-patient-
  name detection (409 on first submission, proceeds on a `confirm_duplicate: true` resubmit --
  so a genuine second patient sharing a name isn't hard-blocked forever), age `0 <= age <= 130`
  (0 is a legitimate newborn), ward-capacity check (skipped if the org has no matching `Ward`
  row), and same-ward same-bed-string conflict detection.
- `GET/PUT /api/ipd/shifts` (HeadNurse): a real, editable weekly nurse x day grid (every org
  nurse defaulted to "Off" for days with no row) -- not the reference mockup's read-only
  hardcoded schedule, since a head nurse needs to actually set shifts.
- `GET /api/ipd/alerts` (same role gate as the roster): flattens the existing
  abnormal-vitals/overdue-task roster computation into one paginated, most-recent-first feed,
  reusing (not re-deriving) the roster logic so the two views can never disagree.
- `GET /api/ipd/reports?days=7` and `GET /api/ipd/dashboard-summary` (HeadNurse): task-
  completion-per-day, patients-by-ward, and top-N *raw* diagnosis strings by count (not the
  reference's 5 invented fixed categories -- `Patient.diagnosis` is free text with no real
  taxonomy); 4 real KPI counts for the dashboard tiles.
- `PATCH /api/ipd/tasks/{id}`'s `status` is now whitelisted to `Pending`/`Completed` (previously
  written with zero validation, unlike `task_type`, which was already validated on create).

New integration test files (`tests/integration/test_ward_capacity_management.py`,
`test_nurse_shift_scheduling.py`, `test_ipd_reports_and_dashboard_summary.py`,
`test_ipd_alerts_endpoint.py`, `test_ipd_admit_validation.py`,
`test_consultations_search_pagination_and_analytics.py`) plus 25 new cases folded into
`test_role_permission_matrix.py` for every endpoint above, across all 5 roles.

### Changed -- Phase 2: `frontend/opd.html` restructured into a step wizard

Setup -> Transcript -> Clinical Note -> Interactions -> Prescription, with a clickable step-dot
header (back-navigable to any completed step, forward capped at the furthest reached), plus
History/Analytics as separate top-level views -- all wrapped *around* the unchanged real
pipeline (`processConsultation`, `checkInteractions`, `finalizeConsultation`, `populateDraft`,
`updatePrintPreview`, voice capture), not a reimplementation of it. `#patient-select` stays the
real state-holder behind a patient-card grid (kept non-`display:none` -- a 1x1px
visually-hidden pattern -- since `select_option()`/`wait_for_selector()` require a
non-zero-bounding-box element). The previously-dead `#print-language` dropdown now really
translates the printed Rx sheet (via the existing `POST /api/translate`, snapshotting and
restoring the doctor's own English draft fields around the translated print, so the working
clinical note is never overwritten) and `#live-mode` really gates whether interim speech shows
live vs. only committing on Stop. History gained real search/pagination; Analytics is wired to
the new `/api/consultations/analytics` endpoint with lightweight dependency-free inline-SVG
charts (no new library, matching the app's no-build-step constraint).

### Changed -- Phase 3: `frontend/ipd.html` restructured into a patient drawer + alerts + kanban

`#patient-detail-modal` repositioned into a slide-out right-side drawer (CSS only). Patient
detail gains a 6th tab -- Overview (admitted duration + assigned nurse, both genuinely computed,
replacing the reference's hardcoded placeholders), Vitals, Medication (active-medications
summary + full consultation history -- a disclosed rename of the old "Doctor's Notes" tab, no
information dropped), Tasks, Nursing Notes, Discharge Summary (kept separate rather than folded
into a generic notes tab, since it's a substantial already-working feature). New Alerts sidebar
view backed by `GET /api/ipd/alerts`. Admit modal now shows a ward-capacity dropdown (live
occupancy, disabled once full) when the org has configured wards, with a confirm-and-resubmit
flow for the new duplicate-name validation. Tasks view gained a List/Kanban toggle (2 real
columns -- Pending/Completed -- with overdue shown as a card attribute, not an invented 3rd
status this system's task lifecycle doesn't have).

### Added -- Phase 4: `frontend/headnurse.html`, a new dedicated HeadNurse page

HeadNurse previously had no page of its own, only role-gated sections inside `ipd.html`.
New page with the reference's sidebar (Dashboard/Patients/Assign/Tasks/Calendar/Reports): real
KPI tiles from `GET /api/ipd/dashboard-summary`, an editable weekly shift Calendar
(`GET`/`PUT /api/ipd/shifts`), and Reports charts (`GET /api/ipd/reports`) -- the first UI ever
built against these three Phase-1 endpoints. `frontend/js/ipd-shared.js` (plain `<script src>`
include, no build step) now holds the genuinely shared, stateless pieces (`apiRequest`,
`closeModal`, `taskTypeBadge`) between `ipd.html` and `headnurse.html`; the deeper patient-
drawer/admit/task-modal logic is intentionally duplicated rather than abstracted, since it's too
stateful to share safely without a real module system. Routing split: `index.html`'s post-login
redirect and `admin.html`'s non-admin-redirect now send `HeadNurse` to `/headnurse.html`
specifically, `Nurse`/`NursingStation` stay on `/ipd.html`. Clean cutover: the now-dead
HeadNurse-only branches (Assign view, Create-Task modal, Unassign button, ward-summary variant)
were removed from `ipd.html` rather than left as dormant dead code.

Two real bugs caught by running (not predicting) the test suite against the new page: (1)
`test_deep_opd_ipd_voice_journey.py` and `tests/scale/runner.py` still navigated a HeadNurse
browser session straight to `/ipd.html`, which now bounces to `/headnurse.html` -- fixed to
target the new page; (2) the voice-mic button's `listening` CSS class toggle (and its
CSS/animation) was dropped while porting the nursing-consult modal into the new page -- added
back, confirmed via the existing `test_headnurse_voice_e2e.py` regression coverage.

### Verification

Every phase landed and stayed fully green before the next started. Final state: **1566 tests
collected**; full non-e2e/scenario suites plus a fresh 57/57 e2e run and 30/31 scenario run (the
1 scenario failure is a pre-existing Playwright timing flake in one Bengali-transcript OPD case,
confirmed unrelated to this pass by calling the backend directly with that exact transcript --
it returns fully correct data -- and by confirming every file in that test's code path is
untouched by this pass's diff). The finalized OPD prescription's content was re-verified
fragment-by-fragment against the same reference PDF validated in an earlier pass, proving the
wizard restructure didn't change what actually gets extracted or printed.

## 2026-08-01 pass (part 3): full-application end-to-end pass, new Discharge Summary feature,
## critical cross-tenant security fix, concurrency testing, multilingual curated use-case library

Final, broadest pass of this engagement: complete application testing across every login type
and use case combined (not per-role in isolation), scenarios spanning a single-doctor small
clinic up to a multi-specialty hospital and trauma center, the full clinical breadth requested
(trauma/emergency through chronic disease, rare disease, multiple cancer types, IVF, and
cosmetic care), real concurrent/bulk-load testing, a brand new AI-generated Discharge Summary
feature, and a curated library of realistic multi-lingual consultations driven through the
actual voice UI with output artifacts saved to `final test output/`.

### Fixed -- backend/app/main.py (CRITICAL)

**Cross-tenant account-takeover vulnerability across three user-management endpoints.**
`GET /api/auth/users`, `PATCH /api/auth/users/{id}`, and `PATCH /api/auth/users/{id}/password`
had no organization scoping at all -- unlike every IPD endpoint (which had this exact class of
bug fixed in the very first pass), these three were missed at the time. Severity:
- `GET /api/auth/users`: any Admin/HeadNurse could see every user's email/role/status across
  **every organization** in the entire system.
- `PATCH /api/auth/users/{id}`: any Admin could change the role of **any user in any other
  organization** -- e.g. promote a stranger's account to Admin, or sabotage another hospital's
  deployment by demoting their admin.
- `PATCH /api/auth/users/{id}/password`: any Admin could reset the password of **any user in
  any other organization** -- full cross-tenant account takeover, the most severe finding
  across every pass of this engagement.

Found via `test_hospital_scale_scenarios.py`'s small-clinic test unexpectedly returning 5 users
instead of 4 (a leaked default-bootstrap admin from an unrelated organization). All three fixed
by adding the same `User.organization_id == current_user.get("organization_id")` filter every
other user-facing query in this codebase already uses. Regression-covered by 10 new tests in
`tests/integration/test_user_management_multi_tenant_isolation.py`, including a check that a
cross-org password-reset attempt does not silently succeed despite the 404, and that the
victim's real password still works afterward.

### Added -- new feature: AI-generated Discharge Summary

`backend/app/models.py` gained a `DischargeSummary` table; `backend/app/scribe.py` gained
`generate_discharge_summary()`; `backend/app/main.py` gained
`POST`/`GET /api/ipd/patients/{id}/discharge-summary` (HeadNurse/NursingStation/Doctor to
generate, same viewers as the rest of the chart to read). Assembles a patient's full IPD record
-- vitals trend, all nursing notes, tasks, and any linked OPD consultations -- into an AI-drafted
discharge document (admission summary, hospital course, discharge diagnosis, medications at
discharge, follow-up instructions, condition at discharge), mirroring `scribe_transcript`'s
never-raises-always-backfills contract and the same "422 on total extraction failure" guard
used elsewhere. `frontend/ipd.html` gained a new "Discharge Summary" tab in the patient detail
modal, including a print/export view (mirroring OPD's existing prescription print pattern) --
a real, common hospital-operations pain point (writing a discharge summary by hand from the
chart) addressed with data this system already captures. 22 new tests
(`tests/integration/test_discharge_summary_feature.py`, `tests/e2e/test_discharge_summary_e2e.py`).

### Added -- real concurrency / bulk-load testing (new `tests/concurrency/`)

10 tests driving genuine simultaneous HTTP requests (via `ThreadPoolExecutor` + `requests`)
against a real live uvicorn server -- the only way to exercise the time-of-check-to-time-of-use
races an in-process, single-request `TestClient` call structurally cannot expose. Specifically
targeted the theoretical race flagged (but never exercised) in `ARCHITECTURE_NOTES.md`:
`POST /api/ipd/assign` has no locking. Result: **8 concurrent assignment calls to the same
patient still produced exactly 1 Active assignment** -- the race did not manifest a real
data-integrity bug in this test environment (SQLite's coarse file-level locking likely
serializes enough of the critical section to prevent it in practice; this is not an absolute
guarantee under Postgres's finer-grained row locking, noted in `TEST_NOTES.md`). Also verified:
concurrent vitals recording across 15 simultaneous requests (no data loss), bulk 50-patient
concurrent admission (no ID collisions), 20 simultaneous logins (all distinct tokens), account
lockout under 8 concurrent failed-login attempts (still locks correctly), and a mixed
read/write burst of 50 concurrent requests (zero 5xx responses).

### Added -- large-scale multi-scenario functional coverage (~330 new integration cases)

- `test_clinical_specialty_diversity.py` (140) -- 35 realistic clinical scenarios across every
  specialty requested: trauma/emergency, chronic disease, multiple rare diseases, five cancer
  types, IVF, cosmetic dermatology, infectious disease (dengue, malaria, TB), psychiatry,
  orthopedics, cardiology, neurology, and more, each verified through the real OPD scribe
  pipeline (creation, storage, retrieval, and per-doctor privacy).
- `test_hospital_scale_scenarios.py` (12) -- small single-doctor clinic through a 30-patient,
  10-ward, 3-head-nurse, 10-nurse multi-specialty hospital; a 25-patient trauma-center
  mass-casualty admission surge with rapid-turnover discharge; rural single-operator vs. urban
  fully-role-separated staffing patterns.
- `test_diagnostic_workup_scenarios.py` (16) -- multi-visit diagnostic journeys where a
  diagnosis firms up across several consultations and lab tests narrow down over time (anemia,
  fever of unknown origin, cardiac workup, biliary colic, weight loss/thyroid), plus an
  IPD-linked workup culminating in an accurate discharge summary.

### Added -- curated multi-lingual use-case library with real voice-driven output (new
### `tests/scenarios/`, output saved to `final test output/`)

30 richly detailed use cases (22 OPD consultations + 8 multi-day IPD stays) spanning small
rural clinic to multi-specialty urban hospital to trauma center; trauma/emergency through
chronic disease, rare disease, breast/lung/colorectal/leukemia/prostate cancer, IVF, and
cosmetic dermatology; ~5 to ~25 minute consultations (represented via dialogue length/detail);
and 14 language variants covering the major languages spoken across India (Hindi, Bengali,
Telugu, Marathi, Tamil, Gujarati, Urdu, Kannada, Odia, Malayalam, Punjabi, Assamese, English,
and Hinglish code-switching). Each is driven through the **real voice-simulated UI flow** (mic
-> speech -> Start/Stop Consulting or Process/Save) against a live server -- not a raw API
shortcut -- exactly matching how a doctor or nurse actually uses this system. Outputs
(transcript, structured prescription or discharge summary, daily vitals/notes for IPD cases)
saved to `final test output/<use_case_id>/`, each with a `metadata.json` recording language/
specialty/setting/scale and an `ai_source` field.

**AI content source note:** the configured `GROQ_API_KEY` was found invalid (`401
invalid_api_key`) at the start of this pass and remained so throughout -- the runner
(`tests/scenarios/test_generate_final_outputs.py`) always attempts a real Groq call first, and
currently falls back to this file's curated, clinically-plausible synthetic content per case
(never presented as real model output -- every `metadata.json` and the top-level `_index.json`
say `"ai_source": "synthetic_fallback"` explicitly). The full pipeline -- real voice input,
real HTTP requests, real storage and retrieval -- was exercised for real regardless. Fix
`backend/.env`'s `GROQ_API_KEY` and re-run `pytest tests/scenarios -m e2e` to regenerate every
case with real AI output.

### Added -- convenience feature: print/export for Discharge Summary

Mirrors OPD's existing "Prepare Rx Sheet" print pattern -- a formatted, printable discharge
summary view (modal + `window.print()`) so ward staff can hand patients a physical/PDF copy at
discharge, the same real-world workflow OPD's prescription printing already supports.

### Fixed -- test authoring (found during this pass, not app bugs)

- `tests/e2e/`: two Playwright tests used an ambiguous `.modal-close` selector matching a
  hidden modal earlier in DOM order (same class of mistake as a prior pass, recurring in new
  test files) -- fixed by scoping to the specific modal's id.
- `tests/concurrency/`: several tests initially passed SQLAlchemy ORM objects (not plain ids/
  strings) into `ThreadPoolExecutor` worker functions -- since a single `Session` is not
  thread-safe and every `commit()` expires all its previously-loaded objects, a worker thread's
  first attribute access could trigger a cross-thread lazy-reload race
  (`sqlalchemy.orm.exc.ObjectDeletedError`). Fixed by extracting plain ids/strings in the main
  thread before dispatching to the executor -- a real lesson for writing concurrency tests
  against this codebase's fixtures, not a bug in the application itself.

Total suite: 1309 tests, run `pytest --collect-only -q` for the exact current figure (was 1067
before this part).

## 2026-08-01 pass (part 2): dedicated NursingStation end-to-end testing, ~219 new tests

Follow-up to the same day's HeadNurse pass: complete end-to-end testing of the NursingStation
role (also called the "ward login" -- the front-desk/admission-desk login) at 200-300 dedicated
cases. NursingStation is a deliberately narrow role: confirmed against `backend/app/main.py`
that exactly six endpoints check `is_nursing_station()` -- `create_ipd_patient`,
`update_patient`, `get_ipd_patients`, `get_patient_details`, `get_vitals`, `get_tasks`. It
admits patients and manages administrative patient info (ward/bed/diagnosis/status/discharge)
and can read the ward-wide roster and full chart, but cannot record any clinical data itself
(no vitals, no tasks, no nursing notes, no voice features of any kind) and cannot manage nurse
assignments in any way.

Ran the same UI-audit method used for HeadNurse (cross-check every role-gated element in
`ipd.html` against the backend's actual permission checks) before writing tests. Unlike the
HeadNurse pass, this found **no new UI bugs specific to NursingStation** -- the two fixes made
during the HeadNurse pass (the Admit Patient button, and the `tasks-tab` id collision) already
fully covered NursingStation's shared surfaces, and were confirmed working for this role via
`tests/e2e/test_nursingstation_e2e_workflow.py` rather than needing separate fixes.

### Added -- tests (~219 new cases across 8 new files: 7 integration + 1 e2e)

- `test_nursingstation_full_workflow.py` (12) -- login/session, a complete front-desk day
  (admit -> update -> read chart -> discharge), and cross-org isolation across every
  NursingStation-reachable endpoint.
- `test_nursingstation_permission_boundaries.py` (29) -- every clinical/assignment/admin action
  this role must be denied (vitals, tasks, nursing notes, all four voice endpoints, assign,
  unassign, nurse-workload, every Admin-only action), confirmed even against a patient the
  station itself admitted (proving these are pure role checks, not incidental side effects of
  some other check), plus token edge cases.
- `test_nursingstation_admission_scenarios.py` (42) -- admission is this role's core daily
  duty, so it gets the deepest scenario coverage: every specialty ward, full age range, gender
  variety, unicode names, a 30-patient admission-rush scenario, and immediate cross-role
  visibility (a HeadNurse can assign a nurse to a station-admitted patient with no extra step).
- `test_nursingstation_patient_management.py` (13) -- administrative updates, discharge/
  transfer processing (including the assignment-cascade-close regression check), and
  concurrent editing by NursingStation and HeadNurse on the same patient.
- `test_nursingstation_dashboard_and_read_access.py` (15) -- ward-wide roster parity with
  HeadNurse/Doctor (not just permission to view it -- the exact same data), and read-only
  access to vitals/tasks/notes/consultations regardless of which role authored them.
- `test_nursingstation_denial_consistency.py` (40) -- proves the role check on every denied
  endpoint fires before body validation, for endpoints structured that way (`create_task`,
  `assign_patient`, `unassign_patient` check role as their literal first line, before even
  parsing the request body) -- confirmed even with unparseable-JSON request bodies.
- `test_nursingstation_voice_feature_denial.py` (37) -- denial from every voice-capable
  endpoint confirmed regardless of transcript content (unicode, very long, injection-shaped,
  empty), with an explicit assertion that Groq is never actually invoked for a denied request.
- `test_nursingstation_realistic_scenarios.py` (18) -- multiple front-desk operators sharing
  one organization's shared roster, realistic international-name intake data, emergency
  walk-in minimal-info admission, a representative 15-admission/6-discharge day, and
  readmission of a previously-discharged patient.
- `tests/e2e/test_nursingstation_e2e_workflow.py` (13) -- real-browser confirmation that the
  UI correctly exposes admit/edit/discharge/ward-summary/read-access and correctly hides
  assign/unassign/tasks-nav/nursing-notes-action for this role, including a combined regression
  check that the patient-detail Tasks tab (fixed in the HeadNurse pass) is genuinely visible
  and read-only for NursingStation, and that the HeadNurse-only nurse-workload endpoint is
  never called by this role's normal UI session.

Total dedicated NursingStation coverage: 219 cases, within the requested 200-300 range. Grand
total suite: 1067 tests (1066 passing, 1 intentional `xfail`), up from 848 before this part.

## 2026-08-01 pass: dedicated HeadNurse end-to-end testing, ~237 new tests

Explicit follow-up request: complete end-to-end testing of the HeadNurse role specifically --
every functionality reachable from a HeadNurse login, including voice features, at 200-300
dedicated cases, plus "think as a head nurse" and add convenience features. Audited every
role-gated element in `frontend/ipd.html` against the backend's actual permission rules (the
same method that found the nurse-consult persistence bug in the first pass) before writing any
tests, which surfaced three real bugs -- two UI permission mismatches and one HTML id collision
that silently broke a feature for every role, not just HeadNurse.

### Fixed -- frontend/ipd.html

1. **The "Admit Patient" button was only ever shown for `NursingStation`**, even though
   `POST /api/ipd/patients` has always allowed `HeadNurse` too (`is_head_nurse(current_user) or
   is_nursing_station(current_user)`). A head nurse had zero UI path to admit a patient despite
   having backend permission to do so. Fixed: `applyRolePermissions()` now shows the button for
   both roles.
2. **The "Mark Complete" task button was only ever shown for the assigned `Nurse`**, in both
   places it's rendered (the patient-detail Tasks tab and the global Tasks view), even though
   `PATCH /api/ipd/tasks/{id}` has always allowed `HeadNurse` to update *any* task
   (`is_head_nurse(current_user) or (is_nurse(current_user) and task.nurse_id ==
   current_user["id"])`). A head nurse who created a task -- including one they never assigned
   to a specific nurse -- had no UI path to complete it themselves. Fixed in both locations.
3. **Critical, previously-invisible bug independent of role: an HTML `id` collision silently
   broke the "Tasks" tab inside every patient's detail modal for every user.** The sidebar's
   "Tasks" nav button and the patient-detail modal's Tasks tab-content `<div>` both used
   `id="tasks-tab"`. `document.getElementById('tasks-tab')` always resolves to the *first*
   matching element in DOM order -- the sidebar button, which appears earlier in the HTML --
   so the tab-switch handler's `document.getElementById(btn.dataset.tab).style.display =
   'block'` was toggling the sidebar button's (irrelevant) inline style instead of ever
   revealing the real tab content, which stayed permanently `display:none` after being hidden
   by the same handler's own "hide all tabs" step. No error was ever thrown -- `getElementById`
   just silently returned the wrong element -- so this had no chance of surfacing without
   testing actual click-triggered visibility (a `.count()`-based DOM-presence check, which an
   earlier draft of this pass's own tests used, does not catch it either; only asserting
   `is_visible()` / attempting a real `.click()` does). Found via
   `test_headnurse_can_mark_a_nurses_task_complete_through_the_ui`, which kept timing out with
   "element is not visible" even after correctly identifying the target button. Fixed by
   renaming the sidebar nav button's id to `tasks-nav-btn` (and its one reference in
   `applyRolePermissions()`); the tab-content div keeps `id="tasks-tab"`, matching the
   `data-tab="tasks-tab"` attribute the generic tab-switcher already keys off of.
4. Reassigning a patient (`POST /api/ipd/assign`) no longer leaves the Assign view's "currently
   assigned" dropdown labels stale -- `loadAssignOptions()` now re-runs after a successful
   assign, so a rapid string of reassignments during a shift handoff reflects each change
   immediately instead of only after navigating away and back.

### Added -- HeadNurse convenience features (backend/app/main.py + frontend/ipd.html)

Thought through this from a head nurse's actual daily-oversight perspective (assign/rebalance
the ward, spot who needs attention first, hand off cleanly at shift change):

- **`POST /api/ipd/unassign`** (new endpoint, HeadNurse-only): explicitly closes a patient's
  active nurse assignment without assigning a replacement. Previously the only way to change an
  assignment was `POST /api/ipd/assign`, which requires a `nurse_id` and therefore can't
  represent "nobody" as a state -- e.g. a nurse goes home sick mid-shift with no immediate
  replacement. Surfaced in the UI as an "Unassign Nurse" button in the patient detail modal.
- **`GET /api/ipd/nurse-workload`** (new endpoint, HeadNurse-only): active-patient count per
  nurse in the organization. Surfaced in the Assign view's nurse dropdown (`"nurse@x.com (3
  patients)"`) so a head nurse can see who's already stretched thin before handing them another
  patient, instead of assigning blind.
- **Ward summary stat bar** on the Dashboard (HeadNurse/NursingStation/Doctor only -- a Nurse's
  view is already just their own handful of patients, where ward-wide stats would be noise):
  total patients, unassigned count, abnormal-vitals count, overdue-tasks count, computed
  client-side from data the dashboard already fetches (no new backend load).
- **Priority sorting** of the dashboard patient grid for the same ward-wide roles: patients
  with abnormal vitals surface first, then patients with overdue tasks, then everyone else --
  so a busy ward's most urgent patients don't require scanning the whole grid to find.

### Added -- tests (~237 new cases across 9 new files: 7 integration + 2 e2e)

- `test_headnurse_full_workflow.py` (22) -- login/session, a complete realistic day (admit ->
  assign -> vitals -> task -> note -> complete -> reassign -> discharge), HeadNurse's
  assignment-independence across every check that gates Nurse, and cross-org isolation across
  every HeadNurse-capable endpoint.
- `test_headnurse_permission_boundaries.py` (31) -- every admin-only action HeadNurse must be
  denied, role-escalation-attempt resistance, malformed/adversarial input (including a
  malformed-JSON-body regression check), cross-org/cross-role assignment attempts, and token
  edge cases (expired, refresh-token-as-access-token, signature-tampered role claims).
- `test_headnurse_admit_and_assign_scenarios.py` (31) -- the assignment lifecycle (assign ->
  reassign -> unassign -> reassign), regression coverage for the two new endpoints, and a
  20-patients-across-4-nurses scale scenario.
- `test_headnurse_task_management.py` (24) -- task creation/distribution across nurses, and
  regression coverage for the Mark Complete fix (HeadNurse completing/reopening tasks assigned
  to any nurse, or unassigned).
- `test_headnurse_voice_features.py` (24) + `test_headnurse_voice_input_robustness.py` (51) --
  the full voice feature set (vitals, nursing notes, nurse-consult, voice-to-vitals) driven by
  HeadNurse specifically, including on patients with no nurse assigned at all, type-coercion
  regression checks, unicode/RTL/injection-shaped transcript variety, and a PHI-leakage check.
- `test_headnurse_vitals_and_notes.py` (23) -- manual (non-voice) vitals/notes, correct
  recorder attribution, and full-chart review across multiple recorders and sources.
- `test_headnurse_dashboard_data_scenarios.py` (10) -- roster data correctness under realistic
  ward compositions (combinations of abnormal/overdue/(un)assigned together, 20-patient scale,
  HeadNurse/NursingStation/Doctor ward-wide-view parity vs. Nurse's strict subset view).
- `tests/e2e/test_headnurse_e2e_workflow.py` (14) -- real-browser regression coverage for the
  three UI fixes above, plus the new ward-summary/nurse-workload/unassign features, plus a
  full-session smoke pass across every view with zero tolerated console errors.
- `tests/e2e/test_headnurse_voice_e2e.py` (7) -- the real mic UI end-to-end as HeadNurse,
  including a full speak -> Process -> Save round trip on an unassigned patient.

Total dedicated HeadNurse coverage: 237 cases, within the requested 200-300 range. Grand total
suite: run `pytest --collect-only -q` for the exact current figure (was 611 before this pass).

## 2026-07-31 pass (part 2): dedicated voice-feature hardening, ~214 new tests

Follow-up requested specifically to verify the voice-based features nurses use (mic -> Groq
extraction -> vitals/nursing-note/consult) were actually exercised, not just covered
incidentally. They were not, at real scale: before this part, voice-specific coverage was ~33
cases (enough to catch the persistence and empty-save bugs in part 1, but no systematic sweep
of extraction-result shapes or raw transcript content). Building that sweep surfaced two more
real, previously-unknown crash bugs.

### Fixed -- backend/app/scribe.py

- **`_generate_json` is typed to return `dict` but didn't enforce it.** Valid JSON that happens
  to parse to a list/string/number/bool ("[1, 2, 3]", "true", "42") is not a `json.JSONDecodeError`,
  so it skipped the existing fallback path entirely and was returned as-is. Every caller
  immediately does `result.get(...)`, which crashes with a raw, unhandled `AttributeError` for
  any non-dict type -- reproduced live via `POST /api/ipd/vitals` with a mocked `"[1, 2, 3]"`
  Groq response before fixing. Fixed by routing a non-dict parse result through the same
  `_fallback_extract` path as a JSON decode failure, since it's functionally the same problem
  (the model didn't return usable structured data). This one fix protects all three voice
  callers (`record_vital`, `create_nursing_note`, `nurse_consult`) simultaneously.

### Fixed -- backend/app/main.py

- **`record_vital`'s voice-extraction path crashed on non-numeric field types instead of
  degrading gracefully.** Groq's JSON output isn't schema-enforced -- a vital field can come
  back as a string (`"seventy"`), a list, or other junk instead of a number. Passed straight
  into the `Vital` model's Integer/Float columns, a list crashed the SQLite insert outright
  (`sqlite3.ProgrammingError: type 'list' is not supported`, reproduced live before fixing),
  and a non-numeric string would have silently corrupted later numeric comparisons (the
  abnormal-vital check's `heart_rate > 100` raises `TypeError` comparing `str > int`). Added
  `_coerce_number()`: best-effort parses ints/floats/numeric strings (extracting the first
  number found, e.g. `"72 bpm"` -> `72`), returns `None` for anything unparseable rather than
  raising or passing through garbage. The existing empty-extraction 422 guard now runs on the
  coerced values, so a field that coerces to `None` correctly counts as "not provided."
- **`POST /api/ipd/voice-to-vitals` had no role check at all** -- unlike its siblings
  (`record_vital`, `nurse_consult`, both Nurse/HeadNurse-only), any authenticated user of any
  role (Admin, Doctor, NursingStation) could call this Groq-backed extraction endpoint. Fixed
  to match the same restriction. Low severity (the endpoint persists nothing), but a real
  inconsistency caught while systematically testing every voice-capable endpoint.

### Added -- tests (~214 new cases across 5 new integration files + 1 new e2e file)

- `tests/integration/test_voice_vitals_extraction_scenarios.py` (42) -- realistic full/partial
  vitals dictations, the type-coercion fix (malformed field types from the LLM), unicode/
  code-switched notes, voice_text input variety (XSS/SQL-injection-shaped content, very long
  text, control characters), and malformed-JSON/wrong-shape Groq responses.
- `tests/integration/test_voice_nursing_note_extraction_scenarios.py` (32) -- the SOAP-note
  equivalent: full/partial dictations, malformed field types (safe here since notes are
  f-string-formatted, not typed DB columns), long narrative notes, Hindi dictation, raw-
  transcript round-trip fidelity, and a PHI-leakage check specific to this endpoint (the
  existing `test_phi_leakage.py` only covered the OPD scribe, never this endpoint).
- `tests/integration/test_voice_nurse_consult_extraction_scenarios.py` (41) -- the combined
  vitals+labs+note extraction: multi-item lists, malformed sub-structures (list items missing
  keys, wrong types, `vitals`/`labs` not even a list), and voice-content-specific access
  control (cross-org, unassigned nurse, non-nursing roles).
- `tests/integration/test_voice_to_vitals_endpoint.py` (20) -- regression coverage for the
  role-check fix above, plus the standalone preview endpoint's no-persistence guarantee and
  malformed-response handling.
- `tests/integration/test_voice_input_robustness.py` (71) -- 21 raw-transcript variants (RTL
  Arabic/Urdu, zero-width characters, control characters, mixed Hindi/English/Devanagari
  numerals, repeated-word spam, literal "null"/"undefined" text, nested quotes, etc.) swept
  across all three persisting voice endpoints, plus documentation that the IPD voice endpoints
  have no minimum transcript length (unlike OPD's `/api/scribe`, which enforces 10 chars).
- `tests/e2e/test_ipd_voice_mic_lifecycle.py` (8) -- real-browser mic button lifecycle:
  listening-state toggling, multi-utterance accumulation across separate recognition sessions,
  error-event handling, the empty-textarea Process guard, a full speak -> Process -> edit ->
  Save round trip confirming the nurse's correction (not the raw extraction) is what's
  persisted, modal-reopen state reset, and the role-gated action-button vs. always-present
  read-only tab distinction.

Total voice-specific coverage: ~247 cases (~33 pre-existing + 214 new), within the requested
200-300 range. Grand total test suite: see the count at the top of this file's most recent
entry below (part 1) plus these -- run `pytest --collect-only -q` for the exact current figure.

## 2026-07-31 pass (part 1): ward-workflow hardening (nurse assignment / vitals / discharge), ~250 new tests

Scenario-driven test pass focused specifically on the daily-ward loop this system exists for:
head nurse assigns patients to nurses, nurses record vitals and examine patients throughout
the day, tasks get created and completed, patients eventually discharge. Read every backend
endpoint and both `frontend/ipd.html`/`frontend/admin.html` end to end before writing tests,
which surfaced several real bugs no prior pass had covered (none of the existing 144 tests
touched `POST /api/ipd/nurse-consult` or the nursing-consult Save path at all).

### Fixed -- backend/app/main.py

1. **Critical: `POST /api/ipd/nurse-consult` persisted an unreviewed draft to the database on
   every "Process" click**, before the nurse ever saw or edited it. The real UI flow is
   mic -> Process (extract for review) -> nurse edits -> Save (persists). This endpoint used to
   insert a `Vital` per extracted item (every structured column left null) and a `NursingNote`
   immediately, so every consult left the AI's raw, un-reviewed first draft permanently in the
   chart -- duplicated by the nurse's actually-reviewed version if they went on to Save, and
   left behind as an orphaned ghost record if they didn't (e.g. processed, disliked the draft,
   and closed the modal). Fixed by making the endpoint pure extraction/preview with no DB
   writes; the existing (previously fixed) Save-step calls remain the only persistence path.
   Covered by `tests/integration/test_nurse_consult_no_persistence.py`.
2. **Voice-derived vitals and nursing notes silently saved as blank records on extraction
   failure.** When Groq returns malformed JSON, `scribe._generate_json`'s fallback returns the
   *OPD-shaped* dict (chiefComplaint/hpi/...) regardless of what the caller actually needed --
   so `record_vital` and `create_nursing_note`'s voice paths ended up with every field `None`/
   empty, silently saved with a plain 200 "success", giving a nurse false assurance that a
   vital or note was actually captured. Both endpoints now return 422 when extraction (voice)
   or submission (manual, e.g. a bare `{"patient_id": X}`) yields nothing at all, while still
   accepting genuinely partial data unchanged (a nurse who only reports one vital, or fills in
   one SOAP section, is not penalized -- and `0` is correctly treated as a real reading, not
   "empty"). Covered by `tests/integration/test_voice_extraction_failure_guards.py`.
3. **`create_task`'s `nurse_id` had no existence/role/organization validation**, unlike its
   sibling `assign_patient` which validates all three. A typo'd id, a Doctor's id, or a nurse
   from a different organization was silently stored, producing a task nobody could ever see or
   complete. Now validated the same way `assign_patient` already does (404 "Nurse not found").
   Covered by `tests/integration/test_task_nurse_assignment_validation.py`.
4. **Discharging/transferring a patient never closed their active `NurseAssignment`.** Setting
   `status` away from `"Active"` via `PUT /api/patients/{id}` left the assignment row `Active`
   forever, so a discharged patient kept appearing in that nurse's ward list indefinitely (the
   nurse's own `GET /api/ipd/patients` branch also had no `Patient.status == "Active"` filter,
   compounding it). Fixed: any status change away from `"Active"` now closes the assignment,
   and the nurse-role roster query filters on active status for defense in depth. The frontend
   had no discharge UI at all before this pass -- see below. Covered by
   `tests/integration/test_discharge_workflow.py`.

### Added -- backend/app/main.py (convenience features surfaced by testing the real workflow)

- **`assigned_nurse` visibility**: `GET /api/ipd/patients` and `GET /api/patients/{id}/details`
  now return the currently assigned nurse's `{id, email}` (or `null`), so a head nurse can see
  ward assignment state at a glance instead of having no way at all to tell who's covering a
  given patient. `vitals`/`tasks`/`nursing_notes` items in patient details also carry a
  `nurse_email` alongside the existing raw `nurse_id`. (A bug in this addition -- the lookup
  dict didn't include the assignment's own nurse id when no vitals/tasks/notes existed yet, so
  `assigned_nurse.email` came back `null` -- was caught by
  `test_assigned_patient_shows_nurse_email_in_details` during this same pass and fixed before
  landing.)
- **Overdue-task flagging**: tasks and the patient roster now carry `is_overdue` /
  `overdue_tasks`, computed from `due_date < now() and status != "Completed"`. Previously an
  overdue task looked identical to any other pending task everywhere in the UI.
- Discharge cascade described above.

### Added -- frontend/ipd.html

- **Fixed: nursing-consult Save wrote every vital with all structured columns hardcoded to
  `null`**, stuffing the actual reading into a free-text `notes` string instead
  (`Parameter: HR, Value: 72 bpm`). This meant voice-recorded vitals never fed the
  abnormal-vitals dashboard alert (which only reads the structured columns) and the patient
  chart's vitals tab literally rendered `BP null/null | HR null | Temp null°C`. Added
  `mapVitalsToStructured()`, which parses common parameter names (BP/Blood Pressure, HR/Pulse/
  Heart Rate, Temp/Temperature, SpO2/O2 Sat/Oxygen, RR/Respiratory Rate) into the real numeric
  columns and sends one consolidated `POST /api/ipd/vitals` instead of one call per item with
  everything null; anything unrecognized still falls back to `notes` so no data is lost.
  Reviewed lab results (previously silently dropped once nurse-consult stopped persisting them
  itself, since Save never sent them anywhere) are now folded into the note's Objective section
  on Save. Covered by `tests/e2e/test_ipd_vitals_mapping_and_discharge.py` (drives the real
  mic -> Process -> Save flow through a headless browser and asserts the DB row).
- **Added: Discharge action.** There was no way in the UI to discharge a patient at all --
  `editPatient()` only ever sent `ward`/`bed`/`diagnosis`, never `status`, even though the
  backend already supported it. Added a Discharge button (HeadNurse/NursingStation only, shown
  only while the patient is Active) to the patient detail modal.
- **Added: assigned-nurse display** on dashboard cards, the patient list, the assign dropdown
  (so a head nurse sees who already has the patient before reassigning), and the patient detail
  header; unassigned patients are visually flagged.
- **Added: patient search/filter** box on the Patients list view (name/ward/bed), for wards
  with a large daily roster.
- **Added: overdue-task highlighting** on the dashboard alert banner, patient detail tasks tab,
  and the global Tasks view (sorted overdue-first).
- **Added: nurse email attribution** in place of raw numeric `Nurse #id` in vitals/tasks/
  nursing-notes displays.
- Vitals tab now renders `--` for a missing field instead of the literal string `"null"`.

### Added -- tests (~250 new cases; see individual files for full rationale)

- `tests/integration/test_role_permission_matrix.py` -- full 5-role x 16-endpoint permission
  matrix (Admin/HeadNurse/NursingStation/Nurse/Doctor), plus unauthenticated- and garbage-token
  rejection across 10 endpoints. Pins down exactly who can call what, so a future accidental
  widening/narrowing of a role check shows up as one specific failing case.
- `tests/integration/test_nurse_consult_no_persistence.py` -- regression coverage for fix #1 above.
- `tests/integration/test_voice_extraction_failure_guards.py` -- regression coverage for fix #2.
- `tests/integration/test_task_nurse_assignment_validation.py` -- regression coverage for fix #3.
- `tests/integration/test_discharge_workflow.py` -- regression coverage for fix #4.
- `tests/integration/test_assigned_nurse_visibility.py` -- the assigned-nurse and overdue-task
  features above, including the self-caught bug fix noted above.
- `tests/integration/test_ward_daily_scenarios.py` -- realistic multi-step ward-day sequences:
  bulk admission, distributing patients across several nurses, shift-handoff reassignment,
  doctor read-only rounds, multiple wards, duplicate patient names disambiguated by bed, a full
  patient-day (admit -> assign -> vitals x3 -> task -> complete -> note), and two documented
  (not invented) clinical-safety gaps found while exercising the abnormal-vitals flag: it never
  checks `oxygen_sat` at all, and has no *low* threshold for `heart_rate` (severe bradycardia
  goes unflagged). See TEST_NOTES.md.
- `tests/integration/test_vitals_recording_scenarios.py` -- decimal precision, the `limit`
  query param on `GET /api/ipd/vitals/{id}`, cross-patient isolation, unicode/very-long notes,
  extreme out-of-range values (including `oxygen_sat > 100`), and malformed `patient_id`
  handling.
- `tests/integration/test_admission_scenarios.py` -- unicode names/wards (Devanagari), age
  boundaries (0, negative, 150), bed/gender format variety, empty/whitespace-only names, and a
  documented SQLite-vs-Postgres field-length parity gap (see TEST_NOTES.md).
- `tests/integration/test_task_lifecycle_scenarios.py` -- completion, reopening, cross-nurse
  authorization, arbitrary status strings (no enum constraint), and ISO due-date format variety.
- `tests/e2e/test_ipd_vitals_mapping_and_discharge.py` -- real-browser coverage for the two
  frontend/ipd.html fixes above.

Total: 397 tests (396 passing, 1 intentional `xfail`), up from 144.

### Dev environment

- Pinned `httpx>=0.27,<0.28` in `requirements-dev.txt`. `httpx==0.28.1` (the latest release at
  the time of this pass) removed the `app=` shortcut constructor argument that this project's
  pinned `starlette==0.27.0`/`fastapi==0.104.1` `TestClient` relies on, so every single test
  failed at collection with `TypeError: Client.__init__() got an unexpected keyword argument
  'app'` before this pin -- a dev-dependency drift issue, not an application bug.

## Fixed: voice consultation data not saving in OPD/IPD (production bug report)

Reported symptom: "start consulting, speak, stop consulting -- the data was not getting
filled in, in both OPD and IPD." These are pure-frontend JS bugs, invisible to the
backend-only pytest suite -- found and confirmed by driving the real `frontend/*.html` pages
through a real headless browser (Playwright) with a mocked `SpeechRecognition`/
`getUserMedia`, simulating an actual multi-utterance voice consultation end-to-end against a
live local server. Each bug below was reproduced live (failing test) before being fixed
(passing test) -- see `tests/e2e/`.

1. **`frontend/opd.html`: guaranteed crash on access-token refresh.** `apiRequest()` declared
   `const accessToken` but reassigned it (`accessToken = data.access_token`) after a
   successful token refresh, throwing `TypeError: Assignment to constant variable` on every
   API call made after the 15-minute access token expires -- including the one that submits
   the consultation transcript to `/api/scribe` when "Stop Consulting" is clicked. A real
   consultation (see the multi-page real transcripts in `tests/from_data/fixtures.py`) very
   plausibly runs long enough to hit this. The failure showed up to the doctor only as a
   generic "❌ Network error" with no draft populated -- and recurred on every subsequent
   action on the page, not just once, since the in-memory token never actually updated.
   Fixed: `const accessToken` → `let accessToken`.
2. **`frontend/opd.html`: submitted transcript was silently doubled.** The live-transcript
   accumulator kept two variables, `accumulatedTranscript` and `currentFinal`, that were
   always incremented by the exact same "final" speech chunk and then concatenated together
   for display (`accumulatedTranscript + currentFinal + interim`) -- so every consultation
   sent to the AI scribe was the entire conversation duplicated back-to-back. Besides wasting
   tokens/cost, a long enough real consultation doubled this way could plausibly exceed a
   model's context window and silently degrade to scribe.py's all-empty fallback draft (a
   "success" response with nothing filled in -- indistinguishable from the reported symptom).
   Fixed: removed the redundant `currentFinal` variable; only `accumulatedTranscript` is
   tracked and displayed now.
3. **`frontend/ipd.html`: "Save Nursing Notes" silently discarded the nurse's edits.** The
   nursing-consult flow is mic → "Process" (LLM extracts vitals/labs/a draft SOAP note for
   review) → nurse edits the note → "Save". The Save handler always resent the raw
   `voice_text` alongside the reviewed note; `backend/app/main.py`'s `create_nursing_note`
   re-runs its own LLM extraction whenever `voice_text` is present, ignoring
   `subjective`/`objective`/`assessment`/`plan` from the request entirely -- so Save
   persisted a second, independently-generated (and generally different) note instead of
   what the nurse actually reviewed and edited, while the UI still reported "saved
   successfully". Also wasted a second Groq call every save. Fixed: Save no longer sends
   `voice_text`; it parses the reviewed SOAP-labeled textarea back into its four fields and
   sends those, so exactly what was reviewed is what gets persisted.
4. **`frontend/ipd.html` / `frontend/admin.html`: stale in-memory access token after
   refresh** (milder sibling of bug 1 -- caught while auditing the same pattern across all
   three frontend pages). Neither file's `apiRequest()` updated the in-memory `accessToken`
   after a successful refresh (only `localStorage`), so every subsequent request on the page
   kept re-triggering an unnecessary 401 → refresh → retry round trip instead of using the
   already-refreshed token. Not a crash (these files never reassign the `const`), but wasteful
   and inconsistent with the intent. Fixed the same way: `const` → `let`, plus the missing
   `accessToken = data.access_token` assignment on refresh.

### Added: `tests/e2e/` -- real-browser regression suite

New `pytest.mark.e2e` tests (`playwright` + `pytest-playwright`, added to
`requirements-dev.txt`; run `playwright install chromium` once after installing) drive the
actual frontend HTML/JS against a real live local server with `SpeechRecognition`/
`getUserMedia` mocked, catching exactly the class of bug above that a backend-only test suite
structurally cannot see:

- `test_opd_voice_consultation.py` — fresh-token happy path; access-token-expires-mid-session
  regression test (bug 1); transcript-not-duplicated regression test (bug 2).
- `test_ipd_nursing_note_save.py` — Save persists the nurse's reviewed/edited note, not a
  second silent LLM re-derivation (bug 3), and asserts exactly one Groq call is made per save.

Each regression test was verified to actually fail against the pre-fix code before being
committed (not just pass against the fix) -- see the "reintroduce the bug, confirm red;
restore, confirm green" step in the investigation.

## Deployment: Render-only (removed Vercel target)

Committed to Render as the sole deployment target. Removed:

- `api/index.py` and `api/__pycache__/` — the hand-maintained Vercel serverless duplicate of
  `backend/app`. Retroactively resolves the "delete vs. sync" decision flagged in the entry
  below and in ARCHITECTURE_NOTES.md section 1 — including the drift issues that file carried
  (missing doctor IPD access, no `/api/drug-interactions`, a different `ScribeEngine`
  implementation, and an unrelated live `admin_create_user` `KeyError` bug) which are moot now
  that the file is gone.
- `vercel.json` — Vercel build/routing config.
- `.vercel/` — local Vercel CLI project link (was never git-tracked; `.gitignore`'s now-unused
  `.vercel` entry removed too).

No changes needed to `backend/app/main.py` itself: it already serves `frontend/**` directly
(added in an earlier "Converted to render specific version" pass), so it remains the single
deployable service with no code changes required for this cleanup.

## This pass

### Added

- `tests/from_data/test_opd_scribe_pipeline.py`: 39 tests driving the OPD scribe pipeline
  (`POST /api/scribe`) with all 12 real consultation transcripts extracted from `./data`'s
  three PDFs, each traceable to its `source_pdf` + `case_label`. Covers full-length real-world
  input handling, raw-transcript persistence fidelity (including unicode `°`/`²` characters),
  per-user consultation privacy, and fallback behavior on the longest fixture transcript.
- `tests/integration/test_multi_tenant_isolation.py`: 9 tests proving (then, after the fix,
  confirming the absence of) cross-organization PHI leakage across the entire IPD module.
- `tests/integration/test_ipd_edge_cases.py`: 20 tests covering missing/malformed required
  fields, abnormal-vital threshold boundaries (exact 140/90/100/38 vs. one unit above), the
  single-active-nurse-assignment invariant, and documented (unfixed) negative-vital-value gap.
- `tests/integration/test_scribe_input_edge_cases.py`: 9 tests covering the transcript
  minimum-length boundary (9 vs. 10 chars after strip), missing/null/non-string transcript,
  and malformed JSON request bodies.
- `tests/integration/test_auth_edge_cases.py`: 8 tests covering the 5-failed-attempt login
  lockout boundary, generic-invalid-credentials response (no user-enumeration), the admin
  password-reset endpoint (regression test for the fix below), and a documented
  (xfail, not fixed) email-case-sensitivity duplicate-account gap.
- `tests/integration/test_phi_leakage.py`: 5 tests confirming raw transcript content, submitted
  passwords, and newly-set passwords never appear in HTTP error/response bodies.

Total: 140 tests (98 pre-existing from an earlier pass + 42 new), 139 passing + 1 intentional
`xfail`.

### Fixed -- backend/app/main.py

- **Critical: cross-tenant PHI/data leak across the entire IPD module.** None of
  `get_ipd_patients`, `get_patient_details`, `update_patient`, `assign_patient`, `record_vital`,
  `get_vitals`, `create_task`, `update_task`, `get_tasks`, `nurse_consult`, or
  `create_nursing_note` filtered by the caller's `organization_id` -- any authenticated
  HeadNurse/NursingStation/Doctor/Nurse could view or modify another organization's patient
  roster, vitals, tasks, and nursing notes, and a HeadNurse could assign another org's patient
  to a nurse. Every patient-touching query in these endpoints now filters on
  `organization_id`, returning 404 (not 403) for cross-tenant access so existence of another
  org's resource is never confirmed to the caller.
- **Missing required-field validation** in `create_ipd_patient`, `assign_patient`,
  `create_task`, and `record_vital` -- these previously indexed straight into the request
  dict (`data["patient_id"]`, etc.), producing a raw 500 for any client that omitted a field
  instead of a clean 400.
- **`create_task`'s `due_date` parsing** (`datetime.fromisoformat`) crashed with an unhandled
  `ValueError` on a malformed date string; now caught and returned as a 400.
- **`reset_user_password` accepted the new password as a query-string parameter**
  (`new_password: str` as a bare FastAPI path/query param, not part of the JSON body) --
  meaning an admin's plaintext password-reset value would land in web server / reverse proxy
  access logs and browser history. Moved to the JSON request body, consistent with every other
  write endpoint. (No frontend page called this endpoint, so the contract change is safe.)
- **`POST /api/scribe` crashed with a raw 500** on a non-string `transcript` field (`'int'
  object has no attribute 'strip'`) instead of a 400.
- **Malformed JSON request bodies crashed 8+ endpoints with a raw 500** instead of a clean 400
  (`json.JSONDecodeError` from `await request.json()` was either uncaught or swallowed into a
  generic 500 handler). Added a global `json.JSONDecodeError` exception handler for endpoints
  with no local try/except, and an explicit `except json.JSONDecodeError` clause (before the
  generic `except Exception`) in the six endpoints that already wrap their body in a
  try/except (`register`, `login`, `refresh`, `scribe_transcript`, `clinical_helper`,
  `translate_prescription`).

### Fixed -- api/index.py (Vercel serverless duplicate; file removed in a later entry, see top of this changelog)

Mirrored the cross-tenant isolation fix, the missing-required-field validation, and the
query-string password fix into this file's equivalent endpoints, since it's a live production
deployment target with the identical defects. Verified with a manual isolated `TestClient`
smoke run (throwaway SQLite DB, two-organization cross-tenant scenario) rather than pytest --
see TEST_NOTES.md section 7 for why this file had no automated coverage, and for a
separately-discovered, not-fixed, pre-existing `KeyError` bug in its `admin_create_user`
endpoint found while smoke-testing.

### Documented, not fixed (see TEST_NOTES.md for full reasoning)

- Vital sign units are untracked in the schema (Celsius/Fahrenheit ambiguity).
- No physiological-range validation on vitals (negative/impossible values accepted).
- Email comparison is case-sensitive, allowing duplicate identities differing only by case.
- Password complexity's `[A-Z]`/`[a-z]` checks are ASCII-only (Unicode letters don't satisfy
  the case requirements).
- `Consultation.patient_id` has no FK-existence check (believed intentional OPD/IPD decoupling).

## Earlier pass (prior session, inherited)

- Initial test framework setup (pytest, conftest.py with the DATABASE_URL safety guard,
  requirements-dev.txt, pytest.ini).
- ARCHITECTURE_NOTES.md authored; `api/index.py` drift from `backend/app` first documented.
- `tests/from_data/fixtures.py`: all 12 cases extracted from the three source PDFs.
- `tests/unit/test_password_complexity.py`, `test_jwt_tokens.py`, `test_scribe_json_parsing.py`,
  `tests/test_smoke.py`: 50 unit tests for pure-function/deterministic logic (password rules,
  JWT create/decode, scribe JSON parsing/fallback).
