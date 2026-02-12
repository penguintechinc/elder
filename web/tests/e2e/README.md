# Elder Playwright E2E Tests

Comprehensive end-to-end browser automation tests for the Elder web UI using Playwright.

## Overview

These tests verify that:
- All pages load without JavaScript errors
- Navigation and routing work correctly
- Forms and modals are interactive and functional
- Tab switching and component interactions work as expected
- React error boundaries don't trigger
- Console has no errors or critical warnings
- API integrations work end-to-end

## Quick Start

### Prerequisites

- Node.js 18+ (already installed in project)
- npm (should be available with Node.js)
- Running Elder web server (local or remote)

### Installation

```bash
# Install Playwright (one-time setup)
cd web
npm install

# This installs @playwright/test and required browser binaries
```

### Running Tests

```bash
# From project root directory
make test-ui              # Run all tests in headless mode
make test-ui-headed       # Run with interactive Playwright UI
make test-ui-debug        # Run with debugger for step-through

# Or from web directory
cd web
npm run test:e2e          # Headless
npm run test:e2e:ui       # Interactive UI
npm run test:e2e:debug    # Debug mode
```

### Viewing Results

After tests complete, open the HTML report:

```bash
# From web directory
npx playwright show-report

# Or manually
open playwright-report/index.html  # macOS
xdg-open playwright-report/index.html  # Linux
```

## Test Files

### web-ui.spec.ts

Main test suite covering:
- **Core Pages**: Homepage, login, dashboard loads
- **Navigation**: Tab switching, page-to-page navigation
- **Forms and Modals**: Create entity modals, form validation, LXD asset creation
- **Page-Specific**: Services, issues, projects pages
- **Error Handling**: API errors, React error boundaries, responsive design
- **Performance**: Page load times, navigation without memory leaks

### fixtures.ts

Custom Playwright fixtures for common test utilities:
- `consoleMessages`: Captures console errors and warnings
- `pageWithErrorCapture`: Page with automatic error capture

## Configuration

Configuration in `playwright.config.ts`:

- **Base URL**: Controlled by `PLAYWRIGHT_BASE_URL` env var (default: `http://localhost:3005`)
- **Browsers**: Chrome, Firefox, Safari by default
- **Workers**: Parallel execution for speed
- **Timeout**: 30 seconds per test
- **Retries**: 2 retries on CI, 0 on local
- **Artifacts**: Screenshots/videos on failure, HTML report

## Environment Variables

```bash
# Base URL for tests
PLAYWRIGHT_BASE_URL=http://localhost:3005

# Disable built-in web server (for remote deployments)
PLAYWRIGHT_WEBSERVER_DISABLED=1

# For beta/HTTPS deployments with self-signed certs
NODE_TLS_REJECT_UNAUTHORIZED=0

# CI environment (enables retries, single worker)
CI=1
```

## Integration with Smoke Tests

Tests are automatically run as part of `smoke-test.sh`:

```bash
# Local docker-compose deployment
./scripts/smoke-test.sh --alpha

# K8s beta deployment
./scripts/smoke-test.sh --beta
```

During smoke tests, Playwright tests run as step 9 after REST API tests complete.

## Writing New Tests

### Basic Test Structure

```typescript
import { test, expect } from '@playwright/test';

test('descriptive test name', async ({ page }) => {
  // Arrange
  await page.goto('/page-path');

  // Act
  await page.locator('button:has-text("Click Me")').click();

  // Assert
  await expect(page.locator('h1')).toContainText('Expected Text');
});
```

### Using Fixtures for Error Capture

```typescript
import { test, expect } from './fixtures';

test('page loads with no errors', async ({ pageWithErrorCapture, consoleMessages }) => {
  await pageWithErrorCapture.goto('/');
  await pageWithErrorCapture.waitForLoadState('networkidle');

  // Verify no console errors
  expect(consoleMessages.errors).toHaveLength(0);
});
```

### Common Patterns

**Waiting for elements**:
```typescript
await page.waitForSelector('button');
await page.waitForLoadState('networkidle');
await page.waitForTimeout(500);
```

**Finding elements**:
```typescript
page.locator('button:has-text("Create")');
page.locator('[role="dialog"]');
page.locator('input[type="email"]');
page.locator('a[href*="entities"]');
```

**Clicking and typing**:
```typescript
await page.locator('button').click();
await page.locator('input[type="text"]').fill('value');
await page.locator('input[type="text"]').type('value', { delay: 100 });
```

**Navigation**:
```typescript
await page.goto('/entities');
await expect(page).toHaveURL(/entities/);
await page.goBack();
```

## Debugging Tests

### Interactive Debugging

```bash
make test-ui-debug
```

Opens Playwright Inspector with full debugging capabilities.

### Viewing Failures

- Screenshots saved to `test-results/` for failed tests
- Videos saved for failed tests (if enabled)
- HTML report shows full trace and artifacts

### Verbose Output

```bash
cd web
npx playwright test --verbose
```

## CI/CD Integration

### GitHub Actions

Tests can be added to CI pipeline:

```yaml
- name: Run Playwright tests
  run: |
    export PLAYWRIGHT_BASE_URL=http://localhost:3005
    npm run test:e2e
```

### Pre-Commit Hook

Could be added to pre-commit validation:

```bash
make test-ui  # Runs before commit
```

## Troubleshooting

### Tests timeout waiting for element

- Increase test timeout: `{ timeout: 60000 }`
- Check if element selector is correct
- Verify page is fully loaded: `await page.waitForLoadState('networkidle')`

### Console errors appearing

- These often come from third-party libraries
- Filter expected errors: `errors.filter(e => !e.includes('expected-message'))`
- Real React errors usually contain "React" in message

### Tests pass locally but fail in CI

- Ensure `PLAYWRIGHT_BASE_URL` is set correctly
- Check for timing issues: add `await page.waitForTimeout(500)`
- Verify service dependencies are running

### "Failed to connect to port"

- Web server not running
- Wrong `PLAYWRIGHT_BASE_URL`
- Firewall blocking connection

## Performance Considerations

- Tests run in parallel by default (configurable workers)
- Browser instances reused within same worker
- Video recording only on failures (performance)
- Consider disabling for purely unit test CI pipelines

## Resources

- [Playwright Documentation](https://playwright.dev)
- [Best Practices](https://playwright.dev/docs/best-practices)
- [Debugging Guide](https://playwright.dev/docs/debug)
- [CI/CD Guide](https://playwright.dev/docs/ci)
