# Elder Development Guide

Guide for setting up a local development environment and contributing to Elder.

## Quick Start

```bash
# Clone repository
git clone https://github.com/penguintechinc/elder.git
cd elder

# Setup environment
make setup

# Start development environment
make dev

# Run tests
make test
```

## Prerequisites

### Required Software

- **Python 3.13+** - [Download](https://www.python.org/downloads/)
- **Node.js 18+** - [Download](https://nodejs.org/)
- **Docker & Docker Compose** - [Download](https://www.docker.com/get-started)
- **Git** - [Download](https://git-scm.com/)

### Recommended Tools

- **VS Code** or **PyCharm** - IDE
- **Postman** - API testing
- **DBeaver** or **pgAdmin** - Database management
- **Redis Commander** - Redis management

## Development Environment Setup

### 1. Clone Repository

```bash
git clone https://github.com/penguintechinc/elder.git
cd elder
```

### 2. Setup Python Environment

```bash
# Create virtual environment
python3.13 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install Python dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Development tools
```

### 3. Setup Node.js Environment

```bash
# Navigate to web directory
cd web

# Install dependencies
npm install

# Return to root
cd ..
```

### 4. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit configuration
nano .env
```

**Minimum configuration for development:**

```bash
# Database
POSTGRES_DB=elder
POSTGRES_USER=elder
POSTGRES_PASSWORD=elder_dev_password

# Redis
REDIS_PASSWORD=elder_redis_password

# Flask
SECRET_KEY=dev-secret-key-change-me
FLASK_ENV=development

# Admin
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
ADMIN_EMAIL=admin@localhost.local
```

### 5. Start Services

```bash
# Start database and cache
docker-compose up -d postgres redis

# Run database migrations
alembic upgrade head

# Start API (in terminal 1)
python3 -m apps.api.main

# Start web UI (in terminal 2)
cd web && npm start
```

### Alternative: Use Make Commands

```bash
# Setup everything
make setup

# Start development environment
make dev

# Run in foreground (see logs)
docker-compose up
```

## Project Structure

```
elder/
├── apps/                      # Application code
│   ├── api/                   # Flask REST API
│   │   ├── api/v1/           # API endpoints
│   │   ├── models/           # Database models
│   │   ├── schemas/          # Validation schemas
│   │   ├── auth/             # Authentication
│   │   ├── grpc/             # gRPC server
│   │   └── main.py           # Application entry
│   └── connector/            # Connector service
│       ├── connectors/       # Connector implementations
│       ├── config/           # Configuration
│       └── main.py           # Service entry
├── web/                       # React web UI
│   ├── src/
│   │   ├── components/       # React components
│   │   ├── pages/            # Page components
│   │   ├── services/         # API clients
│   │   └── App.tsx           # Main app
│   └── package.json
├── shared/                    # Shared utilities
│   ├── database/             # Database utilities
│   ├── logging/              # Logging configuration
│   └── api_utils.py          # API helpers
├── alembic/                   # Database migrations
│   └── versions/             # Migration files
├── tests/                     # Test suites
│   ├── unit/                 # Unit tests
│   ├── integration/          # Integration tests
│   └── e2e/                  # End-to-end tests
├── docs/                      # Documentation
├── k8s/helm/elder/            # Helm chart (the deployment path)
├── infrastructure/            # Configs not yet folded into the chart
│   ├── docker/               # Postgres init SQL (docker-compose only)
│   ├── k8s/github-ci/        # CI ServiceAccount + RBAC reference manifests
│   └── monitoring/           # Prometheus/Alertmanager/Grafana configs
├── docker-compose.yml         # DEPRECATED — retained for reference only
├── Makefile                   # Build automation
└── .env.example               # Example configuration
```

## Development Workflow

### 1. Create a Feature Branch

```bash
# Update main branch
git checkout main
git pull origin main

# Create feature branch
git checkout -b feature/my-new-feature
```

### 2. Make Changes

```bash
# Edit code
nano apps/api/api/v1/my_endpoint.py

# Run tests
make test

# Run linting
make lint
```

### 3. Test Changes

```bash
# Run unit tests
pytest tests/unit/

# Run integration tests
pytest tests/integration/

# Run specific test
pytest tests/unit/test_organizations.py::test_create_organization

# Run with coverage
pytest --cov=apps --cov-report=html
```

### 4. Commit Changes

```bash
# Stage changes
git add .

# Commit with message
git commit -m "feat: add new feature

- Detailed description
- More details
"
```

### 5. Push and Create PR

```bash
# Push to origin
git push origin feature/my-new-feature

# Create pull request on GitHub
# (Follow link in terminal output)
```

## Running Tests

### Unit Tests

```bash
# Run all unit tests
pytest tests/unit/

# Run specific module
pytest tests/unit/test_entities.py

# Run with output
pytest tests/unit/ -v

# Run with coverage
pytest tests/unit/ --cov=apps.api --cov-report=html
```

### Integration Tests

```bash
# Start test environment
docker-compose -f docker-compose.test.yml up -d

# Run integration tests
pytest tests/integration/

# Stop test environment
docker-compose -f docker-compose.test.yml down
```

### End-to-End Tests

```bash
# Start full environment
docker-compose up -d

# Run E2E tests
pytest tests/e2e/

# Or use Cypress (for web UI)
cd web && npm run cy:run
```

## Code Quality

### Linting

```bash
# Python linting
flake8 apps/
black apps/ --check
mypy apps/

# Fix formatting
black apps/

# JavaScript/TypeScript linting
cd web && npm run lint

# Fix lint issues
cd web && npm run lint:fix
```

### Type Checking

```bash
# Python type checking
mypy apps/api/

# TypeScript type checking
cd web && npm run type-check
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
pre-commit install

# Run hooks manually
pre-commit run --all-files
```

## Database Development

### Migrations

```bash
# Create new migration
alembic revision --autogenerate -m "add new field"

# Edit migration file
nano alembic/versions/XXX_add_new_field.py

# Apply migration
alembic upgrade head

# Rollback migration
alembic downgrade -1

# Check current version
alembic current
```

### Database Access

```bash
# Connect to database
docker-compose exec postgres psql -U elder elder

# Run SQL query
docker-compose exec postgres psql -U elder elder -c "SELECT * FROM organizations LIMIT 5;"

# Dump database
docker-compose exec postgres pg_dump -U elder elder > dump.sql
```

## API Development

### Adding New Endpoint

1. **Create endpoint file:**

```python
# apps/api/api/v1/my_resource.py
from flask import Blueprint, request, jsonify

bp = Blueprint("my_resource", __name__)

@bp.route("", methods=["GET"])
def list_resources():
    """List resources."""
    return jsonify({"items": []}), 200

@bp.route("", methods=["POST"])
def create_resource():
    """Create a resource."""
    data = request.get_json()
    return jsonify(data), 201
```

2. **Register blueprint:**

```python
# apps/api/api/v1/__init__.py
from apps.api.api.v1.my_resource import bp as my_resource_bp

def register_blueprints(app):
    app.register_blueprint(my_resource_bp, url_prefix="/api/v1/my-resources")
```

3. **Add tests:**

```python
# tests/unit/test_my_resource.py
def test_list_resources(client):
    response = client.get("/api/v1/my-resources")
    assert response.status_code == 200
```

### Testing API Endpoints

```bash
# Using curl
curl -X GET http://localhost:5000/api/v1/entities

# Using httpie
http GET http://localhost:5000/api/v1/entities

# Using Python requests
python3 -c "import requests; print(requests.get('http://localhost:5000/api/v1/entities').json())"
```

## Frontend Development

### Running Web UI

```bash
cd web

# Development server
npm start

# Production build
npm run build

# Run tests
npm test

# Run linter
npm run lint
```

### Adding New Component

```tsx
// web/src/components/MyComponent.tsx
import React from 'react';

interface MyComponentProps {
  title: string;
}

export const MyComponent: React.FC<MyComponentProps> = ({ title }) => {
  return (
    <div className="my-component">
      <h1>{title}</h1>
    </div>
  );
};
```

## Debugging

### Python Debugging

```python
# Add breakpoint
import pdb; pdb.set_trace()

# Or use debugpy for VS Code
import debugpy
debugpy.listen(5678)
debugpy.wait_for_client()
```

### JavaScript Debugging

```javascript
// Add breakpoint
debugger;

// Use browser DevTools
// Chrome DevTools: F12
```

### Docker Debugging

```bash
# View logs
docker-compose logs -f api

# Execute command in container
docker-compose exec api bash

# Inspect container
docker inspect elder-api

# View container resources
docker stats
```

## Performance Profiling

### Python Profiling

```bash
# Profile API endpoint
python3 -m cProfile -o profile.stats apps/api/main.py

# Analyze profile
python3 -m pstats profile.stats
```

### Database Profiling

```sql
# Enable query logging
ALTER SYSTEM SET log_statement = 'all';
SELECT pg_reload_conf();

# View slow queries
SELECT query, mean_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```

## Development Tools

### Makefile Targets

```bash
make setup          # Setup development environment
make dev            # Start development environment
make test           # Run all tests
make test-unit      # Run unit tests
make test-integration  # Run integration tests
make lint           # Run linters
make format         # Format code
make clean          # Clean build artifacts
make build          # Build containers
make docker-build   # Build Docker images
make docker-push    # Push Docker images
```

### Environment Variables

```bash
# Development
FLASK_ENV=development
LOG_LEVEL=DEBUG

# Testing
FLASK_ENV=testing
DATABASE_URL=postgresql://test:test@localhost:5432/test_db

# Production
FLASK_ENV=production
LOG_LEVEL=INFO
```

## Troubleshooting

### Common Issues

**Port already in use:**
```bash
# Find process using port
lsof -i :5000

# Kill process
kill -9 <PID>
```

**Database connection error:**
```bash
# Check PostgreSQL is running
docker-compose ps postgres

# Restart PostgreSQL
docker-compose restart postgres
```

**Import errors:**
```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

## Best Practices

### Code Style

- Follow PEP 8 for Python
- Use type hints
- Write docstrings for functions
- Keep functions small and focused
- Use meaningful variable names

### Git Commits

- Use conventional commits format
- Write clear commit messages
- Keep commits atomic
- Reference issues in commits

### Testing

- Write tests for new features
- Maintain > 80% test coverage
- Test edge cases
- Use fixtures for test data

### Documentation

- Update docs with code changes
- Add docstrings to functions
- Include usage examples
- Keep README updated

## Resources

- [Architecture Documentation](../architecture/README.md)
- [API Documentation](../api/README.md)
- [Database Schema](../DATABASE.md)
- [Contributing Guide](../CONTRIBUTING.md)

## Getting Help

- **Documentation**: Check docs folder
- **Issues**: GitHub Issues
- **Discussions**: GitHub Discussions
- **Community**: Join our Discord (link TBD)
