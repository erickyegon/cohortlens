import os

from sqlalchemy import create_engine, pool

from alembic import context

config = context.config
url = os.environ["DATABASE_URL_APP"].replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, version_table_schema="app", include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
