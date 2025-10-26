from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os, sys

sys.path.append(os.path.abspath("."))
from app.db import Base
from app import models  # noqa: F401 to load metadata

config = context.config
# Optional logging configuration: skip if logging sections are absent
if config.config_file_name and os.path.exists(config.config_file_name):
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

# Build DB URL from environment variables to avoid ${...} parsing issues in ini
db_user = os.getenv("DB_USER", "neuralbot")
db_pass = os.getenv("DB_PASS", "neuralbot")
db_host = os.getenv("DB_HOST", "db")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("DB_NAME", "neuralbot")
db_url = f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()



