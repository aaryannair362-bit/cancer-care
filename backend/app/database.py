"""
Database engine/session plumbing, extracted out of main.py so new APIRouter modules
(backend/app/routers/*) can import `get_db` without a circular import back into main.py.
Behavior is unchanged from what main.py did inline before this split -- same engine
construction, same SessionLocal, same get_db generator.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from .config import settings

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    db_url,
    connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
