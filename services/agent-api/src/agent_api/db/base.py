from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata root collected by Alembic for all ORM models."""
