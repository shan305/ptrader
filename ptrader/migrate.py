"""Schema migrations, applied on startup and available through the alembic CLI."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Databases created by PTrader 0.1 (Base.metadata.create_all) match this revision.
LEGACY_REVISION = "0001"


class MigrationError(RuntimeError):
    pass


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@contextmanager
def migration_connection(engine: Engine) -> Iterator[Connection]:
    """A connection on which a whole migration run commits or rolls back as one unit.

    On SQLite, foreign keys are switched off while batch operations rebuild tables
    and checked before commit. pysqlite does not open transactions before DDL, so
    BEGIN/COMMIT are issued explicitly to make the schema change atomic.
    """
    with engine.connect() as connection:
        if engine.dialect.name != "sqlite":
            with connection.begin():
                yield connection
            return

        dbapi_connection = connection.connection.dbapi_connection
        previous_isolation = dbapi_connection.isolation_level
        dbapi_connection.isolation_level = None
        try:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("BEGIN")
            try:
                yield connection
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise MigrationError(f"Foreign key violations after migration: {violations[:5]}")
                connection.exec_driver_sql("COMMIT")
            except BaseException:
                if dbapi_connection.in_transaction:
                    connection.exec_driver_sql("ROLLBACK")
                raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            dbapi_connection.isolation_level = previous_isolation
            connection.rollback()


def upgrade_database(engine: Engine, revision: str = "head") -> None:
    """Bring the schema to `revision`, adopting a pre-migration (0.1) database first."""
    config = alembic_config()
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if "accounts" in tables and "alembic_version" not in tables:
            logger.info("Adopting unversioned database at revision %s", LEGACY_REVISION)
            command.stamp(config, LEGACY_REVISION)
        command.upgrade(config, revision)


def downgrade_database(engine: Engine, revision: str) -> None:
    config = alembic_config()
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)
