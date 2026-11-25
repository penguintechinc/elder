# Elder CI/CD Workflows

This document describes all GitHub Actions workflows used in the Elder project and their purposes.

## Workflow Overview

Elder uses comprehensive CI/CD automation to ensure code quality, security, and successful deployments. All workflows are triggered by changes to relevant code paths and include path-based filtering for efficiency.

## Core Workflows

### 1. **Continuous Integration (ci.yml)**

**Purpose**: Main testing and linting pipeline for Python and Node.js code
**Triggers**:
- Push to main, develop, develop/*, feature/* branches
- Pull requests to main, develop branches
- Daily schedule: 2 AM UTC

**Jobs**:
- `changes` - Path detection to skip unnecessary jobs
- `lint` - Python linting (flake8, black, isort, mypy)
- `security-scan` - Security scanning (Trivy, Safety)
- `python-test` - Python unit tests with coverage

**Security Scanning**:
- Trivy filesystem vulnerability scanning
- Safety check for Python dependencies
- Results uploaded to GitHub Security tab

### 2. **Test (test.yml)**

**Purpose**: Comprehensive testing suite
**Triggers**: Push and pull requests to main/develop branches

**Coverage**:
- Python code formatting and type checking
- Security vulnerability scanning
- Python dependency safety checks

### 3. **Build (build.yml)**

**Purpose**: Build automation for development
**Triggers**: Manual workflow dispatch

**Tasks**:
- Clean build artifacts
- Compile Python applications
- Verify build integrity

### 4. **Docker Build (docker-build.yml)**

**Purpose**: Multi-architecture Docker image building
**Triggers**: Push to main branch with .version changes

**Features**:
- Multi-arch builds (amd64, arm64)
- Debian-slim base images
- Optimized layer caching
- Registry push to ghcr.io

**Security**:
- Trivy image scanning
- Vulnerability reporting

### 5. **Version Release (version-release.yml)**

**Purpose**: Automated release creation on version updates
**Triggers**: Push to main branch with .version file changes

**Features**:
- .version path monitoring for changes
- Epoch64 timestamp detection (vMajor.Minor.Patch.epoch64)
- Semantic version extraction
- Automatic GitHub release creation
- Pre-release flag for non-release versions
- Duplicate prevention

**Version Detection**:
- Reads .version file (format: `3.0.0.1764083000`)
- Extracts semantic version (3.0.0)
- Validates against 0.0.0 (default, skip release)
- Creates pre-release with both semver and full timestamp

### 6. **Deploy (deploy.yml)**

**Purpose**: Deployment automation to environments
**Triggers**: Manual workflow dispatch or tag creation

**Features**:
- Environment-specific configurations
- Health check validation
- Rollback capabilities
- Database migration automation

### 7. **Release (release.yml)**

**Purpose**: Create tagged releases
**Triggers**: Manual workflow dispatch with version input

### 8. **Push (push.yml)**

**Purpose**: Push images to registry
**Triggers**: Successful Docker builds

### 9. **GitStream (gitstream.yml)**

**Purpose**: Automated code review and policy enforcement
**Triggers**: Pull request events

### 10. **Cron (cron.yml)**

**Purpose**: Scheduled maintenance tasks
**Triggers**: Daily schedule

**Tasks**:
- Dependency updates
- Security scans
- Cleanup operations

## Service Architecture

Elder includes the following services with independent deployment:

### Web Application (Node.js)
- React frontend
- Webpack bundling
- CORS enabled
- Prometheus metrics

### Connector Service (Python)
- Multi-connector framework
- LDAP, Okta, Google Workspace, Azure AD, vCenter, GCP
- Event streaming
- Rate limiting

### APIs
- REST endpoints
- gRPC services (enterprise)
- Quart framework (async Flask superset)
- JWT authentication

## Path-Based Filtering

Workflows use path detection to skip unnecessary jobs:

```yaml
paths-filter:
  python:
    - 'requirements.txt'
    - '**/*.py'
    - 'apps/web/**'
    - 'shared/licensing/*.py'
  node:
    - 'package.json'
    - 'package-lock.json'
    - 'web/package.json'
    - '**/*.js'
    - '**/*.ts'
    - '**/*.tsx'
    - 'web/src/**'
  docs:
    - 'docs/**'
    - '**/*.md'
```

## Version Management

### .version File Format
- **Format**: `vMajor.Minor.Patch.epoch64`
- **Example**: `3.0.0.1764083000`
- **Epoch64**: Unix timestamp of build time

### Version Update Process
```bash
# Update build timestamp (development builds)
./scripts/version/update-version.sh

# Increment patch version (bug fixes, patches)
./scripts/version/update-version.sh patch

# Increment minor version (features)
./scripts/version/update-version.sh minor

# Increment major version (breaking changes)
./scripts/version/update-version.sh major

# Set specific version
./scripts/version/update-version.sh 1 2 3
```

## Security Scanning

All workflows include security scanning:

### Code Security
- **bandit** - Python security linter
- **Safety** - Python dependency vulnerability scanning
- **Trivy** - Container image and filesystem scanning

### Dependency Security
- **Safety.io** integration for Python
- Pre-commit checks prevent vulnerable dependencies
- GitHub Dependabot alerts monitoring

### Build Security
- Container image scanning
- SARIF report generation
- GitHub Security tab integration

## Workflow Status Checks

All pull requests require:
1. CI pipeline pass (tests, linting)
2. Security scans pass
3. Code review approval
4. Branch protection rules

## Manual Workflows

### Docker Build
```bash
# Trigger manual Docker build
gh workflow run docker-build.yml -f branch=main
```

### Deploy
```bash
# Trigger deployment to staging
gh workflow run deploy.yml -f environment=staging
```

### Release
```bash
# Trigger release creation
gh workflow run release.yml -f version=1.0.0
```

## Environment Variables

Workflows use the following environment variables:

- `PYTHON_VERSION`: 3.13
- `NODE_VERSION`: 18
- `REGISTRY`: ghcr.io
- `REGISTRY_USERNAME`: GitHub username
- `REGISTRY_PASSWORD`: GitHub token

## Troubleshooting

### Workflow Failures

**Test Failures**:
- Check Python version compatibility (3.13+)
- Verify all dependencies installed
- Review test output for specific errors

**Build Failures**:
- Verify Dockerfile syntax
- Check Docker base image availability
- Review build logs for compilation errors

**Security Scan Failures**:
- Run Trivy locally to diagnose
- Review vulnerability details
- Update dependencies or suppress known issues

### Debugging

View workflow logs:
```bash
gh run view <run-id> --log
```

View specific job logs:
```bash
gh run view <run-id> --log --job <job-id>
```

Rerun failed workflows:
```bash
gh run rerun <run-id>
```

## Best Practices

1. **Keep .version file up-to-date** - Changes trigger releases
2. **Run tests locally** - Before pushing to avoid CI delays
3. **Use feature branches** - All development on feature/* branches
4. **Write meaningful commit messages** - Included in release notes
5. **Test Docker builds locally** - Before pushing changes
6. **Monitor workflow runs** - Check GitHub Actions tab regularly

## Related Documentation

- See [docs/STANDARDS.md](STANDARDS.md) for code quality standards
- See [docs/DEPLOYMENT.md](DEPLOYMENT.md) for deployment procedures
- See [CLAUDE.md](../CLAUDE.md) for development environment setup
