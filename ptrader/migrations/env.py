from alembic import context
from sqlalchemy import Connection

from ptrader import models  # noqa: F401  (registers the tables on Base.metadata)
from ptrader.config import Settings
from ptrader.db import Base, make_engine
from ptrader.migrate import migration_connection

config = context.config


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    # Joins the transaction opened by migration_connection, which commits it.
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported")

connection = config.attributes.get("connection")
if connection is not None:
    run_migrations(connection)
else:
    engine = make_engine(config.get_main_option("sqlalchemy.url") or Settings().database_url)
    try:
        with migration_connection(engine) as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
