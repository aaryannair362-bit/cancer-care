# Quick Start

Get AIvana OS running locally in a few minutes.

## Prerequisites

- Python 3.13
- A Groq API key if you want OPD voice-drafted consultations to work
  (`https://console.groq.com`). Everything else works without it.

Document OCR (`easyocr`) needs no separate install — it's a normal pip dependency in
`requirements.txt`. Its first real OCR call downloads model weights (~100MB, one-time, cached
to disk afterward), so expect a pause the first time you upload a scanned document locally.

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS/Linux

pip install -r requirements.txt
```

Copy `.env.example` to `backend/.env` and fill in at least `SECRET_KEY` (the app refuses to
start with the placeholder default — generate one with
`python -c "import secrets; print(secrets.token_hex(32))"`). `DATABASE_URL` defaults to a local
SQLite file if left unset, so no database setup is required to get started.

## Run

```bash
uvicorn backend.app.main:app --reload
```

Visit **http://localhost:8000** — that's the login page. On first run, the app auto-bootstraps
a default Admin account (see `backend/app/main.py`'s startup logic; check the server log for
the generated credentials).

## What to look at first

- **`opd.html`** (Doctor login) — outpatient consultations, structured-form or voice-drafted.
- **`ipd.html`** (Nurse/NursingStation login) — ward admission, vitals, tasks, nursing notes.
- **`headnurse.html`** — nurse assignment, shift scheduling, ward dashboard.
- **`pharmacy.html`**, **`billing.html`**, **`inventory.html`**, **`frontdesk.html`**,
  **`tpa.html`** — the newer HMS modules, each gated to its own role.
- **`cca_os.html`** (any logged-in role) — the Cancer Care OS: document ingestion, staging,
  MDT, care plans, treatment tracking.
- **`admin.html`** (Admin login) — org/user management, medicine/lab-test reference data.

## Tests

```bash
pip install -r requirements-dev.txt
playwright install chromium       # once, only needed for the e2e suite
pytest
```

See `TEST_NOTES.md` for what's covered and what's a documented, deliberate gap.

## Where to go next

- **`ARCHITECTURE_NOTES.md`** — the accurate, living map of this codebase. Read this before
  making non-trivial changes.
- **`SCHEMA.md`** — database schema overview, grouped by module.
- **`CHANGELOG.md`** — chronological log of fixes and features.
- **`AIVANA-UI-STYLE-RULES.md`** — the Cancer Care OS visual design system.
