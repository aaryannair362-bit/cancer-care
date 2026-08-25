# ARCHITECTURE_NOTES.md

Living map of the AIVANA Hospital System codebase. Updated as understanding evolves.
Do not re-derive this from scratch each session — read this first, then verify anything
load-bearing against current source before acting on it.

## 1. What this system is

A small multi-tenant hospital/clinic management system, deployed as a single service on
**Render**:

- **`backend/app/`** — a FastAPI app that runs as a normal long-lived process (`uvicorn
  app.main:app`), backed by Postgres in production (SQLite fallback for local dev). It also
  serves `frontend/**` directly (see `frontend_dir` mount in `main.py`), so there is exactly
  one deployable service — no separate static host or serverless function split.

**Resolved (previously flagged as an open decision):** this project previously also shipped a
second, independent copy of the whole application (`api/index.py`) hand-maintained for
Vercel's serverless Python runtime, plus `vercel.json`. That copy had already drifted from
`backend/app` in several ways (different `ScribeEngine` implementation, different password
complexity rules, doctors not granted IPD access, a `POST /api/drug-interactions` endpoint
that didn't exist there at all, and a live `KeyError` bug in `admin_create_user`). The project
owner decided to commit to Render as the sole deployment target, so `api/index.py`,
`vercel.json`, and the local `.vercel/` CLI link were removed rather than kept in sync. If
Vercel deployment is ever wanted again, the correct approach is a thin serverless handler that
imports `backend.app.main:app` directly — not a hand-maintained duplicate.

## 2. Entry points

- `backend/app/main.py` — FastAPI app, all routes, DB session wiring, default-admin bootstrap,
  and static serving of `frontend/**`. This is the only server entry point.
- `frontend/*.html` — static, vanilla-JS pages (no build step), served by FastAPI's
  `StaticFiles` mount (`/static`, for non-`.html` assets like `frontend/js/ipd-shared.js`) plus
  a `GET /{filename}.html` catch-all in `backend/app/main.py` (so `opd.html` etc. resolve at
  root, e.g. `/opd.html`, not `/static/opd.html`):
  - `index.html` — login/landing. Post-login redirect: `Doctor` → `opd.html`, `HeadNurse` →
    `headnurse.html`, `Nurse`/`NursingStation` → `ipd.html`, `Admin` → `admin.html`.
  - `admin.html` — org/user administration; mirrors the same role-redirect for a non-Admin who
    lands here. Also hosts the medicine/lab-test custom-data admin UI (2026-08-03 pass).
  - `opd.html` — outpatient workflow, restructured 2026-08-03 into a step wizard (Setup ->
    Transcript -> Clinical Note -> Interactions -> Prescription) wrapped around the same real
    pipeline: voice/text transcript → AI scribe → structured prescription draft → save as
    `Consultation`. `#patient-select` stays the real state-holder behind a patient-card grid,
    kept non-`display:none` (a 1x1px visually-hidden pattern) since several e2e tests drive it
    directly via Playwright's `select_option()`, which requires a non-zero-bounding-box element.
  - `ipd.html` — inpatient workflow for `Nurse`/`NursingStation` (and Doctor's ward-round modal):
    patient admission, vitals, tasks, nursing notes. Restructured 2026-08-03: the patient-detail
    modal is now a slide-out right-side drawer (CSS only) with 6 tabs (Overview/Vitals/
    Medication/Tasks/Nursing Notes/Discharge Summary), plus a new Alerts view and a Task
    List/Kanban toggle. HeadNurse-only features (Assign view, Create-Task modal, Unassign
    button) were removed from this file in the same pass — see `headnurse.html` below.
    **HTML ids must stay unique within this file**: until fixed 2026-08-01, the sidebar's
    "Tasks" nav button and the patient-detail modal's Tasks tab-content `<div>` both used
    `id="tasks-tab"`, and `getElementById` silently resolved every lookup to the first (sidebar)
    element — permanently breaking the modal's Tasks tab for every role, with no error ever
    thrown. The sidebar nav button is now `id="tasks-nav-btn"`.
  - `headnurse.html` (new, 2026-08-03) — HeadNurse's own dedicated page (previously only
    role-gated sections inside `ipd.html`). Sidebar: Dashboard (real KPI tiles from
    `GET /api/ipd/dashboard-summary`) / Patients / Assign / Tasks / Calendar (editable weekly
    shift grid, `GET`/`PUT /api/ipd/shifts`) / Reports (`GET /api/ipd/reports` charts). Shares
    `frontend/js/ipd-shared.js` with `ipd.html` for the stateless utility layer
    (`apiRequest`/`closeModal`/`taskTypeBadge`); the patient-drawer/admit/task-modal logic is
    intentionally duplicated between the two files rather than abstracted, since it's too
    stateful to share safely without a real module system (no build step in this project).

## 3. Core modules (backend/app)

- **`config.py`** — `pydantic-settings` `Settings` object. Reads `backend/.env`
  (gitignored, confirmed). Key fields: `DATABASE_URL` (**production value is a real Postgres
  connection string** — see §5, this matters a lot for testing), `SECRET_KEY`, JWT algorithm/
  expiry, `GROQ_API_KEY`, `GROQ_MODEL`.
- **`models.py`** — SQLAlchemy declarative models: `Organization`, `User`, `AuditLog`,
  `PasswordHistory`, `Consultation`, `Patient`, `NurseAssignment`, `Vital`, `Task`,
  `NursingNote`, `DischargeSummary` (added 2026-08-01 — see section 5), `Ward` and `NurseShift`
  (added 2026-08-03 — see section 5). Also present but not covered by this document: a
  `Drug`/`DrugBatch`/`DispensingRecord`/`ControlledDrugRegisterEntry` pharmacy inventory group,
  predating this document's most recent update — verify current wiring against `main.py` before
  relying on any assumption about its behavior. No `ON DELETE`/cascade rules defined.
  `Consultation.patient_id` is a foreign
  key with no existence check (believed intentional OPD/IPD decoupling, see TEST_NOTES.md
  section 8). `Vital`/`Task`/`NursingNote`/`NurseAssignment`'s `patient_id` **is now validated**
  in every `main.py` endpoint that creates or reads them — each first loads the `Patient` row
  scoped to `Patient.organization_id == current_user.get("organization_id")` and 404s if it's
  missing or belongs to another organization (fixed this pass, see CHANGELOG.md; previously
  these queries had no organization scoping at all, a cross-tenant PHI leak covered by
  `tests/integration/test_multi_tenant_isolation.py`).
- **`auth.py`** — password hashing (`pbkdf2_sha256` via passlib), `validate_password_complexity`
  (pure function, see rules below), JWT create/decode (`python-jose`), `get_current_user`
  FastAPI dependency, `log_audit` (writes `AuditLog` rows — every audited action's `email` and
  `resource` string is stored in plaintext; no PHI redaction is applied to `details`), and
  role-check helpers (`is_admin`, `is_head_nurse`, `is_nursing_station`, `is_nurse`; `is_doctor`
  lives in `main.py` instead of `auth.py` — inconsistent but not a bug).

  **User-management endpoints in `main.py` (`get_users`, `update_user_role`,
  `reset_user_password`) are now organization-scoped (fixed 2026-08-01)** — until this pass
  they had no `organization_id` filter at all, unlike every IPD endpoint. This was the most
  severe finding of the entire engagement: any Admin could view every user across every
  organization, change any user's role in any other organization, or **reset any other
  organization's user's password** (full cross-tenant account takeover). See CHANGELOG.md's
  2026-08-01 part 3 entry and `tests/integration/test_user_management_multi_tenant_isolation.py`.

  Password complexity rules (`validate_password_complexity`, `backend/app/auth.py:22`):
  length 12–128, requires upper/lower/digit/special char, rejects 3-char ascending sequences
  (case-insensitive, e.g. `abc`/`123`), rejects 3-identical-char runs (`aaa`), rejects substrings
  `qwerty`/`asdfg`/`zxcvb`/`password`/`admin` (case-insensitive), rejects passwords containing the
  local part of the user's email (case-insensitive).

- **`scribe.py`** — `ScribeEngine`, a thin wrapper around Groq's OpenAI-compatible chat-completions
  REST API (`requests.post`, no SDK). Responsible for:
  - `scribe_transcript(transcript)` — the OPD "AI scribe" core: turns a raw doctor–patient
    conversation transcript into a structured prescription draft (`chiefComplaint`, `hpi`,
    `primaryDiagnosis`, `differentialDiagnosis`, `medications[]`, `advice`, `labTests[]`).
    Always backfills missing/`None` keys with the default empty structure — **never raises**
    even if the LLM call or JSON parse fails (falls through to `_fallback_extract`, worst case
    returns all-empty defaults). This means a Groq outage degrades to an empty prescription
    draft rather than a 500 — a deliberate-looking but *undocumented* safety behavior worth
    flagging: the OPD user gets a silently-empty draft with no indication the AI actually failed.
  - `clinical_helper(current_draft, query)` — free-text advice, returns raw LLM string (no
    structure, no persistence).
  - `translate_prescription(draft, target_language)` — passthrough if `target_language ==
    "English"`, else re-runs the draft through the LLM for translation with the same
    default-backfill behavior.
  - `_generate_json` — strips ```json / ``` code fences, `json.loads`s the result, and on
    `JSONDecodeError` falls back to a crude regex/heading-based `_fallback_extract`.
    **Resolved (previously flagged as a PHI-in-logs concern):** Groq request/response bodies
    and parse failures are logged via `logger.debug(...)`, not `print(...)` — off by default at
    Python's default `WARNING` level, so full transcripts are not written to server logs unless
    an operator explicitly opts into debug-level logging.
    **Also enforces its `-> dict` return type as of the 2026-07-31 voice-hardening pass**: valid
    JSON that happens to parse to a list/string/number (e.g. Groq returning `"[1, 2, 3]"`) used
    to skip the `JSONDecodeError` fallback entirely and get returned as-is, crashing every
    caller's immediate `result.get(...)` with a raw `AttributeError` — reproduced live via
    `POST /api/ipd/vitals` before fixing. A non-dict parse result is now routed through
    `_fallback_extract` the same as a decode failure, protecting every caller (OPD scribe, all
    three IPD voice endpoints) at once.
  - **This function's shape is used by three separate IPD voice features** (all consumed by
    `POST /api/ipd/vitals`, `POST /api/nursing-notes`, `POST /api/ipd/nurse-consult`, and the
    standalone `POST /api/ipd/voice-to-vitals` preview endpoint), not just the OPD scribe
    pipeline described below — the "never raises, falls back to empty" contract applies equally
    to a nurse's voice-recorded vitals as to a doctor's dictated consultation. `record_vital`
    additionally runs the result through `_coerce_number()` (added the same pass) before it
    ever reaches a numeric DB column, since Groq's output isn't schema-enforced and a wrong
    type (a list where a number is expected) crashed the insert outright before that fix.
  - `is_available()` — pings Groq's `/models` endpoint; used only by `GET /api/health`.

