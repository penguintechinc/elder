"""Database connection and session management using PyDAL."""

# flake8: noqa: E501


from contextlib import contextmanager
from typing import Generator

from flask import Flask
from pydal import DAL

# Global PyDAL instance
db = None


def init_db(app: Flask) -> None:
    """
    Initialize database with Flask application.

    Args:
        app: Flask application instance

    Environment Variables:
        DATABASE_URL: Full database URI (takes precedence if set)
        DB_TYPE: Database type (postgresql, mysql, mariadb, mariadb-galera, sqlite, etc.) - default: postgresql
        DB_HOST: Database host - default: localhost
        DB_PORT: Database port - default: 5432 (PostgreSQL) or 3306 (MySQL/MariaDB)
        DB_NAME: Database name - default: elder
        DB_USER: Database username - default: elder
        DB_PASSWORD: Database password - default: elder
        DB_POOL_SIZE: Connection pool size - default: 10

    Primary Supported Databases (Fully Tested):
        - PostgreSQL: postgres://user:pass@host:5432/db
        - MariaDB: mysql://user:pass@host:3306/db?set_encoding=utf8mb4
        - MariaDB Galera Cluster: mysql://user:pass@host:3306/db?set_encoding=utf8mb4
        - SQLite: sqlite://storage.sqlite (development only)

    Additional PyDAL-Supported Databases (Use with caution):
        - MySQL: mysql://user:pass@host:3306/db?set_encoding=utf8mb4
        - MSSQL: mssql3://user:pass@host/db (2005+), mssql4://user:pass@host/db (2012+)
        - Oracle: oracle://user/pass@db
        - MongoDB: mongodb://user:pass@host/db
        - Google Cloud SQL: google:sql://project:instance/database
        - And more: FireBird, DB2, Ingres, Sybase, Informix, Teradata, Cubrid, SAPDB

    Note:
        - All database access uses PyDAL for portability
        - SQLAlchemy models are only used for table initialization
        - Code is designed to work identically on PostgreSQL and MariaDB Galera

        Full PyDAL docs: https://py4web.com/_documentation/static/en/chapter-07.html
    """
    global db
    import logging
    import os
    import time

    logger = logging.getLogger(__name__)

    # Build database URL from environment variables or use full DATABASE_URL
    database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")

    # Fix PostgreSQL URL format for PyDAL (must be postgres:// not postgresql://)
    if database_url and database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgres://", 1)

    if not database_url:
        # Build from individual components
        db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE", "postgres")
        db_host = app.config.get("DB_HOST") or os.getenv("DB_HOST", "localhost")
        db_port = app.config.get("DB_PORT") or os.getenv("DB_PORT", "5432")
        db_name = app.config.get("DB_NAME") or os.getenv("DB_NAME", "elder")
        db_user = app.config.get("DB_USER") or os.getenv("DB_USER", "elder")
        db_password = app.config.get("DB_PASSWORD") or os.getenv("DB_PASSWORD", "elder")

        # Normalize DB_TYPE to PyDAL format
        db_type = db_type.lower()

        # Handle all PyDAL-supported database types
        # Reference: https://py4web.com/_documentation/static/en/chapter-07.html
        if db_type == "sqlite":
            # SQLite - file-based database
            database_url = f"sqlite://{db_name}.sqlite"

        elif db_type in ["mysql", "mariadb", "mariadb-galera"]:
            # MySQL/MariaDB/Galera - with UTF8MB4 encoding
            # Default port for MySQL/MariaDB
            if not db_port or db_port == "5432":  # Reset if PostgreSQL default was used
                db_port = "3306"

            database_url = f"mysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?set_encoding=utf8mb4"

        elif db_type in ["postgresql", "postgres"]:
            # PostgreSQL - PyDAL uses 'postgres://' not 'postgresql://'
            database_url = (
                f"postgres://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            )

        elif db_type in ["mssql", "mssql3", "mssql4"]:
            # Microsoft SQL Server - mssql (legacy), mssql3 (2005+), mssql4 (2012+)
            # Default to mssql4 for modern SQL Server
            adapter = "mssql4" if db_type == "mssql" else db_type
            database_url = f"{adapter}://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "oracle":
            # Oracle - special format with user/password
            database_url = f"oracle://{db_user}/{db_password}@{db_name}"

        elif db_type == "mongodb":
            # MongoDB - NoSQL database
            database_url = f"mongodb://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "firebird":
            # FireBird database
            database_url = f"firebird://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "db2":
            # IBM DB2
            database_url = f"db2://{db_user}:{db_password}@{db_name}"

        elif db_type == "ingres":
            # Ingres database
            database_url = f"ingres://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "sybase":
            # Sybase database
            database_url = f"sybase://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "informix":
            # Informix database
            database_url = f"informix://{db_user}:{db_password}@{db_name}"

        elif db_type == "teradata":
            # Teradata - uses DSN format
            # Format: teradata://DSN=dsn;UID=user;PWD=pass;DATABASE=test
            database_url = f"teradata://DSN={db_host};UID={db_user};PWD={db_password};DATABASE={db_name}"

        elif db_type == "cubrid":
            # CUBRID database
            database_url = f"cubrid://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "sapdb":
            # SAP DB (MaxDB)
            database_url = f"sapdb://{db_user}:{db_password}@{db_host}/{db_name}"

        elif db_type == "imap":
            # IMAP (email storage) - special use case
            database_url = f"imap://{db_user}:{db_password}@{db_host}:{db_port}"

        elif db_type in ["google:sql", "googlesql"]:
            # Google Cloud SQL
            # Format: google:sql://project:instance/database
            # Use DB_HOST as project:instance format
            database_url = f"google:sql://{db_host}/{db_name}"

        elif db_type in ["google:datastore", "googledatastore"]:
            # Google Cloud Datastore (NoSQL)
            database_url = "google:datastore"

        elif db_type in ["google:datastore+ndb", "googledatastore+ndb"]:
            # Google Cloud Datastore with NDB
            database_url = "google:datastore+ndb"

        else:
            # Generic format for any other PyDAL-supported database
            # This allows for future database support without code changes
            database_url = f"{db_type}://{db_user}:{db_password}@{db_host}/{db_name}"

    # Get connection pool size
    pool_size = app.config.get("DB_POOL_SIZE") or int(os.getenv("DB_POOL_SIZE", "10"))

    # Wait for database to be ready (retry logic for startup)
    max_retries = 30  # 30 retries = 30 seconds max wait
    retry_delay = 1  # 1 second between retries

    for attempt in range(max_retries):
        try:
            # Initialize PyDAL with connection pooling
            # Use pool_size=1 instead of 0 to enable migration properly
            # pool_size=0 can cause migration issues as tables aren't created
            # With pool_size=1, we get proper migration while maintaining thread safety
            temp_db = DAL(
                database_url,
                folder=(
                    app.instance_path if hasattr(app, "instance_path") else "databases"
                ),
                migrate=True,
                fake_migrate_all=False,  # Allow table creation on first run
                lazy_tables=False,
                pool_size=1,  # Minimum pool size to enable migration
                adapter_args={"attempts": 1},  # Don't retry failed connections
            )
            # Test the connection
            temp_db.executesql("SELECT 1")
            logger.info("Database connection established successfully")
            db = temp_db
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Failed to connect to database after {max_retries} attempts"
                )
                raise

    # Store db instance in app context
    app.db = db

    # Define all tables
    _define_tables(db)

    # Commit table definitions
    db.commit()

    # WORKAROUND: PyDAL migration not working reliably with pool_size=1 in async context
    # Manually create tables using raw SQL to ensure they exist before data initialization
    try:
        db.executesql("SELECT 1 FROM roles LIMIT 0")
        logger.info("Tables already exist, skipping creation")
    except Exception:
        # Table doesn't exist, rollback failed query and create tables
        db.rollback()
        logger.info("Tables don't exist, creating them manually...")
        # Create tables using PyDAL's SQL generation
        for table_name in db.tables:
            table = db[table_name]
            # Get create table SQL from PyDAL
            create_sql = db._adapter.create_table(table, migrate=False)
            if create_sql:
                try:
                    logger.info(f"Creating table: {table_name}")
                    db.executesql(create_sql)
                except Exception as e:
                    logger.error(f"Failed to create table {table_name}: {e}")
                    db.rollback()  # Rollback failed create and continue
        db.commit()

    # Initialize default data
    _init_default_data(db)


