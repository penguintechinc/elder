import { test as base, Page } from '@playwright/test';

/**
 * Custom fixtures for Elder E2E tests
 * Provides common test setup and utilities
 */

interface ConsoleMessages {
  errors: string[];
  warnings: string[];
}

export const test = base.extend<{
  consoleMessages: ConsoleMessages;
  pageWithErrorCapture: Page;
}>({
  consoleMessages: async ({}, use) => {
    const messages: ConsoleMessages = {
      errors: [],
      warnings: [],
    };
    await use(messages);
  },

  pageWithErrorCapture: async ({ page, consoleMessages }, use) => {
    // Capture console messages
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleMessages.errors.push(msg.text());
      } else if (msg.type() === 'warning') {
        consoleMessages.warnings.push(msg.text());
      }
    });

    // Capture uncaught exceptions
    page.on('pageerror', (error) => {
      consoleMessages.errors.push(`Uncaught exception: ${error.message}`);
    });

    // Capture request failures
    page.on('requestfailed', (request) => {
      consoleMessages.errors.push(`Request failed: ${request.url()}`);
    });

    await use(page);
  },
});

export { expect } from '@playwright/test';