## 4. Data flow — OPD (Outpatient) scribe pipeline

1. Frontend (`opd.html`) posts `{transcript, patient_id?}` to `POST /api/scribe`.
2. `main.py` rejects if `transcript` missing or `len(transcript.strip()) < 10`.
3. `scribe.scribe_transcript(transcript)` calls Groq, parses/backfills JSON.
4. A `Consultation` row is always inserted (case_id = `YYYYMMDD-<6 hex>`), storing the raw
   transcript, the structured result, and rough token/latency accounting
   (`len(text)//4` as a token proxy — not a real tokenizer, purely a display estimate).
5. Result returned to frontend as-is (not the persisted row).

**Resolved (previously an AI call):** `POST /api/drug-interactions` now matches a list of
`{drugName, ...}` medications against `tasks_engine.py`'s small, explicit, hand-curated
`KNOWN_INTERACTIONS` table (~20 well-established pairs, case-insensitive substring match) —
not a Groq call. Deliberate, disclosed change: a curated safety net beats an LLM hallucinating
interactions, with no formulary/interaction database to ground a real lookup against. Same
function (`check_drug_interactions`) also backs the interaction check re-run at OPD consultation
finalize time. A sibling `check_allergy_conflicts` does the same substring-match approach
against `Patient.allergies`.

