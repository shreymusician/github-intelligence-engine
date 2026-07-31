"""Alembic environment configuration.

This script is run by Alembic when generating migrations or running upgrades.
It establishes the database connection and sets up the migration context.

Created: Checkpoint 0.1
To be integrated with ORM models: Checkpoint 0.3+
"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
import os
from pathlib import Path

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv()

# Get the Alembic config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ============================================================================
# DATABASE URL CONFIGURATION
# ============================================================================
# Build the database URL from environment variables
# Format: postgresql+psycopg://user:password@host:port/database

POSTGRES_USER = os.getenv('POSTGRES_USER', 'repo_user')
POSTGRES_PASSWORD = os.getenv('POSTGRES_USER_PASSWORD', 'repo_password')
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
POSTGRES_DB = os.getenv('POSTGRES_DB', 'ai_analytics')

sqlalchemy_url = (
    f'postgresql+psycopg://{POSTGRES_USER}:{POSTGRES_PASSWORD}'
    f'@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}'
)

config.set_main_option('sqlalchemy.url', sqlalchemy_url)

# ============================================================================
# ORM METADATA (To be added in Checkpoint 0.3+)
# ============================================================================
# When ORM models are created, import and set them here:
#
# from app.database.models import Base
# target_metadata = Base.metadata
#
# For now, use None (raw SQL migrations)

target_metadata = None

# ============================================================================
# MIGRATION CONFIGURATION
# ============================================================================

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """

    # Create PostgreSQL connection with connection pooling
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling for migrations
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
