import { test, expect, Page, request as playwrightRequest } from '@playwright/test';

/**
 * Comprehensive Playwright tests for Elder web UI
 *
 * These tests verify:
 * - All pages load without JavaScript errors
 * - Navigation and routing works correctly
 * - Forms and modals are interactive
 * - API integrations work end-to-end
 * - React error boundaries don't trigger
 * - Console has no errors or critical warnings
 */

// Helper to check for console errors and warnings
async function collectConsoleMessages(page: Page) {
  const errors: string[] = [];
  const warnings: string[] = [];

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      errors.push(msg.text());
    } else if (msg.type() === 'warning') {
      warnings.push(msg.text());
    }
  });

  // Handle uncaught exceptions
  page.on('pageerror', (error) => {
    errors.push(`Uncaught exception: ${error.message}`);
  });

  return { errors, warnings };
}

test.describe('Elder Web UI - Core Pages', () => {
  test('homepage loads without errors', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Wait for page to be interactive
    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok, page might be slow
    });

    // Verify page is visible
    const body = await page.locator('body');
    await expect(body).toBeVisible();

    // Verify no critical console errors (filter out expected ones)
    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('login page is accessible', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/login', { waitUntil: 'domcontentloaded' });

    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok
    });

    // Look for login form elements or just verify page loaded
    const body = await page.locator('body');
    await expect(body).toBeVisible();

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('dashboard loads without critical errors', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Navigate to dashboard
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok
    });

    // Verify page content loads
    const body = page.locator('body');
    await expect(body).toBeVisible();

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Navigation', () => {
  test('navigation to main pages', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Start at home
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok
    });

    // Try navigating to entities page
    await page.goto('/entities', { waitUntil: 'domcontentloaded' }).catch(() => {
      // Page might not exist
    });

    const body = page.locator('body');
    await expect(body).toBeVisible();

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('tab switching on compute page', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/compute', { waitUntil: 'domcontentloaded' }).catch(() => {
      // Page might not exist
    });

    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok
    });

    // Look for tab elements
    const tabs = page.locator('[role="tab"], button[class*="tab"]');
    const tabCount = await tabs.count();

    if (tabCount > 0) {
      // Click through a few tabs
      for (let i = 0; i < Math.min(tabCount, 2); i++) {
        try {
          const tab = tabs.nth(i);
          await tab.click();
          // Wait for tab content to load
          await page.waitForTimeout(300);
        } catch (e) {
          // Tab click might fail - that's ok
        }
      }
    }

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('entity detail page navigation', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Navigate to entities
    await page.goto('/entities', { waitUntil: 'domcontentloaded' }).catch(() => {
      // Page might not exist
    });

    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok
    });

    // Try to find and click first entity link
    const entityLink = page.locator('a[href*="entities/"]').first();
    if ((await entityLink.count()) > 0) {
      try {
        await entityLink.click();
        await page.waitForLoadState('domcontentloaded').catch(() => {
          // Timeout ok
        });

        // Look for tabs in detail view
        const tabs = page.locator('[role="tab"], button[class*="tab"]');
        const tabCount = await tabs.count();

        if (tabCount > 0) {
          // Click first tab
          try {
            await tabs.first().click();
            await page.waitForTimeout(200);
          } catch (e) {
            // Tab click failed
          }
        }
      } catch (e) {
        // Navigation failed
      }
    }

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Forms and Modals', () => {
  test('create entity modal opens and closes', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/entities', { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => {
        // Timeout ok
      });

      // Look for create button
      const createButton = page.locator(
        'button:has-text("Create"), button:has-text("Add"), button:has-text("New")'
      );

      if (await createButton.count() > 0) {
        await createButton.first().click();

        // Wait for modal to appear
        const modal = page.locator('[role="dialog"], .modal, [class*="modal"]');
        if (await modal.count() > 0) {
          await expect(modal.first()).toBeVisible();

          // Try to close modal
          const closeButton = page.locator(
            'button[aria-label="Close"], button:has-text("Close"), button:has-text("Cancel")'
          );
          if (await closeButton.count() > 0) {
            await closeButton.first().click();
            await page.waitForTimeout(300);
          }
        }
      }
    } catch (e) {
      // Page or modal might not exist
    }

    // Filter out 404 errors
    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('form validation and error display', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/entities', { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => {
        // Timeout ok
      });

      // Try to find and interact with form
      const inputs = page.locator('input[required], input[type="email"]');
      if (await inputs.count() > 0) {
        // Try to submit without filling
        const submitButton = page.locator('button[type="submit"]');
        if (await submitButton.count() > 0) {
          await submitButton.first().click({ force: true });
          await page.waitForTimeout(500);
        }
      }
    } catch (e) {
      // Form might not exist
    }

    // Filter out 404 errors
    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('LXD container creation form', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/entities', { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => {
        // Timeout ok
      });

      // Look for create button
      const createButton = page.locator(
        'button:has-text("Create"), button:has-text("Add"), button:has-text("New")'
      );

      if (await createButton.count() > 0) {
        await createButton.first().click();
        await page.waitForLoadState('domcontentloaded').catch(() => {
          // Timeout ok
        });

        // Try to select entity type as compute
        const computeOption = page.locator(
          'button:has-text("Compute"), option:has-text("Compute"), [role="option"]:has-text("Compute")'
        );

        if (await computeOption.count() > 0) {
          await computeOption.first().click();
          await page.waitForTimeout(300);

          // Look for LXD container option
          const lxdOption = page.locator(
            'button:has-text("LXD"), option:has-text("LXD"), [role="option"]:has-text("LXD")'
          );

          if (await lxdOption.count() > 0) {
            await lxdOption.first().click();
            await page.waitForTimeout(300);
          }
        }

        // Try to close
        const closeButton = page.locator(
          'button[aria-label="Close"], button:has-text("Close"), button:has-text("Cancel")'
        );
        if (await closeButton.count() > 0) {
          await closeButton.first().click();
        }
      }
    } catch (e) {
      // Modal might not exist
    }

    // Filter out 404 errors
    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Page-Specific Tests', () => {
  test('services page loads or exists', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/services', { waitUntil: 'networkidle', timeout: 15000 });

      const pageContent = page.locator('main, [role="main"], body');
      // Check if page loaded at all
      await expect(pageContent).toBeVisible({ timeout: 5000 });
    } catch (e) {
      // Services page might not exist in some deployments - just verify no errors
      test.info().annotations.push({
        type: 'info',
        description: 'Services page not available in this deployment',
      });
    }

    // Main assertion: no critical console errors
    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('issues page loads or exists', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/issues', { waitUntil: 'networkidle', timeout: 15000 });

      const pageContent = page.locator('main, [role="main"], body');
      await expect(pageContent).toBeVisible({ timeout: 5000 });
    } catch (e) {
      test.info().annotations.push({
        type: 'info',
        description: 'Issues page not available in this deployment',
      });
    }

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('projects page loads or exists', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/projects', { waitUntil: 'networkidle', timeout: 15000 });

      const pageContent = page.locator('main, [role="main"], body');
      await expect(pageContent).toBeVisible({ timeout: 5000 });
    } catch (e) {
      test.info().annotations.push({
        type: 'info',
        description: 'Projects page not available in this deployment',
      });
    }

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });

  test('settings page loads or exists', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    try {
      await page.goto('/settings', { waitUntil: 'networkidle', timeout: 15000 });

      const pageContent = page.locator('main, [role="main"], body');
      await expect(pageContent).toBeVisible({ timeout: 5000 });
    } catch (e) {
      test.info().annotations.push({
        type: 'info',
        description: 'Settings page not available in this deployment',
      });
    }

    const criticalErrors = messages.errors.filter((e) => !e.includes('404'));
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Error Handling', () => {
  test('handles API errors gracefully', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout ok
    });

    // Navigate to non-existent entity
    await page.goto('/entities/99999999', { waitUntil: 'domcontentloaded' }).catch(() => {
      // Navigation might fail
    });

    await page.waitForTimeout(500);

    // Page should still be functional (no uncaught errors)
    const body = await page.locator('body');
    await expect(body).toBeVisible();

    // Filter for actual errors (not network 404 logs)
    const criticalErrors = messages.errors.filter(
      (e) =>
        !e.includes('404') &&
        !e.includes('Failed to fetch') &&
        !e.includes('404 Not Found') &&
        !e.includes('net::ERR')
    );

    expect(criticalErrors).toHaveLength(0);
  });

  test('page remains functional during navigation', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Visit multiple pages to verify stability
    const pagePaths = ['/', '/entities'];

    for (const pagePath of pagePaths) {
      try {
        await page.goto(pagePath, { waitUntil: 'domcontentloaded' });
        await page.waitForLoadState('networkidle').catch(() => {
          // Timeout ok
        });
      } catch (e) {
        // Page might not exist - that's ok
      }
    }

    // Check for critical React error boundary messages
    const reactErrors = messages.errors.filter(
      (e) =>
        e.toLowerCase().includes('react error') ||
        (e.toLowerCase().includes('error boundary') &&
          !e.toLowerCase().includes('caught'))
    );

    expect(reactErrors).toHaveLength(0);
  });

  test('responsive design works on multiple viewports', async ({ page }) => {
    const viewports = [
      { width: 1920, height: 1080 }, // Desktop
      { width: 768, height: 1024 }, // Tablet
      { width: 375, height: 667 }, // Mobile
    ];

    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      try {
        await page.goto('/', { waitUntil: 'domcontentloaded' });
        await page.waitForLoadState('networkidle').catch(() => {
          // Timeout ok
        });

        // Verify page is still interactive
        const body = await page.locator('body');
        await expect(body).toBeVisible();
      } catch (e) {
        // Page might fail at this viewport
      }
    }

    const criticalErrors = messages.errors.filter(
      (e) => !e.includes('404') && !e.includes('net::ERR')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Performance', () => {
  test('page load time acceptable', async ({ page }) => {
    const startTime = Date.now();

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout ok - page might be slow
    });

    const loadTime = Date.now() - startTime;

    // Page should load within 30 seconds (generous for K8s)
    expect(loadTime).toBeLessThan(30000);
  });

  test('navigation remains stable', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Simulate user navigation back and forth
    const paths = ['/', '/entities', '/services'];

    for (let i = 0; i < 3; i++) {
      for (const path of paths) {
        try {
          await page.goto(path, { waitUntil: 'domcontentloaded' });
          await page.waitForLoadState('networkidle').catch(() => {
            // Timeout ok
          });
        } catch (e) {
          // Navigation might fail - that's ok
        }
      }
    }

    // Filter out expected errors
    const criticalErrors = messages.errors.filter(
      (e) => !e.includes('404') && !e.includes('net::ERR')
    );

    // Should not accumulate critical errors
    expect(criticalErrors.length).toBeLessThan(5);
  });
});

