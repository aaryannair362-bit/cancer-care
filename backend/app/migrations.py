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
