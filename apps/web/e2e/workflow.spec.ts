import { test, expect } from '@playwright/test'

test.describe('Persona Studio — Complete E2E Workflow', () => {

  test('dashboard loads', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('aside')).toContainText('PersonaStudio')
    await expect(page.getByRole('main')).toContainText('Overview')
  })

  test('create model form loads', async ({ page }) => {
    await page.goto('/models/create')
    await expect(page.getByRole('main')).toContainText(/Create model/i)
    await expect(page.getByRole('button', { name: /Build model/i })).toBeVisible()
  })

  test('provider readiness exposes real blockers', async ({ request }) => {
    const response = await request.get('http://127.0.0.1:8000/api/v1/system/providers')
    expect(response.ok()).toBeTruthy()
    const body = await response.json()
    expect(body.required).toContain('moderation')
    expect(body.capabilities.map((item: { capability: string }) => item.capability)).toContain('moderation')
    for (const item of body.capabilities) {
      expect(['green', 'yellow', 'red']).toContain(item.status)
    }
  })

  test('complete workflow: create → persona → shoot → pack', async ({ page }) => {
    const modelName = `Playwright Test Model ${Date.now()}`

    // 1. Navigate to create model
    await page.goto('/models/create')

    // 2. Fill in the form
    await page.fill('input[placeholder="e.g. Ava"]', modelName)
    await page.fill('input[type="number"]', '25')

    // 3. Submit
    await page.getByRole('button', { name: /Build model/i }).click()

    // 4. Should navigate to persona page
    await page.waitForURL(/\/personas\//, { timeout: 10000 })
    await expect(page.getByRole('heading', { name: modelName })).toBeVisible()

    // 5. Verify overview tab
    await expect(page.getByRole('main').getByRole('button', { name: 'Overview', exact: true })).toBeVisible()

    // 6. Check identity tab
    await page.click('button:has-text("Identity")')
    await expect(page.getByRole('heading', { name: /Identities/ })).toBeVisible()

    // 7. Create a shoot
    await page.getByRole('button', { name: 'Shoots', exact: true }).click()
    await page.getByRole('button', { name: /Create Shoot/i }).click()
    await expect(page.getByRole('heading', { name: /Shoots/ })).toBeVisible()

    // 8. Create a content pack
    await page.getByRole('button', { name: 'Content', exact: true }).click()
    await page.getByRole('button', { name: /Generate Pack/i }).click()
    await expect(page.getByRole('heading', { name: /Content Packs/ })).toBeVisible()

    // 9. Check analytics
    await page.getByRole('main').getByRole('button', { name: 'Analytics', exact: true }).click()
    await page.getByRole('button', { name: 'Generate', exact: true }).click()
    await expect(page.getByRole('heading', { name: /Analytics/ })).toBeVisible()

    // 10. Check forecast
    await page.getByRole('main').getByRole('button', { name: 'Revenue', exact: true }).click()
    await page.getByRole('button', { name: 'Generate', exact: true }).click()

    // 11. Check schedule
    await page.getByRole('main').getByRole('button', { name: 'Calendar', exact: true }).click()
    await page.getByRole('button', { name: 'Auto-Schedule', exact: true }).click()

    // 12. Check workflows
    await page.getByRole('main').getByRole('button', { name: 'Workflows', exact: true }).click()
    await expect(page.getByRole('main')).toContainText('Workflows')
  })
})
