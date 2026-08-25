"""
Database engine/session plumbing, extracted out of main.py so new APIRouter modules
(backend/app/routers/*) can import `get_db` without a circular import back into main.py.
Behavior is unchanged from what main.py did inline before this split -- same engine
construction, same SessionLocal, same get_db generator.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from .config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