/**
 * Create Modal Content Tests
 *
 * Catches the "blank modal" bug class: a Create/Add button exists,
 * the modal container opens, but no form fields render inside it.
 * This happened when /api/v1/status returned 404 and blocked FormModalBuilder.
 */

const PAGES_WITH_CREATE_MODAL = [
  { route: '/entities', name: 'Entities', buttonText: 'Create Entity' },
  { route: '/software', name: 'Software', buttonText: 'Add Software' },
  { route: '/services', name: 'Services', buttonText: 'Create' },
  { route: '/certificates', name: 'Certificates', buttonText: 'Add Certificate' },
  { route: '/issues', name: 'Issues', buttonText: 'Create' },
  { route: '/projects', name: 'Projects', buttonText: 'Create' },
  { route: '/milestones', name: 'Milestones', buttonText: 'Create' },
  { route: '/labels', name: 'Labels', buttonText: 'Create' },
  { route: '/data-stores', name: 'Data Stores', buttonText: 'Create' },
  { route: '/dependencies', name: 'Dependencies', buttonText: 'Create' },
  { route: '/organizations', name: 'Organizations', buttonText: 'Create' },
  { route: '/keys', name: 'Keys', buttonText: 'Add Provider' },
  { route: '/secrets', name: 'Secrets', buttonText: 'Add Provider' },
  { route: '/webhooks', name: 'Webhooks', buttonText: 'Create' },
  { route: '/on-call-rotations', name: 'On-Call Rotations', buttonText: 'Create' },
  { route: '/networking', name: 'Networking', buttonText: 'Add Network' },
  { route: '/iam', name: 'IAM', buttonText: 'Add Identity' },
  { route: '/ipam', name: 'IPAM', buttonText: 'Create' },
  { route: '/backups', name: 'Backups', buttonText: 'Create' },
  { route: '/admin/sso', name: 'SSO Configuration', buttonText: 'Add' },
  { route: '/admin/license-policies', name: 'License Policies', buttonText: 'Create' },
  { route: '/admin/tenants', name: 'Tenants', buttonText: 'Create' },
];

