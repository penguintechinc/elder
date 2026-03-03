# Create Modal Smoke Tests Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Playwright smoke tests that detect blank Create modals and console errors for every page with a Create/Add button.

**Architecture:** Data-driven test matrix with one `test.describe` block using `for...of` loop to generate a named test per page. Each test clicks the Create button, asserts the modal renders form fields, and checks for console errors.

**Tech Stack:** Playwright, TypeScript, existing `web/tests/e2e/web-ui.spec.ts`

---

### Task 1: Add Create Modal Content test block

**Files:**
- Modify: `web/tests/e2e/web-ui.spec.ts` (append after Performance section, ~line 550)

**Step 1: Write the test block**

Add the following after the closing `});` of the Performance describe block (line 550):

```typescript
/**
 * Create Modal Content Tests
 *
 * These tests catch the "blank modal" bug class: a Create/Add button exists,
 * the modal container opens, but no form fields render inside it.
 * This happened when /api/v1/status returned 404 and blocked FormModalBuilder.
 */

// Pages with Create/Add buttons and their expected button text
const PAGES_WITH_CREATE_MODAL = [
  { route: '/entities', name: 'Entities', buttonText: 'Create' },
  { route: '/software', name: 'Software', buttonText: 'Add Software' },
  { route: '/services', name: 'Services', buttonText: 'Create' },
  { route: '/certificates', name: 'Certificates', buttonText: 'Create' },
  { route: '/issues', name: 'Issues', buttonText: 'Create' },
  { route: '/projects', name: 'Projects', buttonText: 'Create' },
  { route: '/milestones', name: 'Milestones', buttonText: 'Create' },
  { route: '/labels', name: 'Labels', buttonText: 'Create' },
  { route: '/data-stores', name: 'Data Stores', buttonText: 'Create' },
  { route: '/dependencies', name: 'Dependencies', buttonText: 'Create' },
  { route: '/organizations', name: 'Organizations', buttonText: 'Create' },
  { route: '/keys', name: 'Keys', buttonText: 'Create' },
  { route: '/secrets', name: 'Secrets', buttonText: 'Add Provider' },
  { route: '/webhooks', name: 'Webhooks', buttonText: 'Create' },
  { route: '/on-call-rotations', name: 'On-Call Rotations', buttonText: 'Create' },
  { route: '/networking', name: 'Networking', buttonText: 'Create' },
  { route: '/iam', name: 'IAM', buttonText: 'Create' },
  { route: '/ipam', name: 'IPAM', buttonText: 'Create' },
  { route: '/backups', name: 'Backups', buttonText: 'Create' },
  { route: '/admin/sso', name: 'SSO Configuration', buttonText: 'Add' },
  { route: '/admin/license-policies', name: 'License Policies', buttonText: 'Create' },
  { route: '/admin/tenants', name: 'Tenants', buttonText: 'Create' },
];

test.describe('Elder Web UI - Create Modal Content', () => {
  for (const pageConfig of PAGES_WITH_CREATE_MODAL) {
    test(`${pageConfig.name} - create modal renders content without errors`, async ({ page }) => {
      // Collect console errors
      const errors: string[] = [];
      page.on('console', (msg) => {
        if (msg.type() === 'error') {
          errors.push(msg.text());
        }
      });
      page.on('pageerror', (error) => {
        errors.push(`Uncaught: ${error.message}`);
      });

      // Navigate to the page
      const response = await page.goto(pageConfig.route, { waitUntil: 'domcontentloaded' }).catch(() => null);

      // Skip if page doesn't exist
      if (!response || response.status() === 404) {
        test.skip(true, `${pageConfig.name} page not available (404)`);
        return;
      }

      // Wait for page to settle
      await page.waitForLoadState('networkidle').catch(() => {});

      // Record error count before clicking (baseline)
      const baselineErrorCount = errors.length;

      // Find the Create/Add button
      const createButton = page.locator(
        `button:has-text("${pageConfig.buttonText}")`
      ).first();

      // Skip if no create button found
      if ((await createButton.count()) === 0) {
        test.skip(true, `${pageConfig.name} has no "${pageConfig.buttonText}" button`);
        return;
      }

      // Click the Create button
      await createButton.click();

      // Wait for modal to appear
      const modal = page.locator('[role="dialog"], [class*="modal-overlay"], [class*="Modal"]');
      await expect(modal.first()).toBeVisible({ timeout: 10000 });

      // CRITICAL: Assert modal is NOT blank — must have form fields
      const formFields = modal.first().locator('input, select, textarea, [role="combobox"], [role="listbox"]');
      const fieldCount = await formFields.count();

      // Hard fail: modal opened but has no form fields (blank modal bug)
      expect(fieldCount, `${pageConfig.name} modal is BLANK — opened but contains 0 form fields`).toBeGreaterThan(0);

      // Assert modal has a submit/action button
      const actionButton = modal.first().locator(
        'button[type="submit"], button:has-text("Create"), button:has-text("Add"), button:has-text("Save")'
      );
      expect(
        await actionButton.count(),
        `${pageConfig.name} modal has no submit/action button`
      ).toBeGreaterThan(0);

      // Check for new console errors that appeared AFTER clicking Create
      const newErrors = errors.slice(baselineErrorCount).filter(
        (e) =>
          !e.includes('404') &&
          !e.includes('net::ERR') &&
          !e.includes('Failed to fetch')
      );
      expect(
        newErrors,
        `${pageConfig.name} modal triggered console errors: ${newErrors.join(', ')}`
      ).toHaveLength(0);

      // Close the modal
      const closeButton = page.locator(
        'button[aria-label="Close"], button:has-text("Cancel"), button:has-text("Close")'
      ).first();
      if ((await closeButton.count()) > 0) {
        await closeButton.click().catch(() => {});
      } else {
        await page.keyboard.press('Escape');
      }
    });
  }
});
```

