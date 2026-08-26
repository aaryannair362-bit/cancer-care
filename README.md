# AIvana OS — Cancer Care

A hospital management system with a dedicated oncology (Cancer Care / "CCA") module,
deployed as a single FastAPI service that also serves its own static frontend — no separate
frontend host, no build step.

**Start here if you're onboarding:** `ARCHITECTURE_NOTES.md` is the living, accurate map of
this codebase — read it before making changes. This file is a short orientation only.

## What's actually in this repo

- **`backend/app/`** — the FastAPI application (`uvicorn app.main:app`), backed by Postgres in
  production (SQLite for local dev/tests). `main.py` is the entry point: auth, org/user
  management, OPD consultations, IPD wards. It mounts 11 feature routers from
  `backend/app/routers/`: pharmacy, inventory, billing, patients, appointments,
  nursing_charting, procedures, nursing_assessments, mar (medication administration),
  patient_documents, and cca (the Cancer Care OS).
- **`frontend/`** — static HTML pages with vanilla JS, no framework/build step, served directly
  by FastAPI. One page per role/module (`opd.html`, `ipd.html`, `headnurse.html`, `pharmacy.html`,
  `billing.html`, `inventory.html`, `frontdesk.html`, `tpa.html`, `admin.html`, `cca_os.html`, ...).
- **`tests/`** — pytest suite (unit/integration/concurrency/e2e/from_data/scenarios). See
  `TEST_NOTES.md` for coverage scope and documented ambiguities.
- **`data/specs/`, `data/reports/`** — the requirements source material the HMS/CCA build was
  developed against (a master spec PDF and a CCA demo spec).

### Not part of this product (legacy, kept for reference only)

`dashboard/`, `voice-agent/`, `types/`, `dashboard-ui.html` are an earlier, unrelated
Next.js/Supabase voice-agent scaffold that predates the current hospital system. They are not
wired into `backend/app` or `frontend/` in any way and are not part of what gets deployed.
`QUICKSTART.md`'s older revisions described that scaffold — it's been rewritten below to match
the actual system.

## Core modules

- **OPD** (outpatient) — doctor consultations. Supports both a plain structured-entry form and
  voice-drafted consultations (microphone → server-side transcription → an LLM-drafted
  prescription draft the doctor reviews and finalizes). Medication/lab names are corrected
  against bundled reference datasets (`drug_matcher.py`, `lab_test_matcher.py`) regardless of
  how they were entered.
- **IPD** (inpatient) — ward admission, vitals, tasks, nursing notes, discharge. Plain
  structured-form entry only — voice input is intentionally OPD-only, see
  `ARCHITECTURE_NOTES.md`.
- **HeadNurse / NursingStation** — nurse assignment, shift scheduling, ward dashboards/reports.
- **Pharmacy** — formulary, batch receiving, FEFO dispensing, controlled-drug register.
- **Inventory** — vendors, purchase requests → orders → goods receipt, stock transfers.
- **Billing** — tariffs, packages, invoices, payments, refunds, insurance/corporate claims.
- **TPA** — insurance pre-authorization workflow.
- **Front Desk / Patients** — registration, MRN assignment, patient search.
- **Appointments** — scheduling and a walk-in queue/token system.
- **Nursing Charting/Assessments** — IV fluid orders, intake/output, admission/pain/fall-risk
  (Morse)/pressure-ulcer (Braden) assessments.
- **MAR** — medication administration records.
- **Patient Documents** — upload with OCR extraction (PDF/image → text via `ocr_service.py`).
- **CCA Oncology OS** (`/api/cca`, `frontend/cca_os.html`) — document ingestion → AI-extracted
  clinical facts with provenance → clinician verify/correct/reject → contradiction detection →
  AJCC staging → NCCN guideline readiness → AI clinical brief → MDT tumor-board tracking → care
  plans → treatment-day/toxicity/clearance → RECIST response assessment.
- **Admin** — org/user management, medicine/lab-test reference-data administration.

## Roles

Admin, Doctor, HeadNurse, NursingStation, Nurse, Pharmacist, Billing, TPA, InventoryManager —
every data-access endpoint is scoped to the caller's organization (multi-tenant).

## Running locally

```bash
python -m venv venv
venv\Scripts\activate        # Windows; use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Visit `http://localhost:8000` (the login page is served at `/`). See `.env.example` (or
`backend/.env`, gitignored) for required environment variables — at minimum `DATABASE_URL` and
`SECRET_KEY`; `GROQ_API_KEY` is required for OPD voice drafting. Document OCR (`python-doctr`)
needs no extra setup — it's a pure Python dependency already in `requirements.txt`.

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/conftest.py` forces an isolated throwaway SQLite database — see that file's docstring
before changing anything about test setup, since `main.py` opens a real database connection at
import time.

## Deployment

Deployed as a single service on Render, native Python runtime (no Docker/system packages
needed — every dependency, including document OCR, installs via `pip`). See `render.yaml` in
the repo root for the versioned build/deploy configuration.

## Further reading

| Topic | File |
|---|---|
| Codebase architecture, conventions, known gaps | `ARCHITECTURE_NOTES.md` |
| Chronological log of fixes and features | `CHANGELOG.md` |
| Test coverage scope and documented ambiguities | `TEST_NOTES.md` |
| Cancer Care module UI style guide | `AIVANA-UI-STYLE-RULES.md` |
| OCR engine benchmark notes | `OCR_BENCHMARK.md` |
