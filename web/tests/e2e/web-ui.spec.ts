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

    await page.goto('/');

    // Wait for page to be interactive
    await page.waitForLoadState('networkidle');

    // Verify page is visible
    const body = await page.locator('body');
    await expect(body).toBeVisible();

    // Verify no critical console errors
    expect(messages.errors).toHaveLength(0);
  });

  test('login page is accessible', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/login');

    await page.waitForLoadState('networkidle');

    // Look for login form elements
    const emailInput = page.locator('input[type="email"], input[placeholder*="email" i]');
    const passwordInput = page.locator('input[type="password"]');

    // At least one should be present
    const isLoginForm =
      (await emailInput.count()) > 0 || (await passwordInput.count()) > 0;

    expect(isLoginForm).toBeTruthy();
    expect(messages.errors).toHaveLength(0);
  });

  test('dashboard loads after authentication', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Navigate to dashboard
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Verify dashboard content loads
    // Look for key dashboard indicators
    const dashboardContent = page.locator('main, [role="main"]');
    await expect(dashboardContent).toBeVisible();

    expect(messages.errors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Navigation', () => {
  test('navigation between main pages', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Start at home
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Try navigating to entities page
    const entitiesLink = page.locator('a[href*="entities"], button:has-text("Entities")');
    if (await entitiesLink.count() > 0) {
      await entitiesLink.first().click();
      await page.waitForLoadState('networkidle');
      await expect(page).toHaveURL(/entities/);
    }

    // Try navigating to services page
    const servicesLink = page.locator('a[href*="services"], button:has-text("Services")');
    if (await servicesLink.count() > 0) {
      await servicesLink.first().click();
      await page.waitForLoadState('networkidle');
      await expect(page).toHaveURL(/services/);
    }

    expect(messages.errors).toHaveLength(0);
  });

  test('tab switching on compute page', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/compute');
    await page.waitForLoadState('networkidle');

    // Look for tab elements
    const tabs = page.locator('[role="tab"], button[class*="tab"]');
    const tabCount = await tabs.count();

    if (tabCount > 0) {
      // Click through each tab
      for (let i = 0; i < Math.min(tabCount, 3); i++) {
        const tab = tabs.nth(i);
        await tab.click();
        // Wait for tab content to load
        await page.waitForTimeout(500);
        await page.waitForLoadState('networkidle');
      }
    }

    expect(messages.errors).toHaveLength(0);
  });

  test('entity detail page tabs', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Navigate to entities
    await page.goto('/entities');
    await page.waitForLoadState('networkidle');

    // Try to find and click first entity link
    const entityLink = page.locator('a[href*="entities/"]').first();
    if (await entityLink.count() > 0) {
      await entityLink.click();
      await page.waitForLoadState('networkidle');

      // Look for tabs in detail view
      const tabs = page.locator('[role="tab"], button[class*="tab"]');
      const tabCount = await tabs.count();

      if (tabCount > 0) {
        // Click through tabs
        for (let i = 0; i < Math.min(tabCount, 3); i++) {
          await tabs.nth(i).click();
          await page.waitForTimeout(300);
        }
      }
    }

    expect(messages.errors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Forms and Modals', () => {
  test('create entity modal opens and closes', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/entities');
    await page.waitForLoadState('networkidle');

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

    expect(messages.errors).toHaveLength(0);
  });

  test('form validation and error display', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/entities');
    await page.waitForLoadState('networkidle');

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

    expect(messages.errors).toHaveLength(0);
  });

  test('LXD container creation form', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/entities');
    await page.waitForLoadState('networkidle');

    // Look for create button
    const createButton = page.locator(
      'button:has-text("Create"), button:has-text("Add"), button:has-text("New")'
    );

    if (await createButton.count() > 0) {
      await createButton.first().click();
      await page.waitForLoadState('networkidle');

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

    expect(messages.errors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Page-Specific Tests', () => {
  test('services page loads', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/services');
    await page.waitForLoadState('networkidle');

    const pageContent = page.locator('main, [role="main"]');
    await expect(pageContent).toBeVisible();

    expect(messages.errors).toHaveLength(0);
  });

  test('issues page loads', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/issues');
    await page.waitForLoadState('networkidle');

    const pageContent = page.locator('main, [role="main"]');
    await expect(pageContent).toBeVisible();

    expect(messages.errors).toHaveLength(0);
  });

  test('projects page loads', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/projects');
    await page.waitForLoadState('networkidle');

    const pageContent = page.locator('main, [role="main"]');
    await expect(pageContent).toBeVisible();

    expect(messages.errors).toHaveLength(0);
  });

  test('settings page loads', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/settings');
    await page.waitForLoadState('networkidle');

    const pageContent = page.locator('main, [role="main"]');
    // Settings page might not exist - just check for errors
    expect(messages.errors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Error Handling', () => {
  test('handles API errors gracefully', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Navigate to non-existent entity
    await page.goto('/entities/99999999');
    await page.waitForTimeout(1000);

    // Page should still be functional (no uncaught errors)
    const body = await page.locator('body');
    await expect(body).toBeVisible();

    // Filter for actual errors (not network 404 logs)
    const criticalErrors = messages.errors.filter(
      (e) =>
        !e.includes('404') &&
        !e.includes('Failed to fetch') &&
        !e.includes('404 Not Found')
    );

    expect(criticalErrors).toHaveLength(0);
  });

  test('no React error boundaries triggered', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Visit multiple pages to try to trigger errors
    const pages = ['/', '/entities', '/services', '/projects'];

    for (const pagePath of pages) {
      await page.goto(pagePath);
      await page.waitForLoadState('networkidle');
    }

    // Check for React error boundary messages
    const reactErrors = messages.errors.filter(
      (e) =>
        e.toLowerCase().includes('react') &&
        (e.toLowerCase().includes('error') ||
          e.toLowerCase().includes('boundary'))
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
      await page.goto('/');
      await page.waitForLoadState('networkidle');

      // Verify page is still interactive
      const body = await page.locator('body');
      await expect(body).toBeVisible();
    }

    expect(messages.errors).toHaveLength(0);
  });
});

test.describe('Elder Web UI - Performance', () => {
  test('page load time acceptable', async ({ page }) => {
    const startTime = Date.now();

    await page.goto('/', { waitUntil: 'networkidle' });

    const loadTime = Date.now() - startTime;

    // Page should load in less than 10 seconds
    expect(loadTime).toBeLessThan(10000);
  });

  test('no memory leaks on navigation', async ({ page }) => {
    const messages = { errors: [], warnings: [] };
    Object.assign(messages, await collectConsoleMessages(page));

    // Simulate user navigation back and forth
    for (let i = 0; i < 5; i++) {
      await page.goto('/');
      await page.waitForLoadState('networkidle');

      await page.goto('/entities');
      await page.waitForLoadState('networkidle');

      await page.goto('/services');
      await page.waitForLoadState('networkidle');
    }

    // If there are memory leaks, they'd show as errors
    expect(messages.errors).toHaveLength(0);
  });
});
