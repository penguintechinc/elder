import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL || 'admin@localhost'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123'

async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' })
  await page.getByLabel(/email/i).fill(ADMIN_EMAIL)
  await page.getByLabel(/password/i).fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: /sign in|log in/i }).click()
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15000 })
}

test.describe('Unified Issues UI', () => {
  test('issues list loads with the issue type filter visible', async ({ page }) => {
    await login(page)
    await page.goto('/issues', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Issues' })).toBeVisible()
    await expect(page.getByTestId('issue-type-filter')).toBeVisible()
  })

  test('create an issue using the combined assignee picker', async ({ page }) => {
    await login(page)
    await page.goto('/issues', { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: /create issue/i }).click()
    await expect(page.getByTestId('create-issue-form')).toBeVisible()

    await page.getByTestId('issue-title-input').fill(`E2E smoke issue ${Date.now()}`)
    await page.getByTestId('issue-type-select').selectOption('support')
    await expect(page.getByTestId('support-fields')).toBeVisible()

    await page.getByTestId('issue-organization-select').selectOption({ index: 1 })

    const assigneeInput = page.getByTestId('assignee-picker').locator('input')
    await assigneeInput.click()
    const firstOption = page.getByTestId('assignee-picker').locator('button').first()
    if (await firstOption.isVisible().catch(() => false)) {
      await firstOption.click()
    }

    await page.getByTestId('submit-issue-button').click()
    await expect(page.getByTestId('create-issue-form')).not.toBeVisible({ timeout: 10000 })
  })
})
