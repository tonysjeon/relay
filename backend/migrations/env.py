from alembic import context
from app import models  # noqa: F401 -- register all tables with Base.metadata
from app.core.config import Settings
from app.db.base import Base
from app.db.connections import create_database_engine

target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    run_migrations(context.config.attributes["connection"])
else:
    engine = create_database_engine(Settings())
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