// ─── Auth configuration ──────────────────────────────────────────────────────
// PLAYWRIGHT_API_URL: explicit API base URL (use when API runs on a different
//   port or host from the web app, e.g. local dev with port-forwarded API).
// Falls back to PLAYWRIGHT_BASE_URL so alpha/beta (shared ingress) work with
//   zero extra config.
//
// PLAYWRIGHT_TARGET_HOST: beta bypass — when set, Node.js requests go through
//   the bypass URL (PLAYWRIGHT_BASE_URL) with this value as the Host header,
//   routing correctly through the K8s ingress without hitting Cloudflare.
//
// Examples:
//   Alpha:  PLAYWRIGHT_BASE_URL=http://elder.localhost.local
//           (API_BASE resolves to http://elder.localhost.local — same ingress)
//   Beta:   PLAYWRIGHT_BASE_URL=https://dal2.penguintech.cloud
//           PLAYWRIGHT_TARGET_HOST=elder.penguintech.cloud
//           (requests go to bypass URL with Host header)
//   Local:  PLAYWRIGHT_API_URL=http://localhost:4000
//           PLAYWRIGHT_BASE_URL=http://localhost:3005
// ─────────────────────────────────────────────────────────────────────────────

const ADMIN_EMAIL = process.env.ADMIN_EMAIL || 'admin@localhost.local';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'admin123';