## 5. Data flow — IPD (Inpatient) workflow

Roles: `Admin`, `HeadNurse`, `NursingStation`, `Nurse`, `Doctor` (string `role` on `User`,
compared via case-sensitive equality helpers — a typo'd role string silently fails every
permission check rather than erroring, e.g. `"nurse"` lowercase would never match `is_nurse`).

**`NursingStation`** (also referred to as the "ward login" — the front-desk/admission-desk
role) is deliberately the narrowest clinical-adjacent role. Confirmed by grepping every
`is_nursing_station()` call site in `main.py` (2026-08-01): exactly six endpoints allow it —
`create_ipd_patient` (admit), `update_patient` (administrative edits + discharge/transfer),
`get_ipd_patients` (ward-wide roster, same view as HeadNurse/Doctor), `get_patient_details`
(full chart, read-only), `get_vitals`, `get_tasks` (read-only). It cannot record any clinical
data (`record_vital`, `create_task`, `update_task`, `create_nursing_note`, `nurse_consult`,
`voice_to_vitals` all exclude it) and cannot touch nurse assignments (`assign_patient`,
`unassign_patient`, `nurse_workload` are HeadNurse-only). See
`tests/integration/test_nursingstation_*.py` and `tests/e2e/test_nursingstation_e2e_workflow.py`.

