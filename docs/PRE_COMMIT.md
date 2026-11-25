# Pre-Commit Checklist

**CRITICAL: This checklist MUST be followed before every commit.**

---

## Automated Pre-Commit Script

**Run the automated pre-commit script to execute all checks:**

```bash
./scripts/pre-commit/pre-commit.sh
```

This script will:
1. Run all checks in the correct order
2. Log output to `/tmp/pre-commit-elder-<epoch>.log`
3. Provide a summary of pass/fail status
4. Echo the log file location for review

**Individual check scripts** (run separately if needed):
- `./scripts/pre-commit/check-python.sh` - Python linting & security
- `./scripts/pre-commit/check-node.sh` - Node.js/React linting, audit & build
- `./scripts/pre-commit/check-security.sh` - All security scans
- `./scripts/pre-commit/check-secrets.sh` - Secret detection
- `./scripts/pre-commit/check-docker.sh` - Docker build & validation
- `./scripts/pre-commit/check-tests.sh` - Unit and integration tests

---

## Required Steps (In Order)

Before committing, run these checks in this order (or use `./scripts/pre-commit/pre-commit.sh`):

### Foundation Checks

- [ ] **Linters**: Python (flake8, black, isort, mypy), JavaScript (eslint), Go (golangci-lint)
  ```bash
  make lint
  ```

- [ ] **Security scans**: bandit, npm audit, gosec on modified packages
  ```bash
  cd apps/api && bandit -r .
  cd apps/web && npm audit
  ```

- [ ] **No secrets**: Verify no credentials, API keys, tokens, or passwords in code
  ```bash
  ./scripts/pre-commit/check-secrets.sh
  ```

### Build & Integration Verification

- [ ] **Build & Run**: Verify code compiles and containers start successfully
  ```bash
  docker compose build --no-cache api web
  docker compose up -d && sleep 10
  docker compose ps  # Verify all services are healthy
  ```