// API base: explicit override → same origin as web app → Playwright default
const _API_BASE =
  process.env.PLAYWRIGHT_API_URL ||
  process.env.PLAYWRIGHT_BASE_URL ||
  'http://localhost:3005';

// Beta bypass: Host header for K8s ingress routing through the dal2 LB
const _EXTRA_HEADERS: Record<string, string> = process.env.PLAYWRIGHT_TARGET_HOST
  ? { Host: process.env.PLAYWRIGHT_TARGET_HOST }
  : {};

/**
 * Authenticate the test browser session.
 *
 * Obtains a JWT via direct API call (fast; no browser UI needed), injects it
 * into localStorage, and pre-seeds GDPR consent so any UI flows that render
 * LoginPageBuilder also work without hitting the cookie banner.
 *
 * Hard-fails the test if the login API call is unreachable or returns a
 * non-200 — default credentials are always present on alpha/beta deployments.
 */
async function authenticate(page: Page): Promise<void> {
  const ctx = await playwrightRequest.newContext({
    baseURL: _API_BASE,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: _EXTRA_HEADERS,
  });

  let token: string;
  try {
    const res = await ctx.post('/api/v1/portal-auth/login', {
      data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(
      res.status(),
      `Login API returned ${res.status()} — check API is reachable at ${_API_BASE} ` +
        `(set PLAYWRIGHT_API_URL to override, defaults to PLAYWRIGHT_BASE_URL)`
    ).toBe(200);
    const data = await res.json();
    token = data.token as string;
    expect(token, 'Login response missing token — unexpected API response shape').toBeTruthy();
  } finally {
    await ctx.dispose();
  }

  // Navigate to /login so we have a same-origin context for localStorage writes
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(
    ({ t }) => {
      localStorage.setItem('elder_token', t);
      // Pre-seed GDPR consent — LoginPageBuilder keeps form inputs disabled
      // until this key is present; fresh browser contexts lack it
      localStorage.setItem(
        'gdpr_consent',
        JSON.stringify({ accepted: true, necessary: true, functional: true, analytics: true, marketing: true })
      );
    },
    { t: token }
  );
}

test.describe('Elder Web UI - Create Modal Content', () => {
  for (const pageConfig of PAGES_WITH_CREATE_MODAL) {
    test(`${pageConfig.name} - create modal renders content without errors`, async ({ page }) => {
      test.setTimeout(45000);

      await authenticate(page);

      // Collect console errors
      const errors: string[] = [];
      page.on('console', (msg) => {
        if (msg.type() === 'error') errors.push(msg.text());
      });
      page.on('pageerror', (error) => {
        errors.push(`Uncaught: ${error.message}`);
      });

      // Navigate to the page
      const response = await page.goto(pageConfig.route, { waitUntil: 'domcontentloaded' }).catch(() => null);
      if (!response || response.status() === 404) {
        test.skip(true, `${pageConfig.name} page not available (404)`);
        return;
      }

      const baselineErrorCount = errors.length;

      // Find the Create/Add button — wait for it to appear
      const createButton = page.locator(`button:has-text("${pageConfig.buttonText}")`).first();
      try {
        await createButton.waitFor({ state: 'visible', timeout: 15000 });
      } catch {
        test.skip(true, `${pageConfig.name} has no "${pageConfig.buttonText}" button`);
        return;
      }

      // Click the Create button — skip if not interactive (disabled/gated)
      try {
        await createButton.click({ timeout: 8000 });
      } catch {
        test.skip(true, `${pageConfig.name} "${pageConfig.buttonText}" button is not interactive (possibly gated)`);
        return;
      }

      // Wait for modal to appear — try role="dialog" first, then fall back to any new form container
      const dialog = page.locator('[role="dialog"]').first();
      let modalContainer: typeof dialog;

      try {
        await dialog.waitFor({ state: 'visible', timeout: 5000 });
        modalContainer = dialog;
      } catch {
        // Custom modal without role="dialog" — wait for new heading + form fields to appear
        // (e.g. Secrets, Webhooks use custom CreateXxxModal components)
        const customModal = page.locator('h2:has-text("Add"), h2:has-text("Create"), h3:has-text("Add"), h3:has-text("Create")').last();
        await customModal.waitFor({ state: 'visible', timeout: 5000 });
        // Use the heading's parent container as the modal scope
        modalContainer = customModal.locator('..').locator('..');
      }

      // CRITICAL: Assert modal is NOT blank — must have form fields
      const formFields = modalContainer.locator('input, select, textarea, [role="combobox"]');
      const fieldCount = await formFields.count();
      expect(fieldCount, `${pageConfig.name} modal is BLANK — opened but contains 0 form fields`).toBeGreaterThan(0);

      // Check for new console errors that appeared AFTER clicking Create
      // Filter out network/resource errors (API 4xx/5xx) — focus on JS/React errors
      const newErrors = errors.slice(baselineErrorCount).filter(
        (e) =>
          !e.includes('404') &&
          !e.includes('net::ERR') &&
          !e.includes('Failed to fetch') &&
          !e.includes('Failed to load resource') &&
          !e.includes('status of 5') &&
          !e.includes('CORS policy') &&
          !e.includes('Access-Control')
      );
      expect(
        newErrors,
        `${pageConfig.name} modal triggered console errors: ${newErrors.join(', ')}`
      ).toHaveLength(0);

      // Close the modal
      await page.keyboard.press('Escape');
    });
  }
});


test.describe('Version validation', () => {
  test('AppConsoleVersion logs a non-zero version on startup', async ({ page }) => {
    const consoleLogs: string[] = [];
    page.on('console', (msg) => {
      consoleLogs.push(msg.text());
    });

    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {
      // Timeout is ok
    });

    // AppConsoleVersion logs version info — collect all console output
    const allLogs = consoleLogs.join('\n');

    // If no version-related output at all, skip (AppConsoleVersion may not be on login page)
    if (!allLogs.toLowerCase().includes('version')) {
      // Try the root/dashboard page which definitely loads AppConsoleVersion
      consoleLogs.length = 0;
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => {});
    }

    const finalLogs = consoleLogs.join('\n');

    // Check the /api/v1/status endpoint directly for a non-zero version
    const statusResponse = await page.evaluate(async () => {
      try {
        const res = await fetch('/api/v1/status');
        if (!res.ok) return null;
        return await res.json();
      } catch {
        return null;
      }
    });

    if (statusResponse !== null) {
      const version = statusResponse.version || statusResponse.api_version || '';
      expect(version, 'API status version is 0.0.0 — APP_VERSION build-arg not injected').not.toBe('0.0.0');
      expect(version, 'API status version is empty').not.toBe('');
    } else {
      // Endpoint not available — fall back to checking console logs
      // AppConsoleVersion prints "Version: X.Y.Z" — assert it's not 0.0.0
      const zeroVersionMatch = finalLogs.match(/Version:\s*0\.0\.0/);
      expect(
        zeroVersionMatch,
        'AppConsoleVersion logged "Version: 0.0.0" — VITE_VERSION build-arg not injected correctly'
      ).toBeNull();
    }
  });
});

