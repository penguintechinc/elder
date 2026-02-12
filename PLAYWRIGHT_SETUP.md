# Playwright Web UI Testing Suite - Setup and Implementation Guide

## Overview

This document describes the comprehensive Playwright web UI testing suite added to Elder v3.1.0. This testing infrastructure catches web UI bugs, JavaScript errors, form interactions, and component rendering issues that aren't covered by HTTP-only tests.

## What Was Added

### 1. Playwright Configuration (`web/playwright.config.ts`)

- **Cross-browser testing**: Chrome, Firefox, Safari
- **Parallel execution**: Configurable workers for fast test runs
- **Artifacts on failure**: Screenshots and videos automatically captured
- **HTML reporting**: Beautiful test reports with full traces
- **Timeout configuration**: 30-second tests, 10-second expectations
- **Environment variables**: `PLAYWRIGHT_BASE_URL`, `PLAYWRIGHT_WEBSERVER_DISABLED`

### 2. Comprehensive Test Suite (`web/tests/e2e/web-ui.spec.ts`)

The test suite is organized into 6 describe blocks covering different aspects:

#### Core Pages
- Homepage loads without errors
- Login page is accessible
- Dashboard loads after authentication

#### Navigation
- Switching between main pages (entities, services)
- Tab switching on compute page (VMs, Kubernetes, LXD)
- Entity detail page tabs

#### Forms and Modals
- Create entity modal opens and closes
- Form validation and error display
- LXD container creation form

#### Page-Specific Tests
- Services page loads
- Issues page loads
- Projects page loads
- Settings page loads

#### Error Handling
- Graceful handling of API errors (404s, etc.)
- No React error boundaries triggered
- Responsive design works across viewports (mobile/tablet/desktop)

#### Performance
- Page load time < 10 seconds
- No memory leaks on repeated navigation

### 3. Test Fixtures (`web/tests/e2e/fixtures.ts`)

Reusable utilities for common test patterns:

- `consoleMessages`: Automatically captures console errors and warnings
- `pageWithErrorCapture`: Page with built-in error tracking
- Uncaught exception handling
- Request failure tracking

### 4. Documentation (`web/tests/e2e/README.md`)

Comprehensive guide including:
- Quick start instructions
- Test structure and organization
- Configuration options
- Writing new tests (examples)
- Debugging techniques
- CI/CD integration patterns
- Troubleshooting common issues

### 5. Build System Integration

#### Makefile Targets
```bash
make test-ui              # Run tests in headless mode
make test-ui-headed       # Run with interactive Playwright UI
make test-ui-debug        # Run with debugger
```

#### Smoke Test Integration
- Added as Step 9 after REST API tests
- Works for both alpha (local) and beta (K8s) deployments
- Graceful fallback if Playwright not installed

### 6. Dependencies Updated

#### web/package.json
- Added `@playwright/test` (v1.48.2)
- Added npm scripts: `test:e2e`, `test:e2e:ui`, `test:e2e:debug`

## Quick Start

### Installation (First Time)

```bash
cd web
npm install  # Already done - installs Playwright and browsers
```

### Running Tests Locally

```bash
# From project root
make test-ui              # Headless mode (fastest)
make test-ui-headed       # Interactive with UI
make test-ui-debug        # Step-through debugger

# Or from web directory
cd web
npm run test:e2e
```

### Viewing Results

```bash
# After tests complete, open HTML report
cd web
npx playwright show-report

# Or manually
open playwright-report/index.html
```

## Integration with Smoke Tests

Tests automatically run as part of the smoke test pipeline:

```bash
# Local docker-compose deployment
./scripts/smoke-test.sh --alpha

# K8s beta deployment
./scripts/smoke-test.sh --beta
```

During smoke tests, Playwright tests run as Step 9 after REST API tests complete.

## Configuration for Different Environments

### Local Development
```bash
# Default configuration, runs against http://localhost:3005
make test-ui
```

### Staging/Beta (K8s)
```bash
# Against dal2.penguintech.io
export PLAYWRIGHT_BASE_URL=https://dal2.penguintech.io
export NODE_TLS_REJECT_UNAUTHORIZED=0
make test-ui
```

### Disable Web Server
For testing against existing deployments:
```bash
export PLAYWRIGHT_WEBSERVER_DISABLED=1
make test-ui
```

## What Tests Verify

✅ **Page Loads**
- All pages load without JavaScript errors
- HTML structure is correct
- Static assets are served

✅ **Navigation**
- Links and routing work correctly
- Tab switching functions
- Page transitions are smooth

✅ **Forms and Modals**
- Modals open and close properly
- Form submission works
- Validation errors display correctly
- Field interactions are responsive

✅ **React Components**
- No error boundaries triggered
- Component state management works
- React-Query requests complete

✅ **Error Handling**
- API errors are handled gracefully
- 404 errors don't crash the UI
- Network timeouts are handled

✅ **Performance**
- Pages load within 10 seconds
- Navigation doesn't leak memory
- Component interactions are responsive

## Testing Specific Features

### LXD Asset Creation
The test suite includes specific tests for creating LXD containers and VMs through the UI:

```typescript
test('LXD container creation form', async ({ page }) => {
  // Navigate to entities
  // Click create button
  // Select compute type
  // Select LXD container sub-type
  // Verify form appears with correct fields
});
```

### Compute Page Tabs
Tab switching is tested to ensure all compute types display correctly:

