
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_database_url, settings


if not settings.DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL chưa được cấu hình trong file .env"
    )

engine = create_engine(get_database_url(), pool_pre_ping=True, pool_recycle=1800)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
