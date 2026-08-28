"""
Additive-only schema migration helper.

There is no Alembic in this project, and Base.metadata.create_all only ever
creates *missing* tables -- it never adds new columns to a table that already
exists. Whenever a new column is added to a model in models.py, register it
here so existing databases (including the production one) pick it up the
next time the app starts, the same way create_all already does.

Every migration here MUST be purely additive (ADD COLUMN only) and safe to
run repeatedly -- it always checks the live column list first.
"""

from sqlalchemy import inspect, text

# (table_name, column_name, DDL type clause) -- DDL type must be valid for
# both SQLite and Postgres, since DATABASE_URL selects between them at runtime.
ADDITIVE_COLUMNS = [
    ("consultations", "visit_type", "VARCHAR(20) DEFAULT 'OPD'"),
    ("consultations", "admission_day", "INTEGER"),
    ("consultations", "interaction_warnings", "JSON"),
    ("tasks", "task_type", "VARCHAR(20) DEFAULT 'General'"),
    ("tasks", "source", "VARCHAR(20) DEFAULT 'Manual'"),
    ("tasks", "consultation_id", "INTEGER"),
    ("consultations", "finalized_at", "TIMESTAMP"),
    ("patients", "admission_type", "VARCHAR(20) DEFAULT 'IPD'"),
    ("patients", "allergies", "JSON"),
    ("patients", "status", "VARCHAR(20) DEFAULT 'Active'"),
    ("consultations", "objective_findings", "TEXT"),
    ("consultations", "allergy_warnings", "JSON"),
    ("billing_claims", "pre_authorization_id", "INTEGER"),
    # Master Patient Index fields (routers/patients.py) -- added to the Patient model but never
    # registered here, so any pre-existing database never got them and every call to
    # POST /api/patients/register failed with "table patients has no column named mrn".
    ("patients", "mrn", "VARCHAR(30)"),
    ("patients", "phone", "VARCHAR(20)"),
    ("patients", "date_of_birth", "DATE"),
    ("patients", "address", "TEXT"),
    ("patients", "id_proof_type", "VARCHAR(50)"),
    ("patients", "id_proof_number", "VARCHAR(100)"),
    # Added to the Consultation model (POST /api/consultations' optional follow_up_of_id field)
    # but never registered here, so GET /api/patients/{id}/case-summary's consultations query
    # failed with "no such column: consultations.follow_up_of_id" on any pre-existing database.
    ("consultations", "follow_up_of_id", "INTEGER"),
    # Same gap on DrugBatch -- present on the model, never registered here.
    ("drug_batches", "location", "VARCHAR(100) DEFAULT 'Main Store'"),
    ("drug_batches", "purchase_order_line_id", "INTEGER"),
    # CCAPatient identity-verification fields (models_cca.py) -- Front Desk's registration wizard
    # captures these but they were never persisted anywhere.
    ("cca_patients", "id_proof_type", "VARCHAR(50)"),
    ("cca_patients", "id_proof_number", "VARCHAR(100)"),
    ("cca_patients", "id_proof_name", "VARCHAR(200)"),
    ("cca_patients", "id_proof_dob", "VARCHAR(20)"),
    ("cca_patients", "id_proof_verification_status", "VARCHAR(30)"),
    # CCAPatient.hms_patient_id (models_cca.py:12) -- the FK linking a CCA patient identity to
    # its general-HMS Patient row -- was present on the model but never registered here, so any
    # pre-existing database (including a local dev aivana.db predating this field) was missing
    # the column entirely: every query touching CCAPatient (patient list, registration, and
    # effectively every CCA endpoint) failed with "no such column: cca_patients.hms_patient_id".
    ("cca_patients", "hms_patient_id", "INTEGER"),
    # ClinicalFact addendum/versioning fields (models_cca.py) -- same gap, different table:
    # present on the model, never registered here, so any pre-existing database was missing
    # both, breaking every fact-listing/verification query with "no such column:
    # cca_clinical_facts.parent_fact_id".
    ("cca_clinical_facts", "parent_fact_id", "INTEGER"),
    ("cca_clinical_facts", "version_no", "INTEGER DEFAULT 1"),
    # Care Plan / Treatment Plan separation (see the Care Plan & Treatment Plan architecture
    # doc): CarePlan now references its authorizing TreatmentPlan(s) explicitly instead of
    # embedding modality content as its own source of truth, and TreatmentPlan gains a real
    # versioning + signature lifecycle instead of being born directly into "ACTIVE".
    ("cca_care_plans", "source_treatment_plan_ids", "JSON"),
    ("cca_treatment_plans", "mdt_decision_id", "INTEGER"),
    ("cca_treatment_plans", "intent", "VARCHAR(100) DEFAULT 'Curative'"),
    ("cca_treatment_plans", "version_no", "INTEGER DEFAULT 1"),
    ("cca_treatment_plans", "supersedes_id", "INTEGER"),
    ("cca_treatment_plans", "signer_email", "VARCHAR(200)"),
    ("cca_treatment_plans", "signer_role", "VARCHAR(50)"),
    ("cca_treatment_plans", "signed_at", "TIMESTAMP"),
    # Treatment Order / Treatment Event separation: Day-Care must act on a signed Order, not
    # infer administration directly from a Plan or Care Plan -- see the Care Plan & Treatment
    # Plan architecture doc.
    ("cca_treatment_clearances", "order_id", "INTEGER"),
    # Guideline-update review flagging (architecture doc non-negotiable: a new guideline
    # version must never silently rewrite a signed plan, only flag it for clinician review).
    ("cca_treatment_plans", "guideline_review_required", "BOOLEAN DEFAULT FALSE"),
    ("cca_treatment_plans", "guideline_review_reason", "TEXT"),
    # AI Search's explicit-confirmation task origin tag (architecture doc non-negotiable:
    # AI proposes, clinician decides).
    ("cca_care_plan_tasks", "source", "VARCHAR(30) DEFAULT 'SYSTEM'"),
    # Patient-facing view, gated behind explicit clinical/consent review (architecture doc
    # non-negotiable: never expose internal reasoning by default).
    ("cca_care_plans", "patient_facing_approved", "BOOLEAN DEFAULT FALSE"),
    ("cca_care_plans", "patient_facing_approved_by", "VARCHAR(200)"),
    ("cca_care_plans", "patient_facing_approved_at", "TIMESTAMP"),
    ("cca_care_plan_tasks", "patient_visible_note", "TEXT"),
    # Found via a direct audit of the live production Postgres schema (information_schema.columns)
    # against every SQLAlchemy model column, after the same "column on model, never registered
    # here" bug (already fixed twice above for cca_patients/cca_clinical_facts) broke
    # GET /patients/{id}/treatment-plans in production with
    # "column cca_treatment_plans.created_by does not exist". This audit method is authoritative
    # -- a local SQLite recreated via create_all always has every current column already, which
    # is why earlier local-only comparisons missed these.
    ("cca_care_plan_versions", "before_after_diff", "JSON"),
    ("cca_care_plan_tasks", "owner_role", "VARCHAR(30)"),
    ("cca_care_plan_tasks", "category", "VARCHAR(30)"),
    ("cca_care_plan_tasks", "dependency_ids", "JSON"),
    ("cca_care_plan_tasks", "linked_order_id", "INTEGER"),
    ("cca_care_plan_tasks", "linked_result_id", "INTEGER"),
    ("cca_care_plan_tasks", "blocker_reason", "TEXT"),
    ("cca_treatment_plans", "created_by", "VARCHAR(200)"),
    # MDT recommendation disposition: treating-clinician accept/partially-accept/reject with
    # reason (architecture doc Sec 18), not a bare binary approve.
    ("cca_mdt_decisions", "disposition_reason", "TEXT"),
]