def _define_tables(db: DAL) -> None:
    """Define all database tables using PyDAL."""

    # Import table definitions
    from apps.api.models.pydal_models import define_all_tables

    define_all_tables(db)


def _init_default_data(db: DAL) -> None:
    """Initialize default data (roles, permissions, admin user)."""
    import os

    from werkzeug.security import generate_password_hash

    # Check if roles already exist
    # Note: On first run, tables might not exist yet
    roles_exist = False
    try:
        if db(db.roles).count() > 0:
            roles_exist = True
    except Exception:
        # Tables don't exist yet (first run), rollback failed transaction and continue
        db.rollback()

    # Only create roles/permissions/org if they don't exist
    roles = {}
    root_org_id = None
    system_tenant_id = None

    # Create system tenant if it doesn't exist (needed for portal_users and organizations)
    try:
        existing_tenant = db(db.tenants.id == 1).select().first()
        if not existing_tenant:
            system_tenant_id = db.tenants.insert(
                name="System",
                slug="system",
                subscription_tier="enterprise",
                is_active=True,
                data_retention_days=365,
                storage_quota_gb=1000,
            )
        else:
            system_tenant_id = existing_tenant.id
    except Exception:
        # Tables don't exist yet (first run), rollback and continue
        db.rollback()
        # Will create tenant after tables are created
        system_tenant_id = 1

    if not roles_exist:
        # Create default roles
        roles_data = [
            {"name": "super_admin", "description": "Full system access"},
            {"name": "org_admin", "description": "Full access within organization"},
            {"name": "editor", "description": "Can create and edit entities"},
            {"name": "viewer", "description": "Read-only access"},
        ]

        for role_data in roles_data:
            role_id = db.roles.insert(**role_data)
            roles[role_data["name"]] = role_id

        # Create default permissions
        permissions_data = [
            # Entity permissions
            {
                "name": "create_entity",
                "resource_type": "entity",
                "action_name": "create",
            },
            {"name": "edit_entity", "resource_type": "entity", "action_name": "edit"},
            {
                "name": "delete_entity",
                "resource_type": "entity",
                "action_name": "delete",
            },
            {"name": "view_entity", "resource_type": "entity", "action_name": "view"},
            # Organization permissions
            {
                "name": "create_organization",
                "resource_type": "organization",
                "action_name": "create",
            },
            {
                "name": "edit_organization",
                "resource_type": "organization",
                "action_name": "edit",
            },
            {
                "name": "delete_organization",
                "resource_type": "organization",
                "action_name": "delete",
            },
            {
                "name": "view_organization",
                "resource_type": "organization",
                "action_name": "view",
            },
            # Dependency permissions
            {
                "name": "create_dependency",
                "resource_type": "dependency",
                "action_name": "create",
            },
            {
                "name": "delete_dependency",
                "resource_type": "dependency",
                "action_name": "delete",
            },
            {
                "name": "view_dependency",
                "resource_type": "dependency",
                "action_name": "view",
            },
            # User management
            {
                "name": "manage_users",
                "resource_type": "identity",
                "action_name": "manage",
            },
            {"name": "view_users", "resource_type": "identity", "action_name": "view"},
            # Role management
            {"name": "manage_roles", "resource_type": "role", "action_name": "manage"},
            {"name": "view_roles", "resource_type": "role", "action_name": "view"},
            # Audit logs
            {
                "name": "view_audit_logs",
                "resource_type": "audit",
                "action_name": "view",
            },
        ]

        permissions = {}
        for perm_data in permissions_data:
            perm_id = db.permissions.insert(**perm_data)
            permissions[perm_data["name"]] = perm_id

        # Assign permissions to roles
        role_permissions_map = {
            "super_admin": list(permissions.keys()),  # All permissions
            "org_admin": [
                "create_entity",
                "edit_entity",
                "delete_entity",
                "view_entity",
                "view_organization",
                "create_dependency",
                "delete_dependency",
                "view_dependency",
                "view_users",
                "view_audit_logs",
            ],
            "editor": [
                "create_entity",
                "edit_entity",
                "view_entity",
                "view_organization",
                "create_dependency",
                "view_dependency",
            ],
            "viewer": ["view_entity", "view_organization", "view_dependency"],
        }

        for role_name, perm_names in role_permissions_map.items():
            role_id = roles[role_name]
            for perm_name in perm_names:
                perm_id = permissions[perm_name]
                db.role_permissions.insert(role_id=role_id, permission_id=perm_id)

        # Create root organization (default system org)
        root_org_id = db.organizations.insert(
            name="System",
            description="Root organization for system administrators",
            organization_type="organization",
            parent_id=None,
            tenant_id=system_tenant_id,
        )
    else:
        # Roles exist, fetch them for admin user creation
        for role_name in ["super_admin", "org_admin", "editor", "viewer"]:
            role = db(db.roles.name == role_name).select().first()
            if role:
                roles[role_name] = role.id

        # Get existing root organization
        root_org = db(db.organizations.name == "System").select().first()
        if root_org:
            root_org_id = root_org.id
        else:
            # Create root org if it doesn't exist
            root_org_id = db.organizations.insert(
                name="System",
                description="Root organization for system administrators",
                organization_type="organization",
                parent_id=None,
                tenant_id=system_tenant_id,
            )

    # Create default admin user if specified in environment (idempotent)
    # For portal authentication, username must be email format
    admin_email = os.getenv("ADMIN_EMAIL", "admin@localhost.local")
    admin_username = os.getenv("ADMIN_USERNAME", admin_email)  # Default to email
    admin_password = os.getenv("ADMIN_PASSWORD")

    if admin_password and root_org_id:
        # Check if admin user already exists (check both username and email)
        existing_admin = (
            db(
                (db.identities.username == admin_username)
                | (db.identities.email == admin_email)
            )
            .select()
            .first()
        )

        if not existing_admin:
            # Create admin user - username is email for portal auth
            admin_id = db.identities.insert(
                username=admin_email,  # Use email as username for portal auth
                email=admin_email,
                full_name="System Administrator",
                identity_type="human",
                auth_provider="local",
                password_hash=generate_password_hash(admin_password),
                is_active=True,
                is_superuser=True,
                organization_id=root_org_id,  # Link admin to root OU
            )

            # Assign super_admin role
            if "super_admin" in roles:
                db.user_roles.insert(
                    identity_id=admin_id,
                    role_id=roles["super_admin"],
                    scope="global",
                )

        # Also create portal user with global_role='admin' for v2.2.0+ web UI
        existing_portal_admin = (
            db(db.portal_users.email == admin_email).select().first()
        )
        if not existing_portal_admin:
            db.portal_users.insert(
                tenant_id=1,  # System tenant
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                global_role="admin",
                full_name="System Administrator",
                is_active=True,
            )

    # Create guest user if enabled (idempotent)
    enable_guest_login = os.getenv("ENABLE_GUEST_LOGIN", "false").lower() == "true"
    guest_username = os.getenv("GUEST_USERNAME", "guest")
    guest_password = os.getenv("GUEST_PASSWORD", "guest")

    if enable_guest_login and root_org_id:
        # Check if guest user already exists
        existing_guest = db(db.identities.username == guest_username).select().first()

        if not existing_guest:
            # Create guest user
            guest_id = db.identities.insert(
                username=guest_username,
                email=f"{guest_username}@localhost",
                full_name="Guest User (Read-Only)",
                identity_type="human",
                auth_provider="local",
                password_hash=generate_password_hash(guest_password),
                is_active=True,
                is_superuser=False,
                organization_id=root_org_id,  # Link guest to root OU
            )

            # Assign viewer role (read-only)
            if "viewer" in roles:
                db.user_roles.insert(
                    identity_id=guest_id,
                    role_id=roles["viewer"],
                    scope="global",
                )

    db.commit()


@contextmanager
def get_db_session() -> Generator[DAL, None, None]:
    """
    Get a database session context manager.

    Yields:
        Database session (PyDAL db instance)

    Example:
        with get_db_session() as session:
            user = session(session.identities).select().first()
    """
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