- `HeadNurse`/`NursingStation` admit patients (`POST /api/ipd/patients`) and assign nurses
  (`POST /api/ipd/assign`, HeadNurse only) — assignment closes any prior `Active` assignment
  for that patient before creating the new one (single active nurse per patient, enforced at
  the query level, not a DB constraint; no locking). **This race was tested live under real
  concurrency in the 2026-08-01 pass** (`tests/concurrency/`, real simultaneous HTTP requests
  against a live server) and did not reproduce a double-assignment under SQLite — see
  TEST_NOTES.md section 14 for the important caveat that this doesn't guarantee the same under
  Postgres's finer-grained locking in production.
  `POST /api/ipd/unassign` (HeadNurse only, added 2026-08-01) closes the active assignment
  without creating a replacement, representing "nobody currently assigned" as an explicit state
  (previously only reachable by reassigning to *someone*). `GET /api/ipd/nurse-workload`
  (HeadNurse only, same pass) returns each of the org's nurses' current active-patient count,
  used by the Assign view so a head nurse can see load before assigning more.
- A `Nurse` may only see/act on patients with an `Active` `NurseAssignment` row for their own
  `user_id`; every nurse-scoped endpoint re-checks this per-request (repeated ~6 times near-
  identically across `main.py` — duplication noted, not fixed, since behavior is consistent).