def run_additive_migrations(engine):
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, column, ddl_type in ADDITIVE_COLUMNS:
            if table not in existing_tables:
                # table itself doesn't exist yet -- create_all will make it
                # with the column already present, nothing to do here.
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if column in existing_columns:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


# (table_name, column_name) pairs whose NOT NULL constraint needs relaxing on an existing
# database -- distinct from ADD COLUMN above because DROP NOT NULL isn't expressible as one
# portable statement across SQLite and Postgres.
NULLABLE_RELAXATIONS = [
    # CarePlanTask.care_plan_id: a task is patient-scoped first: real triggers (e.g. an MDT
    # recommendation finalizing) routinely happen before any Care Plan exists yet. See
    # models_cca.py's CarePlanTask docstring and the Care Plan & Treatment Plan architecture
    # doc's flagged gap on task assignment without a Care Plan.
    ("cca_care_plan_tasks", "care_plan_id"),
]


def run_constraint_migrations(engine):
    """Postgres (the production backend, per config.py's DATABASE_URL) supports `ALTER
    COLUMN ... DROP NOT NULL` directly as a fast, safe, metadata-only change -- no table
    rewrite, no lock beyond a brief schema-change lock. SQLite has no equivalent ALTER
    COLUMN support at all (it would require rebuilding the whole table), and since SQLite is
    this project's disposable local/test fallback only (tests/conftest.py always points it
    at a throwaway file; see also ARCHITECTURE_NOTES.md), a local aivana.db predating this
    change should simply be deleted and let create_all rebuild it with the relaxed
    constraint already in place, rather than this helper building SQLite table-rebuild
    machinery for dev convenience alone."""
    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column in NULLABLE_RELAXATIONS:
            if table not in existing_tables:
                continue
            columns = {c["name"]: c for c in inspector.get_columns(table)}
            col = columns.get(column)
            if col is None or col.get("nullable"):
                continue
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"))
