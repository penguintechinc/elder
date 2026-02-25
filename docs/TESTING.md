# Testing Guide for Elder

This document outlines the testing strategy and local pre-commit checklist for the Elder project.

## Testing Strategy

### GitHub Actions CI (Automated)

The GitHub Actions CI pipeline runs on every push and pull request:

- **Code Quality**: Black, isort, flake8, mypy
- **Unit Tests**: Pytest with mocked dependencies (no external services)
- **Security Scans**: Trivy (filesystem), Semgrep, Dependabot
- **Build Tests**: Docker image builds (amd64 & arm64)
- **Web Builds**: TypeScript compilation, bundling

### Local Testing (Pre-Commit Checklist)

Run these checks **locally BEFORE committing** to catch issues early:

## Pre-Commit Checklist

### 1. Code Quality (2 min)

```bash
# Format code with Black (matches CI version 25.12.0)
docker compose exec -T api black apps/ shared/ --exclude=apps/api/grpc/generated

# Sort imports with isort (matches CI version 7.0.0)
docker compose exec -T api isort apps/ shared/ --skip apps/api/grpc/generated

# Lint with flake8
docker compose exec -T api flake8 apps/ shared/ --exclude=apps/api/grpc/generated
```

### 2. Unit Tests (5 min)

```bash
# Run unit tests (exclude integration and e2e)
docker compose exec -T api pytest tests/unit/ -v --tb=short -m "not integration and not e2e"
```

### 3. E2E Tests (10 min - Local Only)

**ONLY run locally if you have docker compose services running:**

```bash
make dev
pytest tests/e2e/ -v --tb=short
docker compose down
```

### 3b. Playwright Web UI Tests (15 min - Local Only)

Comprehensive browser automation tests that verify all pages load without JavaScript errors, tabs/modals work correctly, and form interactions function:

```bash
# Install Playwright dependencies (one-time)
cd web && npm install

# Run Playwright tests (headless mode)
make test-ui

# Or run with interactive UI for debugging
make test-ui-headed

# Or run with debugger for step-through debugging
make test-ui-debug
```

**What Playwright Tests Verify**:
- All main pages load without JavaScript errors
- Navigation between pages works correctly
- Tab switching on compute/entity detail pages
- Modals open/close and forms are interactive
- Form validation works
- React error boundaries don't trigger
- API error handling is graceful
- No console errors or warnings
- Responsive design across viewports (mobile, tablet, desktop)

**Test Reports**:
```bash
# View HTML test report after tests complete
open web/playwright-report/index.html  # macOS
xdg-open web/playwright-report/index.html  # Linux
```

### 4. Security Checks (2 min)

```bash
safety check  # Check Python dependencies
```

### 5. Build Verification

```bash
docker compose build --no-cache api
docker compose build --no-cache web
```

## What Gets Tested Where

| Test Type | GitHub Actions | Status |
|-----------|-----------------|--------|
| Code Quality | Yes | ✅ Blocking |
| Unit Tests | Yes | ✅ Blocking |
| E2E Tests (API) | No | ❌ Disabled |
| Web UI Tests (Playwright) | No | ⚠️ Local Only |
| Integration Tests | No | ❌ Disabled |
| Container Security | No | ❌ Disabled |

## Why Tests Are Disabled in CI

- **E2E Tests**: Require full docker compose; resource-intensive
- **Integration Tests**: Using outdated SQLAlchemy (needs PyDAL rewrite)
- **Container Security**: Registry propagation issues; Trivy runs on filesystem

## Logo Issue (ARM Deployment)

**Issue**: "cannot find /elder-logo.png" on ARM/other systems

**Root Cause**: Static assets path configuration differs between architectures/environments

**Solution**: Check the following:

1. Verify logo exists in web build:
```bash
docker compose exec web ls -la /app/public/elder-logo.png
```

2. Check web Dockerfile uses correct base image for your architecture:
```bash
# For ARM:
FROM node:18-alpine

# For amd64:
FROM node:18-alpine
```

3. Check environment-specific asset paths in web app config

4. Ensure web service is built with `--no-cache`:
```bash
docker compose build --no-cache web
```