**Step 2: Run tests locally to verify syntax**

Run: `cd /home/penguin/code/elder/web && PLAYWRIGHT_WEBSERVER_DISABLED=1 PLAYWRIGHT_BASE_URL=https://dal2.penguintech.io npx playwright test --grep "Create Modal Content" --list`
Expected: Lists 22 test cases, one per page

**Step 3: Run tests against beta cluster**

Run: `cd /home/penguin/code/elder/web && PLAYWRIGHT_WEBSERVER_DISABLED=1 PLAYWRIGHT_BASE_URL=https://dal2.penguintech.io npx playwright test --grep "Create Modal Content" --reporter=list`
Expected: Tests pass for pages that exist. Skips for pages not deployed. Hard fails if any modal is blank.

**Step 4: Fix any test failures**

If any tests fail due to incorrect button text or selector issues, update the `PAGES_WITH_CREATE_MODAL` array entries for those pages.

**Step 5: Commit**

```bash
git add web/tests/e2e/web-ui.spec.ts
git commit -m "test: add create modal content smoke tests for all pages

Catches the blank modal bug class where a Create button opens a modal
but no form fields render inside it (e.g. when API calls block rendering).
Tests all 22 pages with Create/Add buttons."
```

---

### Task 2: Remove redundant old modal tests

**Files:**
- Modify: `web/tests/e2e/web-ui.spec.ts` (~lines 197-324)

**Step 1: Review overlap**

The old "Forms and Modals" describe block (lines 196-325) has 3 tests:
- `create entity modal opens and closes` — now covered by Entities entry in new matrix
- `form validation and error display` — generic, keep if unique value
- `LXD container creation form` — specific, keep as-is

**Step 2: Remove the `create entity modal opens and closes` test**

Delete lines 197-237 (the first test in the "Forms and Modals" block) since it's now covered more thoroughly by the new matrix test.

**Step 3: Run full test suite**

Run: `cd /home/penguin/code/elder/web && PLAYWRIGHT_WEBSERVER_DISABLED=1 PLAYWRIGHT_BASE_URL=https://dal2.penguintech.io npx playwright test --reporter=list`
Expected: All tests pass (original 17 + new 22 = ~39 total, minus 1 removed = ~38)

**Step 4: Commit**

```bash
git add web/tests/e2e/web-ui.spec.ts
git commit -m "refactor: remove redundant modal open/close test covered by matrix"
```
