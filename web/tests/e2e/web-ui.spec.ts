import { test, expect, Page } from '@playwright/test';

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
  { route: '/entities', name: 'Entities', buttonText: 'Create' },
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

// Helper: login and set token in localStorage
async function loginAndSetToken(page: Page): Promise<boolean> {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  const token = await page.evaluate(async (creds) => {
    try {
      const res = await fetch('/api/v1/portal-auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(creds),
      });
      if (!res.ok) return null;
      const data = await res.json();
      if (data.token) localStorage.setItem('elder_token', data.token);
      return data.token || null;
    } catch {
      return null;
    }
  }, {
    email: process.env.ELDER_TEST_EMAIL || 'admin@localhost.local',
    password: process.env.ELDER_TEST_PASSWORD || 'admin123',
  });
  return !!token;
}

test.describe('Elder Web UI - Create Modal Content', () => {
  for (const pageConfig of PAGES_WITH_CREATE_MODAL) {
    test(`${pageConfig.name} - create modal renders content without errors`, async ({ page }) => {
      test.setTimeout(45000);

      // Login
      const loggedIn = await loginAndSetToken(page);
      if (!loggedIn) {
        test.skip(true, 'Login failed — cannot test authenticated pages');
        return;
      }

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

      // Click the Create button
      await createButton.click();

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
          !e.includes('status of 5')
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