```typescript
test('tab switching on compute page', async ({ page }) => {
  // Navigate to compute page
  // Switch between VMs, Kubernetes, LXD/LXC tabs
  // Verify each tab content loads without errors
});
```

## Writing New Tests

### Basic Template

```typescript
import { test, expect } from '@playwright/test';

test('your test name', async ({ page }) => {
  // Arrange
  await page.goto('/page-path');

  // Act
  await page.locator('button:has-text("Action")').click();

  // Assert
  await expect(page.locator('h1')).toContainText('Expected');
});
```

### Using Error Capture

```typescript
import { test, expect } from './fixtures';

test('page with error detection', async ({ pageWithErrorCapture, consoleMessages }) => {
  await pageWithErrorCapture.goto('/');

  // Verify no console errors
  expect(consoleMessages.errors).toHaveLength(0);
});
```

## Common Selectors

```typescript
// By text
page.locator('button:has-text("Create")')

// By role
page.locator('[role="dialog"]')
page.locator('[role="tab"]')

// By type
page.locator('input[type="email"]')
page.locator('button[type="submit"]')

// By href/path
page.locator('a[href*="entities"]')

// Nested
page.locator('[role="dialog"] input[type="text"]')
```

## Debugging Tests

### Interactive Debugging

```bash
make test-ui-debug
```

Opens Playwright Inspector with full debugging:
- Step through test line by line
- Inspect DOM in real-time
- Execute JavaScript in console

### View Test Results

```bash
# HTML report with full trace viewer
cd web && npx playwright show-report

# Individual test artifacts
open web/test-results/[test-name]/
```

### Verbose Output

```bash
cd web
npx playwright test --verbose
```

## Performance Considerations

- **Parallel Execution**: Tests run in parallel across multiple workers
- **Browser Reuse**: Browsers shared within same worker for efficiency
- **Video on Failure**: Only videos for failed tests (saves disk space)
- **Screenshot Comparison**: Not used in current suite (future enhancement)

## CI/CD Integration

### Adding to GitHub Actions

```yaml
- name: Run Playwright tests
  run: |
    cd web
    npm run test:e2e
```

### Pre-Commit Hook

Could be added to pre-commit validation:

```bash
# In .pre-commit-config.yaml or pre-commit script
make test-ui
```

## Troubleshooting

### Tests timeout waiting for element

```typescript
// Increase timeout for specific assertion
await expect(element).toBeVisible({ timeout: 60000 });

// Or wait for specific condition
await page.waitForLoadState('networkidle');
```

### Console errors appearing

```typescript
// Filter expected errors
const criticalErrors = consoleMessages.errors.filter(
  e => !e.includes('expected-message')
);
```

### Tests pass locally but fail in CI

- Verify `PLAYWRIGHT_BASE_URL` is correct
- Check timing with `await page.waitForTimeout(500)`
- Use `page.pause()` to debug in interactive mode

### "Failed to connect to port"

- Verify web server is running
- Check `PLAYWRIGHT_BASE_URL` is correct
- Ensure no firewall blocking
- Check if port is already in use

## Files Changed

### New Files
- `web/playwright.config.ts` - Playwright configuration
- `web/tests/e2e/web-ui.spec.ts` - Comprehensive test suite
- `web/tests/e2e/fixtures.ts` - Test fixtures and utilities
- `web/tests/e2e/README.md` - Detailed testing documentation
- `PLAYWRIGHT_SETUP.md` - This file

### Modified Files
- `web/package.json` - Added Playwright dependency and scripts
- `Makefile` - Added test-ui targets
- `scripts/smoke-test.sh` - Added Playwright test step 9
- `docs/TESTING.md` - Added Playwright testing guide

### Commits
- `2fbc2af`: feat: Add comprehensive Playwright web UI testing suite
- `e1e6992`: chore: Integrate Playwright tests into build system and smoke tests

## Next Steps

### Optional Enhancements

1. **Visual Regression Testing**
   - Add `@playwright/test` screenshot comparison
   - Create baseline screenshots
   - Compare new runs against baseline

2. **Accessibility Testing**
   - Add `@axe-core/playwright` plugin
   - Test for WCAG 2.1 violations
   - Ensure keyboard navigation works

3. **Performance Testing**
   - Measure Core Web Vitals
   - Track page load metrics
   - Monitor API response times

4. **Load Testing**
   - Add K6 or Apache JMeter tests
   - Simulate concurrent users
   - Identify bottlenecks

5. **API Mocking**
   - Add fixtures for common API responses
   - Test error states
   - Mock slow/flaky endpoints

### Running with Every Commit

```bash
# Add to pre-commit hook or CI
make test-ui
```

### Monitoring Test Results

- Check `playwright-report/index.html` regularly
- Look for flaky tests
- Track failure trends
- Update tests as UI evolves

## Resources

- [Playwright Official Docs](https://playwright.dev)
- [Best Practices](https://playwright.dev/docs/best-practices)
- [Debugging Guide](https://playwright.dev/docs/debug)
- [CI/CD Setup](https://playwright.dev/docs/ci)
- [Community Forum](https://github.com/microsoft/playwright/discussions)

## Support

For issues or questions:
1. Check `web/tests/e2e/README.md` for troubleshooting
2. Review test examples in `web-ui.spec.ts`
3. Run with `--verbose` flag for detailed output
4. Use `make test-ui-debug` for interactive debugging

---

**Version**: 3.1.0
**Created**: 2026-02-11
**Added By**: Claude Opus 4.6