- **Discharge/transfer** (`PUT /api/patients/{id}` with `status` set to anything other than
  `"Active"`) now cascades to close any `Active` `NurseAssignment` for that patient (fixed this
  pass — previously the assignment was never closed, so a discharged patient kept appearing in
  that nurse's ward list forever). `GET /api/ipd/patients`'s nurse-role branch also filters on
  `Patient.status == "Active"` directly now, for defense in depth. There is still no dedicated
  "discharge" endpoint — it's just a `status` update on the existing patient-edit endpoint, now
  exposed in `ipd.html` as a Discharge button (HeadNurse/NursingStation only).
- **Discharge Summary (new feature, 2026-08-01)**: `POST`/`GET /api/ipd/patients/{id}/discharge-summary`
  (HeadNurse/NursingStation/Doctor to generate; same viewers as the rest of the chart — including
  an assigned Nurse — to read). Assembles the patient's full IPD record (vitals in chronological
  order, all nursing notes, tasks, and any linked OPD `Consultation` rows) into a discharge
  document via **`discharge_summary.py`** (a separate module, added later — deterministic
  template assembly of already-recorded chart data, no AI/network dependency; fully replaces
  the earlier `scribe.generate_discharge_summary()` LLM path this section used to describe),
  persisted to the new `DischargeSummary` table (one row per generation — regenerating creates
  a new row, `GET` always returns the latest). Same never-raises-always-backfills contract as `scribe_transcript`,
  plus the same "422 if every field came back empty" guard used elsewhere. Distinct from the
  discharge *action* above (`PUT /api/patients/{id}` with `status: "Discharged"`) — generating a
  summary doesn't discharge the patient and discharging doesn't require a summary; the UI's new
  "Discharge Summary" tab in the patient detail modal exposes both as separate actions, plus a
  print/export view mirroring OPD's existing prescription-print pattern.
- **`assigned_nurse` visibility** (added this pass): `GET /api/ipd/patients` and
  `GET /api/patients/{id}/details` both return the patient's current `Active` assignment as
  `{id, email}` (or `null`), and `vitals`/`tasks`/`nursing_notes` items in patient details carry
  a `nurse_email` alongside the raw `nurse_id`. Previously there was no way at all — via API or
  UI — to see which nurse was covering a given patient without querying the DB directly.
- **`is_overdue`/`overdue_tasks`** (added this pass): tasks and the roster now expose whether a
  task's `due_date` has passed while `status != "Completed"`. Purely computed at read time
  (`due_date < datetime.utcnow()`), not stored.
- Vitals (`POST /api/ipd/vitals`) and nursing notes (`POST /api/nursing-notes`) can optionally
  be derived from a free-text `voice_text` via Groq JSON-extraction. Both now reject (`422`)
  when extraction/submission yields nothing usable at all (every vital field `None` and no
  notes; all four SOAP fields blank) — fixed this pass, previously silently saved as a blank
  record with a 200 "success", regardless of whether the empty result came from the voice path
  or a bare manual POST. Partial data (a nurse who only mentions one vital, or a `0` reading)
  is unaffected.
- **`POST /api/ipd/nurse-consult`** is a **pure preview/extraction endpoint — it writes nothing
  to the database.** Fixed this pass: it used to insert a `Vital` per extracted item (columns
  all null) and a `NursingNote` immediately on every "Process" click, before the nurse ever
  reviewed the draft — duplicated by the real, reviewed save if the nurse went on to click
  "Save" (a separate, still-persisting pair of calls: `POST /api/ipd/vitals` +
  `POST /api/nursing-notes`), and left as an orphaned, un-reviewed ghost record in the chart if
  they didn't. See CHANGELOG.md for the full story.
- **"Abnormal" vital flagging** (`GET /api/ipd/patients`): a patient's latest vital is flagged
  abnormal if `bp_systolic > 140` OR `bp_diastolic > 90` OR `heart_rate > 100` OR
  `heart_rate < 60` OR `temperature > 38` OR `oxygen_sat < 92`
  (Celsius assumed, unvalidated — nothing stops a Fahrenheit value like
  `98.6` from being stored and silently read as "normal" under the 38 threshold, or a genuinely
  high Fahrenheit temp like `102` being flagged even though the threshold is Celsius-calibrated
  — **no units field exists on `Vital`**, this is a real correctness risk for a clinical system
  and is covered in TEST_NOTES.md rather than silently "fixed" by guessing an intended unit).
  Thresholds use strict inequalities, i.e. exactly `140/90/100/60/38.0/92` are **not** flagged —
  boundary behavior is tested explicitly. `oxygen_sat < 92` and `heart_rate < 60` were added to
  close a real patient-safety gap (a dangerously low SpO2 or severe bradycardia used to be
  silently "normal" on the dashboard) — see TEST_NOTES.md section 10 for the standard-textbook
  thresholds used and the still-open recommendation to get clinical sign-off on a full
  NEWS2/MEWS-style weighted score rather than these single-vital cutoffs.