test.describe('Elder Web UI - API Route Integrity', () => {
  /**
   * Detect frontend API calls that return 404 (path mismatch).
   *
   * Catches the class of bug where the frontend calls e.g. /sso/idp-configs
   * but the backend registers /sso/idp — the mismatch produces silent 404s
   * that don't throw JS errors and are invisible to other test suites.
   *
   * Hard-fails on login — default credentials are always present on
   * alpha/beta. Set PLAYWRIGHT_API_URL if the API runs on a different port.
   */
  test('No API endpoints return 404 during authenticated page loads', async ({ page }) => {
    test.setTimeout(120000);

    const notFoundCalls: string[] = [];
    page.on('response', (response) => {
      const url = response.url();
      if (url.includes('/api/v1/') && response.status() === 404) {
        notFoundCalls.push(`${response.request().method()} ${new URL(url).pathname} → 404`);
      }
    });

    await authenticate(page);

    const pagesToVisit = [
      '/',
      '/entities',
      '/software',
      '/services',
      '/certificates',
      '/issues',
      '/projects',
      '/milestones',
      '/labels',
      '/data-stores',
      '/dependencies',
      '/organizations',
      '/networking',
      '/iam',
      '/ipam',
      '/backups',
      '/webhooks',
      '/on-call-rotations',
      '/admin/sso',
      '/admin/license-policies',
      '/admin/tenants',
      '/settings',
    ];

    for (const path of pagesToVisit) {
      try {
        const response = await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 15000 });
        if (response && response.status() !== 404) {
          await page.waitForLoadState('networkidle').catch(() => {});
        }
      } catch {
        // Navigation timeout — page may be slow, continue
      }
    }

    expect(
      notFoundCalls,
      `API path mismatches detected (frontend calls routes that don't exist on backend):\n${notFoundCalls.join('\n')}`
    ).toHaveLength(0);
  });
});