- [ ] **Smoke tests** (mandatory, <2 min): All containers healthy, APIs respond, UI loads
  ```bash
  make smoke-test
  ```

  **What smoke tests verify**:
  - All containers build without errors
  - All containers start and remain healthy
  - API health endpoints respond with 200 status
  - Database connectivity (Village ID lookup, organizations list)
  - Web UI pages load without JavaScript errors

  See: [TESTING.md - Smoke Tests](TESTING.md#smoke-tests)

### Feature Testing & Documentation

- [ ] **Mock data** (for testing features): Ensure 3-4 test items per entity
  ```bash
  make seed-mock-data
  ```

  This populates:
  - Organizations with hierarchy
  - Entities (servers, services, networks)
  - Resources (identities, software, data stores)
  - Dependencies and relationships
  - Village IDs for all created items

  For the unified Issues/support/CRM model (support issues, `customer_company`/
  `customer_contact` CRM entities, intake forms, assignment webhooks), also run:
  ```bash
  make seed-demo-unified
  ```

  Needed before capturing screenshots and UI testing.
  See: [TESTING.md - Mock Data Scripts](TESTING.md#mock-data-scripts)

- [ ] **Screenshots** (for UI changes): Capture current application state
  ```bash
  # Prerequisites: running make dev and make seed-mock-data
  node scripts/capture-screenshots.cjs
  # Or: npm run screenshots
  ```

  **What to screenshot**:
  - Login page (unauthenticated state)
  - All feature pages with realistic mock data showing 3-4 items
  - Various states/statuses when applicable
  - Empty states vs populated views
  - Village ID integration (if adding new resource types)

  Screenshots automatically removes old ones, captures fresh.
  Commit updated screenshots with feature/UI changes.

### Comprehensive Testing

- [ ] **Unit tests**: `make test-unit` or language-specific commands
  ```bash
  # Python API tests
  cd apps/api && python -m pytest tests/unit/ -v

  # Node.js Web UI tests
  cd apps/web && npm test
  ```

  Requirements:
  - Network isolated, mocked dependencies
  - Must pass before committing
  - Cover new code paths
  - Test Village ID generation and lookup if applicable

- [ ] **Integration tests**: Component interaction verification with real dependencies
  ```bash
  make test-integration
  ```

  See: [TESTING.md - Integration Tests](TESTING.md#integration-tests)

- [ ] **API tests** (if adding/modifying endpoints):
  ```bash
  # Test Village ID lookup endpoints, auth, CRUD operations
  cd apps/api && python -m pytest tests/api/ -v
  ```

  **Required test coverage**:
  - Authentication and authorization
  - All new endpoints
  - Village ID lookup and resolution
  - Error cases and validation
  - Database interactions

### Finalization

- [ ] **Version updates**: Update `.version` if releasing new version
  ```bash
  ./scripts/version/update-version.sh patch  # Or major/minor
  ```

- [ ] **Documentation**: Update docs if adding/changing workflows or features
  - Update DEVELOPMENT.md if setup changes
  - Update API.md if endpoints change
  - Update RELEASE_NOTES.md for version releases
  - Document Village ID system usage if adding new resource types

- [ ] **Docker builds**: Verify Dockerfile uses debian-slim base (no alpine)
  ```bash
  grep "^FROM" Dockerfile  # Should show debian-slim or similar
  hadolint Dockerfile      # Lint Dockerfile
  ```

- [ ] **Cross-architecture** (optional, for final validation before release):
  ```bash
  # Build and test on alternate architecture
  docker buildx build --platform linux/arm64 .  # If developing on amd64
  docker buildx build --platform linux/amd64 .  # If developing on arm64
  ```
  See: [TESTING.md - Cross-Architecture Testing](TESTING.md#cross-architecture-testing)

---

## Language-Specific Commands

### Python (API)

**Location**: `apps/api/`

```bash
# Linting
cd apps/api
flake8 .
black --check .
isort --check .
mypy .

# Security
bandit -r .
safety check

# Build & Run
python -m py_compile *.py              # Syntax check
pip install -r requirements.txt        # Dependencies
python -m flask run &                  # Verify it starts (then kill)

# Tests
python -m pytest tests/ -v             # All tests
python -m pytest tests/unit/ -v        # Unit tests only
python -m pytest tests/api/ -v         # API tests only
```

### Node.js / JavaScript / TypeScript / React

**Location**: `apps/web/`

```bash
# Linting
cd apps/web
npm run lint
# or
npx eslint .

# Security (REQUIRED)
npm audit                              # Check for vulnerabilities
npm audit fix                          # Auto-fix if possible

# Build & Run
npm run build                          # Compile/bundle
npm run dev &                          # Verify it starts (then kill)

# Tests
npm test                               # Run all tests
```

### Docker / Containers

```bash
# Lint Dockerfiles
hadolint Dockerfile
hadolint Dockerfile.prod

# Verify base image (debian-slim, NOT alpine)
grep -E "^FROM.*slim" Dockerfile

# Build & Run
docker build -t elder:test .                            # Build image
docker run -d --name test-container elder:test          # Start container
docker logs test-container                              # Check for errors
docker stop test-container && docker rm test-container  # Cleanup

# Docker Compose (all services)
docker compose build --no-cache  # Build all services
docker compose up -d              # Start all services
docker compose logs               # Check for errors
docker compose down               # Cleanup
```

---

## Commit Rules

- **NEVER commit automatically** unless explicitly requested by the user
- **NEVER push to remote repositories** under any circumstances
- **ONLY commit when explicitly asked** - never assume commit permission
- **Wait for approval** before running `git commit`
- **Update RELEASE_NOTES.md** on every version update or significant feature push
  - Prepend new entries to the top of the file (newest first)
  - Follow Keep a Changelog format
  - Include: New Features, Bug Fixes, Breaking Changes, Security Fixes
  - Reference related commits or PRs where applicable

---

## Security Scanning Requirements

### Before Every Commit

- **Run security audits on all modified packages**:
  - **Python packages**: Run `bandit -r .` and `safety check` on apps/api/
  - **Node.js packages**: Run `npm audit` on apps/web/
  - **Go packages**: Run `gosec ./...` on services/ (if applicable)

- **Do NOT commit if security vulnerabilities are found** - fix all issues first

- **Document vulnerability fixes** in commit message if applicable
  ```bash
  git commit -m "Fix: Update lodash to 4.17.21 (CVE-2021-23337)"
  ```

### Vulnerability Response

1. Identify affected packages and severity
2. Update to patched versions immediately
3. Test updated dependencies thoroughly
4. Document security fixes in commit messages
5. Verify no new vulnerabilities introduced

---

## API Testing Requirements

Before committing changes to API services:

- **Create and run API testing scripts** for each modified endpoint
- **Testing scope**: All new endpoints and modified functionality
- **Test files location**: `tests/api/` with service-specific subdirectories
  - `tests/api/auth/` - Authentication tests
  - `tests/api/organizations/` - Organization CRUD tests
  - `tests/api/village_id/` - Village ID lookup tests
  - `tests/api/entities/` - Entity tests
  - `tests/api/resources/` - Resource tests

- **Run before commit**: Each test script should be executable and pass completely

- **Test coverage for Elder**:
  - Health checks (`/healthz`)
  - Authentication (`/api/v1/auth/login`, `/api/v1/auth/logout`)
  - Village ID lookup (`/id/{village_id}`)
  - Organization CRUD (`/api/v1/organizations`)
  - Entity CRUD (`/api/v1/entities`)
  - Resource CRUD (`/api/v1/resources`)
  - Error cases and validation

**Example API test**:
```bash
# Test organization CRUD with Village IDs
curl -X POST http://localhost:5000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","description":"Test"}' | jq '.village_id'

VILLAGE_ID=$(curl -s http://localhost:5000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].village_id')

curl "http://localhost:5000/id/${VILLAGE_ID}" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Screenshot & Mock Data Requirements

### Prerequisites

Before capturing screenshots, ensure development environment is running with mock data:

```bash
make dev                   # Start all services
make seed-mock-data       # Populate with 3-4 test items per entity
make seed-demo-unified    # Populate unified-model demo data (support issues, CRM, intake forms, webhooks)
```

### Capture Screenshots

For all UI changes, update screenshots to show current application state with realistic data:

```bash
node scripts/capture-screenshots.cjs
# Or via npm script if configured: npm run screenshots
```

### What to Screenshot

- **Login page** (unauthenticated state)
- **Dashboard** (with mock data showing key metrics)
- **All feature pages** with realistic mock data showing:
  - 3-4 representative items per entity (organizations, entities, resources)
  - Various states/statuses when applicable (active, inactive, pending, etc.)
  - Empty states vs populated views
  - Village ID integration for new resource types
- **Entity/Resource pages** (with relationships visible)
- **Search and filter results**
- **Village ID lookup results** (if adding new resource types)

### Commit Guidelines

- Automatically removes old screenshots and captures fresh ones
- Commit updated screenshots with relevant feature/UI/documentation changes
- Screenshots demonstrate feature purpose and functionality
- Helpful error message if login fails: "Ensure mock data is seeded"

---

## Village ID System Testing

For changes affecting the Village ID system:

- [ ] **Village ID Generation**: Test generation of all ID types (tenant, org, item)
  ```bash
  python -c "
  from apps.api.utils.village_id import VillageIDGenerator
  gen = VillageIDGenerator()
  print('Tenant:', gen.generate_tenant_id())
  print('Organization:', gen.generate_org_id('a1b2-0000-00000000'))
  print('Item:', gen.generate_item_id('a1b2-c3d4-00000000'))
  "
  ```

- [ ] **Village ID Lookup**: Test `/id/{village_id}` endpoint with various resource types
  ```bash
  # Create items and verify lookups
  TOKEN=$(curl -s -X POST http://localhost:5000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin123"}' | jq -r '.access_token')

  VILLAGE_ID=$(curl -s http://localhost:5000/api/v1/organizations \
    -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].village_id')

  curl "http://localhost:5000/id/${VILLAGE_ID}" \
    -H "Authorization: Bearer $TOKEN" | jq '.'
  ```

- [ ] **Village ID Hierarchy**: Verify tenant/org context embedded in Village ID
  ```python
  # VillageID format: TTTT-OOOO-IIIIIIII
  # Extract components
  vid = "a1b2-c3d4-e5f67890"
  tenant = vid[:4]      # "a1b2"
  org = vid[5:9]        # "c3d4"
  item = vid[10:]       # "e5f67890"
  print(f"Tenant: {tenant}, Org: {org}, Item: {item}")
  ```

---

## Related Documentation

- **Development Setup**: [DEVELOPMENT.md](DEVELOPMENT.md)
- **Testing Guide**: [TESTING.md](TESTING.md)
- **Architecture**: [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md)
- **Database**: [DATABASE.md](DATABASE.md)
- **API Reference**: [API.md](API.md)

---

**Last Updated**: 2026-01-06
**Maintained by**: Penguin Tech Inc
**Project**: Elder 3.0.0