- **Ward capacity, nurse shifts, alerts, reports (added 2026-08-03)**: `Ward` is a per-org
  `(name, bed_capacity)` row, matched case-insensitively against the existing free-text
  `Patient.ward` — no FK, no `Bed` entity, so an org with no `Ward` rows configured keeps
  admitting normally (the capacity check in `create_ipd_patient` is simply skipped). Occupancy
  is always a live `COUNT` of `Active` patients matching that ward name, never a stored/cached
  number. `POST /api/ipd/patients` also now validates age (`0 <= age <= 130`) and rejects a
  same-ward same-bed-string conflict, and gates a case-insensitive duplicate-*active*-name match
  behind a `confirm_duplicate: true` resubmit (409 on the first attempt) rather than hard-
  blocking a genuinely different same-named patient forever. `GET/PUT /api/ipd/shifts`
  (HeadNurse-only) is a real, editable per-org nurse x date shift grid (`NurseShift`, unique on
  `(nurse_id, shift_date)`, `shift_type` one of `Morning|Evening|Night|Off`). `GET
  /api/ipd/alerts` reuses the exact same roster-building helpers `GET /api/ipd/patients` already
  uses (`_resolve_ipd_patients_for_role`/`_build_ipd_roster`) to flatten abnormal-vitals/overdue-
  task entries into one paginated feed, so the two views can never silently disagree. `GET
  /api/ipd/reports`/`GET /api/ipd/dashboard-summary` (HeadNurse-only) are real aggregates over
  `Task`/`Patient` rows — diagnosis distribution is top-N *raw* `Patient.diagnosis` strings by
  count, not a fixed invented taxonomy, since that field is free text with no real categories.

## 6. Auth & session model

JWT access tokens (15 min default) + refresh tokens (7 days default), `HS256`, secret from
`.env`. Login lockout: 5 failed attempts locks the account for 30 minutes
(`user.status = "Locked"`, `lock_until`), auto-unlocks (and resets counter) on the next login
attempt once `lock_until` has passed. No lockout on `/api/auth/register` (unlimited attempts to
register with different emails — not a target of this test pass, no rate limiting exists
anywhere in the app; flagged, not fixed, since adding rate limiting is a scope decision).

## 7. Testing status

Before the first test-writing pass: zero automated tests, no test framework configured, no CI.
Everything in `tests/` was authored across nine passes; see `TEST_NOTES.md` for coverage
scope, ambiguities, and the LLM-nondeterminism boundary, and `CHANGELOG.md` for the
chronological log of fixes. Current status: **1566 tests collected** (`pytest --collect-only
-q`), up from 1309 before the 2026-08-03 pass (medicine/lab-test correction, frontend visual
refresh, and the 4-phase OPD/IPD/HeadNurse rebuild covered in that date's CHANGELOG.md entry —
new files include `tests/integration/test_ward_capacity_management.py`,
`test_nurse_shift_scheduling.py`, `test_ipd_reports_and_dashboard_summary.py`,
`test_ipd_alerts_endpoint.py`, `test_ipd_admit_validation.py`,
`test_consultations_search_pagination_and_analytics.py`, plus 25 new
`test_role_permission_matrix.py` cases), up from 144 before 2026-07-31's
two-part ward-workflow pass (~250 general ward-scenario cases, then ~214 more specifically
targeting the voice-based nurse features), up from 611 before 2026-08-01's dedicated HeadNurse
pass (~237 more), up from 848 before that same day's dedicated NursingStation pass (~219 more),
and up from 1067 before that same day's full-application pass (~240 more: clinical-specialty
diversity, hospital-scale scenarios, diagnostic workups, the critical user-management isolation
fix, and real concurrency testing — see CHANGELOG.md's 2026-08-01 part 3 entry) — plus a
separate, non-pytest-counted 30-case curated multi-lingual use-case library
(`tests/scenarios/`) whose output artifacts are saved to `final test output/`. Spanning:

