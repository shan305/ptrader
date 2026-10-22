from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Engine, MetaData, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DecimalString(TypeDecorator):
    """Stores Decimal values as text so prices and money stay exact on every backend.

    CHECK constraints compare these columns with CAST(... AS NUMERIC).
    """

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else format(Decimal(value), "f")

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes, also on backends (SQLite) that drop the offset."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def is_memory_sqlite(url: str) -> bool:
    return url in ("sqlite://", "sqlite:///:memory:")


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if is_memory_sqlite(url):
            # One shared connection, otherwise every session sees its own empty database.
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
