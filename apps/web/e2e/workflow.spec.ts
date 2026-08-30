import { test, expect } from '@playwright/test'

test.describe('Persona Studio — Complete E2E Workflow', () => {

  test('dashboard loads', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('text=PERSONA STUDIO')).toBeVisible()
    await expect(page.locator('text=MODELS')).toBeVisible()
  })

  test('create model form loads', async ({ page }) => {
    await page.goto('/models/create')
    await expect(page.locator('text=CREATE MODEL')).toBeVisible()
    await expect(page.locator('text=BUILD MODEL')).toBeVisible()
  })

  test('complete workflow: create → persona → shoot → pack', async ({ page }) => {
    // 1. Navigate to create model
    await page.goto('/models/create')

    // 2. Fill in the form
    await page.fill('input[placeholder="e.g. Ava"]', 'Playwright Test Model')
    await page.fill('input[type="number"]', '25')

    // 3. Submit
    await page.click('button:has-text("BUILD MODEL")')

    // 4. Should navigate to persona page
    await page.waitForURL(/\/personas\//, { timeout: 10000 })
    await expect(page.locator('text=Playwright Test Model')).toBeVisible()

    // 5. Verify overview tab
    await expect(page.locator('text=Overview')).toBeVisible()

    // 6. Check identity tab
    await page.click('button:has-text("Identity")')
    await expect(page.locator('text=Identities')).toBeVisible()

    // 7. Create a shoot
    await page.click('button:has-text("Shoots")')
    await page.click('button:has-text("CREATE SHOOT")')
    await page.waitForTimeout(1000) // wait for generation

    // 8. Create a content pack
    await page.click('button:has-text("Content")')
    await page.click('button:has-text("GENERATE PACK")')
    await page.waitForTimeout(1000)

    // 9. Check analytics
    await page.click('button:has-text("Analytics")')
    await page.click('button:has-text("Generate Analytics")')
    await page.waitForTimeout(1000)
    await expect(page.locator('text=90 days')).toBeVisible()

    // 10. Check forecast
    await page.click('button:has-text("Revenue")')
    await page.click('button:has-text("Generate Forecast")')
    await page.waitForTimeout(1000)

    // 11. Check schedule
    await page.click('button:has-text("Calendar")')
    await page.click('button:has-text("Auto-Schedule")')
    await page.waitForTimeout(1000)

    // 12. Check workflows
    await page.click('button:has-text("Workflows")')
    await expect(page.locator('text=Workflows')).toBeVisible()
  })
})