- `tests/unit/` — pure-function logic (password complexity, JWT, scribe JSON parsing).
- `tests/from_data/` — the 12-case real-transcript OPD pipeline suite.
- `tests/concurrency/` — real simultaneous HTTP requests against a live server (not the
  in-process `TestClient`), the only way to exercise genuine request-interleaving races.
- `tests/scenarios/` — the curated multi-lingual use-case library; doubles as both a pytest
  suite and a content generator (writes to `final test output/`).
- `tests/integration/` — multi-tenant isolation, IPD edge cases, scribe input edge cases, auth
  edge cases, PHI-leakage checks, a full 5-role x 16-endpoint permission matrix, nurse-consult
  persistence, voice-extraction failure guards, task nurse_id validation, discharge workflow,
  assigned-nurse visibility, broader ward-day/vitals/admission/task-lifecycle scenarios, a
  dedicated HeadNurse-role suite (login/session, full-day workflows, permission boundaries,
  admit/assign/unassign/workload, task management, voice features, dashboard data), and a
  dedicated NursingStation-role suite (login/session, front-desk workflows, permission
  boundaries and denial-consistency, admission scenarios, patient/discharge management,
  ward-wide read-access parity, voice-feature denial) — see CHANGELOG.md's 2026-07-31 and
  2026-08-01 entries for the full list of new files. All of the above run against `backend/app`
  in-process via FastAPI's `TestClient` — no real network, no real browser.
- `tests/e2e/` (`pytest.mark.e2e`, needs `playwright install chromium` once after installing
  `requirements-dev.txt`) — drives the **real** `frontend/*.html` pages through a real headless
  browser against a real live local server, with `SpeechRecognition`/`getUserMedia` mocked to
  simulate voice input deterministically. This is the only layer that can catch pure-frontend
  JS bugs (a `const` reassignment throwing at runtime, a transcript-accumulation logic error,
  a request payload that discards user edits, a vitals-mapping function silently leaving DB
  columns null) — none of which a backend-only test can see, since the backend receives
  whatever the (possibly buggy) JS decided to send it.

Significant things found and fixed across these passes:

- **(2026-08-01 pass, most severe finding of the entire engagement) Cross-tenant account
  takeover via three user-management endpoints.** `GET /api/auth/users`,
  `PATCH /api/auth/users/{id}`, and `PATCH /api/auth/users/{id}/password` had no
  `organization_id` scoping at all — unlike every IPD endpoint below, which had this exact bug
  fixed years earlier in this engagement. Any Admin at any organization could enumerate every
  user across every organization in the system, change any other organization's user's role
  (privilege escalation/sabotage), or **reset any other organization's user's password**
  (complete account takeover). Found via an unrelated hospital-scale scenario test unexpectedly
  returning one extra, unrelated user. Fixed by adding the same org filter every other
  user-facing query already uses; see CHANGELOG.md's 2026-08-01 part 3 entry and
  `tests/integration/test_user_management_multi_tenant_isolation.py`.
- **The entire IPD module had no organization-scoping at all** on any
  Patient/Vital/Task/NursingNote query — any authenticated clinical user at one organization
  could view or modify another organization's patients (`get_ipd_patients`,
  `get_patient_details`, `update_patient`, `assign_patient`, `record_vital`, `get_vitals`,
  `create_task`, `update_task`, `get_tasks`, `nurse_consult`, `create_nursing_note`). Fixed in
  `backend/app/main.py` (pytest-covered) and, at the time, also mirrored into the
  since-removed `api/index.py` Vercel duplicate (see section 1 — that file no longer exists).