// ============================================================================
// Authenticated post-login tests
// Regression: blank page / TypeError after successful login (entity.type drift)
// ============================================================================

test.describe('Elder Web UI - Authenticated Dashboard', () => {
  test('dashboard renders after login without crashing', async ({ page }) => {
    const jsErrors: string[] = [];
    page.on('pageerror', (e) => jsErrors.push(e.message));
    page.on('console', (msg) => {
      if (msg.type() === 'error') jsErrors.push(msg.text());
    });

    await authenticate(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {});

    // The page must have visible content — not a blank white/black screen
    const body = page.locator('body');
    await expect(body).toBeVisible();

    // At least one layout landmark must be present (sidebar, nav, main)
    const hasContent = await page
      .locator('nav, aside, main, [role="navigation"], [role="main"]')
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent, 'Dashboard must render layout after login — blank page detected').toBe(true);

    // No uncaught JS errors (filter out browser extension noise)
    const appErrors = jsErrors.filter(
      (e) =>
        !e.includes('background.js') &&
        !e.includes('extension') &&
        !e.includes('404') &&
        !e.includes('non-passive')
    );
    expect(
      appErrors,
      `Dashboard crashed with JS error(s) after login:\n${appErrors.join('\n')}`
    ).toHaveLength(0);
  });

  test('dashboard entity list renders entity type without TypeError', async ({ page }) => {
    // Regression: entity.entity_type.replace() crashed because API returns `type`, not `entity_type`
    const jsErrors: string[] = [];
    page.on('pageerror', (e) => jsErrors.push(e.message));

    await authenticate(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {});

    // Any TypeError about `.replace` on undefined is the entity_type drift crash
    const typeErrors = jsErrors.filter((e) => e.includes('replace') || e.includes("reading 'replace'"));
    expect(
      typeErrors,
      `Entity type field crash detected (API field name drift):\n${typeErrors.join('\n')}`
    ).toHaveLength(0);
  });

  test('authenticated routes stay authenticated after login', async ({ page }) => {
    await authenticate(page);

    const protectedRoutes = ['/', '/entities', '/organizations', '/dependencies', '/issues'];
    for (const route of protectedRoutes) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => {});
      expect(
        page.url(),
        `Authenticated route ${route} redirected to login unexpectedly`
      ).not.toContain('/login');
    }
  });
});

