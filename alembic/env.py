"""Alembic environment configuration for Elder."""

import importlib
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add parent directory to path for imports
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

# Import Base and all SQLAlchemy models registry-driven.
# Imports intentionally follow sys.path.insert above, so E402 is expected here.
from apps.api.models.base import Base  # noqa: E402
from apps.api.modules import CORE_MODELS, MODULES  # noqa: E402

# Import all model modules to register them with Base.metadata
# This ensures that alembic autogenerate picks up all tables
model_modules_to_import = set(CORE_MODELS)

for module_manifest in MODULES:
    for model_module in module_manifest.models_import:
        model_modules_to_import.add(model_module)

for model_module_path in sorted(model_modules_to_import):
    try:
        importlib.import_module(model_module_path)
    except ImportError as e:
        import warnings

        warnings.warn(f"Failed to import model module {model_module_path}: {e}")

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for autogenerate
target_metadata = Base.metadata

# Get database URL from environment variable if set
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
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
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