- **Three real-browser-only bugs broke the OPD/IPD voice consultation flow in production**: a
  guaranteed `TypeError` crash on access-token refresh in `opd.html` (silently killing the
  save whenever a consultation outlasted the 15-minute token lifetime), a transcript-doubling
  bug in the same file's speech accumulator, and an IPD "Save Nursing Notes" flow that
  silently discarded the nurse's reviewed edits and persisted a fresh, independently-generated
  LLM re-derivation instead. Found by driving the real frontend through Playwright with a
  mocked `SpeechRecognition`, reproduced live before fixing, and pinned down with permanent
  `tests/e2e/` regression tests (each verified to fail against the pre-fix code).
- **(2026-07-31 pass) `POST /api/ipd/nurse-consult` double-persisted every voice consult**: it
  wrote an unreviewed AI draft (Vital rows with every structured column null, plus a
  NursingNote) to the database immediately on "Process", before the nurse ever reviewed it —
  duplicated by the real Save if the nurse proceeded, or left as an orphaned ghost record if
  they didn't. No prior pass's test suite touched this endpoint at all. Fixed by making it a
  pure preview/extraction endpoint with zero DB writes.
- **(2026-07-31 pass) voice-recorded vitals never populated the columns the dashboard actually
  reads**: `ipd.html`'s Save handler hardcoded every structured `Vital` column (`bp_systolic`,
  `heart_rate`, etc.) to `null` and stuffed the real reading into a free-text `notes` string —
  so the abnormal-vitals alert (which only reads structured columns) never fired for anything
  captured via voice, and the chart literally rendered "BP null/null". Fixed with a frontend
  parameter-name mapper; caught and regression-tested via a real-browser Playwright test.
- **(2026-08-01 pass) Two UI-only permission mismatches specifically hurt HeadNurse**: the
  Admit Patient button was only ever shown for `NursingStation` and the Mark Complete task
  button only ever shown for the assigned `Nurse`, in both cases despite the backend always
  permitting HeadNurse too. Found by systematically cross-checking every role-gated element in
  `ipd.html` against `main.py`'s actual permission checks. Fixed by widening the visibility
  conditions to match backend reality.
- **(2026-08-01 pass) An HTML `id` collision silently broke the patient-detail modal's Tasks
  tab for every role**, not just HeadNurse — see section 2 above for the full mechanism. Found
  only because a HeadNurse-focused e2e test attempted a real `.click()` (which requires
  genuine visibility) rather than a DOM-presence `.count()` check; the bug had zero error
  signature and would not have surfaced from backend testing, manual code review, or a
  presence-only frontend check.
- **(2026-08-01 pass, NursingStation half) No new bugs found** — the same UI-audit method
  applied to NursingStation (also called the "ward login") turned up nothing new, because the
  two frontend fixes above already covered every UI surface this role shares with HeadNurse.
  Worth recording explicitly: a systematic audit finding zero issues on a second pass is a
  meaningful (negative) result confirming the first pass's fixes actually generalized, not an
  indication the audit wasn't thorough — `tests/e2e/test_nursingstation_e2e_workflow.py`
  exercises the same fixed surfaces (the Tasks tab, the Admit button) from this role's login.

See CHANGELOG.md for the full fix list and TEST_NOTES.md for what was deliberately left as a
documented gap rather than guessed at.

**Critical safety note for anyone extending these tests:** `backend/.env`'s `DATABASE_URL`
points at a real Postgres instance (confirmed to start with `postgresql`, value not printed
here or committed anywhere). `backend/app/main.py` calls `Base.metadata.create_all(bind=engine)`
at **import time**, not inside a function — importing the module is enough to open a connection
and issue DDL against whatever `DATABASE_URL` is active. `tests/conftest.py` therefore
overrides `DATABASE_URL` (and forces a throwaway SQLite file) via `os.environ` **before**
`backend.app.main` is ever imported. Never import `backend.app.main` in a test or script without
that guard in place first.
