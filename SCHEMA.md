# Database Schema Overview

This is a map of the schema, grouped by module, not a column-by-column reference — the models
themselves (`backend/app/models.py` for the hospital/HMS tables, `backend/app/models_cca.py`
for the Cancer Care OS tables) are the source of truth; this file will drift out of sync with
them exactly the way the previous version of this document (describing an unrelated
Supabase/voice-agent schema) did, so treat it as a starting map for exploration, not a
substitute for reading the model file when something load-bearing depends on an exact column.

Both files are plain SQLAlchemy declarative models against a single database (Postgres in
production, SQLite for local dev/tests — see `DATABASE_URL`). `Base.metadata.create_all()` runs
at app startup; additive schema changes go through `backend/app/migrations.py`.

## Multi-tenancy

Every hospital/HMS table that holds patient-identifiable or organization-specific data carries
an `organization_id` foreign key to `Organization`, and every query in `backend/app/main.py`
and `backend/app/routers/*.py` filters on it — this is the single most important invariant in
the codebase (see `ARCHITECTURE_NOTES.md` for the history of cross-tenant leak bugs that were
found and fixed by *not* following this rule). The Cancer Care OS tables (`models_cca.py`) now
carry the same `organization_id` column on `CCAPatient`, enforced at the router layer
(`backend/app/routers/cca.py`) the same way.

## Core / auth

`Organization`, `User`, `AuditLog`, `PasswordHistory` — org-scoped accounts, JWT auth, and an
append-only audit trail written on every state-changing action.

## OPD / IPD clinical core

- `Consultation` — OPD visit record (SOAP-structured: chief complaint/HPI as Subjective,
  objective findings, primary/differential diagnosis as Assessment, medications/labs/advice as
  Plan). Supports both plain structured entry and voice-drafted entry (`raw_transcript`,
  `gemini_latency`, token-count columns populated only on the voice path). Self-referential
  `follow_up_of_id` links a visit to the consultation it's following up on.
- `Patient` — the IPD/ward patient record (MRN, demographics, admission/ward/bed, allergies).
- `NurseAssignment`, `Vital`, `Task`, `NursingNote`, `DischargeSummary` — ward workflow: who's
  assigned to whom, recorded vitals, tasks, SOAP nursing notes, and the (deterministic,
  template-based, non-AI — see `discharge_summary.py`) discharge summary.
- `Ward`, `NurseShift` — ward bed-capacity tracking and the nurse shift-scheduling grid.

## Pharmacy / inventory

`Drug`, `DrugBatch`, `DispensingRecord`, `ControlledDrugRegisterEntry` — formulary, batch/expiry
tracking (FEFO dispensing), and the narcotics register. `Vendor`, `PurchaseRequest`,
`PurchaseOrder`, `PurchaseOrderLine`, `StockTransfer` — the procure-to-stock pipeline shared by
pharmacy and general inventory. Concurrent stock claims go through `stock_utils.claim_batch_stock`'s
atomic conditional update, not application-level locking — see that module's docstring for why.

## Billing

`Tariff`, `BillingPackage`, `Invoice`, `InvoiceLine`, `Payment`, `Refund`, `BillingClaim`,
`PreAuthorizationRequest` — tariff/package pricing, invoicing (manual, tariff-based,
package-based, or pulled from pharmacy dispensing/bed-days), payment collection, refunds, and
the insurance/corporate claims + TPA pre-authorization flow. Money columns are `Numeric`, not
`Float` — see `ARCHITECTURE_NOTES.md` for the reasoning.

## Appointments / front desk

`Appointment`, `QueueToken` — scheduling and the walk-in queue/token system.

## Nursing workflows

`IVFluidOrder`, `IntakeOutputRecord` — fluid orders and running intake/output balance.
`AdmissionAssessment`, `PainAssessment`, `FallRiskAssessment` (Morse scale),
`PressureUlcerAssessment` (Braden scale) — structured nursing assessments, scored at creation
time. `MedicationAdministration` — the MAR, distinct from the general `Task` table, with an
optional link back to a `Task` for PRN doses.

## Clinical documentation

`ProcedureRecord` — doctor-documented procedures. `PatientDocument` — uploaded external
records (referral letters, prior reports) with OCR-extracted text and a SHA-256 dedup hash.

## Cancer Care OS (`models_cca.py`)

A separate table set (prefixed `cca_*`) implementing the oncology-specific workflow:
`CCAPatient` (with `organization_id`), `CCAConsent`, `CCAEncounter`, `CCAIntakeAssessment`,
`CCADocument`, `ClinicalFact` (AI-extracted, clinician-verifiable facts with verbatim-text and
bounding-box provenance), `CCAContradiction`, `CCACancerDiagnosis`, `CCABiomarkerResult`,
`CCAOrder`/`CCAResult`, `StagingRecord`/`StagingEvidence` (AJCC staging, evidence-gated —
never auto-computed, see `cca_engine.py`), `GuidelineContext` (NCCN readiness), `ClinicalBrief`,
`MDTCase`/`MDTDecision` (tumor board), `CarePlan`/`CarePlanVersion`/`CarePlanTask`,
`TreatmentPlan`/`TreatmentSession`, `ToxicityEvent` (CTCAE v5.0), `TreatmentClearance`,
`ResponseAssessment` (RECIST 1.1), and `CCAJourneyEvent` (the patient-timeline audit trail).

## What's deliberately not modeled

No `ON DELETE`/cascade rules are defined anywhere — a pre-existing, intentional convention
(deletes are handled at the application layer via status changes, e.g. `Patient.status =
"Discharged"`, not row removal). No vital-sign unit column exists (Celsius/mmHg assumed,
undocumented — see `ARCHITECTURE_NOTES.md`'s open questions).
