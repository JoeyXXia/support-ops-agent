from alembic import context

from packages.storage.database import engine_from_environment
from packages.storage.schema import metadata

config = context.config


def run(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Use an explicit PostgreSQL connection for Day 8 migrations")
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = engine_from_environment()
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
