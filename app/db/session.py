from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.db.migrations import run_schema_migrations


connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def init_db() -> None:
    """Create database tables and apply lightweight migrations."""

    SQLModel.metadata.create_all(bind=engine)
    run_schema_migrations(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:  # pragma: no cover - cleanup
        session.rollback()
        raise
    finally:
        session.close()
