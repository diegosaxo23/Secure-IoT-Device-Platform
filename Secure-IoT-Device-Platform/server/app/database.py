from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args: dict[str, object] = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _apply_lightweight_migrations() -> None:
    """Keep direct compatibility with SQLite databases created by v1.x.

    The project remains self-contained and does not require Alembic for the lab.
    New installations already use the v2.0 schema; a v1.1 database only needs the
    physical/simulated deployment classification column.
    """
    inspector = inspect(engine)
    if "devices" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("devices")}
    if "deployment_type" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE devices ADD COLUMN deployment_type "
                    "VARCHAR(16) NOT NULL DEFAULT 'physical'"
                )
            )
    # Create the index separately for installations migrated from v1.x.
    if settings.database_url.startswith("sqlite"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_devices_deployment_type "
                    "ON devices (deployment_type)"
                )
            )


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
