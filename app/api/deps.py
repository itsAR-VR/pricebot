from collections.abc import Generator

from sqlmodel import Session

from app.db.session import get_session


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""

    with get_session() as session:
        yield session
