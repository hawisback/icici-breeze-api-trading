from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()

run_migrations_online()