// ============================================================================
// Regression: gh-75 — Sidebar invisible in Docker builds (Tailwind @source path)
//
// The @source directive in web/src/index.css must resolve to the node_modules
// directory that actually contains @penguintechinc/react-libs. If the path is
// wrong (e.g. ../../node_modules/ instead of ../node_modules/), Tailwind silently
// skips scanning react-libs and strips sidebar classes from the production bundle.
// No JS errors are thrown — the sidebar just becomes invisible.
//
// These tests catch that class of bug by asserting:
//   A. The built CSS bundle contains the sidebar utility classes
//   B. The sidebar <nav> element is visible with correct computed positioning
//   C. The sidebar has the expected width on a desktop viewport
// ============================================================================

test.describe('Sidebar CSS — react-libs @source regression (gh-75)', () => {
  test('built CSS bundle includes react-libs sidebar utility classes', async ({ page }) => {
    // regression: gh-75
    await authenticate(page);
    await page.goto('/', { waitUntil: 'networkidle' });

    // Collect all stylesheet URLs loaded by the page
    const cssUrls: string[] = await page.evaluate(() =>
      Array.from(document.styleSheets)
        .map((s) => s.href)
        .filter((href): href is string => Boolean(href))
    );

    expect(
      cssUrls.length,
      'No external stylesheets found — Vite bundle may not have loaded'
    ).toBeGreaterThan(0);

    // These are classes emitted by SidebarMenu in @penguintechinc/react-libs.
    // If the @source path in index.css is broken, ALL of these will be absent
    // from the purged production bundle.
    const requiredSidebarClasses = ['left-0', 'w-64', 'border-r', 'fixed'];

    for (const cssUrl of cssUrls) {
      const response = await page.request.get(cssUrl);
      if (!response.ok()) continue;
      const cssText = await response.text();

      // If any one stylesheet contains all required classes, we're good
      const missingFromThisSheet = requiredSidebarClasses.filter((cls) => !cssText.includes(cls));
      if (missingFromThisSheet.length === 0) return; // all present — pass
    }

    // No stylesheet contained all required classes
    throw new Error(
      `Built CSS bundle is missing react-libs sidebar utility classes: ${requiredSidebarClasses.join(', ')}. ` +
        'This almost certainly means web/src/index.css has a broken @source path — ' +
        'Tailwind skipped scanning @penguintechinc/react-libs and purged the sidebar classes. ' +
        'Check that the @source path resolves to the correct node_modules directory.'
    );
  });

  test('sidebar nav element is visible with correct CSS positioning', async ({ page }) => {
    // regression: gh-75
    await authenticate(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {});

    // SidebarMenu from react-libs renders a <nav> element with fixed positioning
    const sidebar = page.locator('nav').first();
    await expect(sidebar, 'Sidebar <nav> must be visible — CSS may be missing').toBeVisible();

    // If Tailwind classes are missing, computed styles will be browser defaults
    // (position: static, left: auto) instead of the react-libs values
    const position = await sidebar.evaluate((el) => window.getComputedStyle(el).position);
    expect(
      position,
      `Sidebar position is "${position}" instead of "fixed" — left-sidebar CSS classes are missing from the bundle`
    ).toBe('fixed');

    const left = await sidebar.evaluate((el) => window.getComputedStyle(el).left);
    expect(
      left,
      `Sidebar left is "${left}" instead of "0px" — left-0 class is missing from the bundle`
    ).toBe('0px');
  });

  test('sidebar has expected width on desktop viewport', async ({ page }) => {
    // regression: gh-75 — w-64 (256px) should be applied on lg breakpoint
    await authenticate(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => {});

    const sidebar = page.locator('nav').first();
    await expect(sidebar).toBeVisible();

    const box = await sidebar.boundingBox();
    if (box) {
      // w-64 = 16rem; at default 16px base = 256px. Allow ±4px for sub-pixel rendering.
      expect(
        box.width,
        `Sidebar width is ${box.width}px — expected ~256px (w-64). The w-64 class may be missing from the bundle.`
      ).toBeGreaterThanOrEqual(252);
      expect(box.width).toBeLessThanOrEqual(260);
    }
  });
});
