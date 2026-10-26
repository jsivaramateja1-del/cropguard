import { expect, test } from '@playwright/test'

/**
 * Live smoke test against a real deployment.
 *
 * Skipped unless `E2E_BASE_URL` is set, because it depends on a free-tier host that sleeps
 * and on data that changes. This is the only suite that can catch deployment-level faults
 * — a missing SPA rewrite, a CORS misconfiguration, an API contract that drifted from the
 * mocks — so run it before a demo:
 *
 *   E2E_BASE_URL=https://cropguard-blush.vercel.app npm run test:e2e:live
 */

const API = process.env.E2E_API_URL || 'https://cropguard-pzs9.onrender.com'
const configured = Boolean(process.env.E2E_BASE_URL)

test.describe('live deployment', () => {
  test.skip(!configured, 'set E2E_BASE_URL to run the live smoke test')

  // A sleeping free instance can take a minute to wake; give these room.
  test.setTimeout(120_000)

  test('the frontend serves the app', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: /check a leaf/i })).toBeVisible()
  })

  test('deep links work — the SPA rewrite is present', async ({ page }) => {
    // This is the check that found a missing rewrite on Vercel: a direct load of a nested
    // route returned nothing while the root worked.
    for (const [path, heading] of [
      ['/outbreaks', /outbreak map/i],
      ['/follow-up', /follow up/i],
      ['/expert', /expert review/i],
      ['/account', /sign in|create an account/i],
    ]) {
      const response = await page.goto(path, { waitUntil: 'domcontentloaded' })
      expect(response?.status(), `${path} should be 200`).toBe(200)
      await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible()
    }
  })

  test('the API reports itself healthy', async ({ request }) => {
    const response = await request.get(`${API}/health`)
    expect(response.status()).toBe(200)
    const body = await response.json()
    expect(body.status).toBe('ok')
    expect(body.checks.model.ok).toBe(true)
    expect(body.checks.database.ok).toBe(true)
    expect(body.checks.database.dialect).toBe('postgresql')
    expect(body.checks.model.classes).toBe(16)
    // The health payload must never carry credentials or filesystem paths.
    const raw = JSON.stringify(body)
    expect(raw).not.toContain('postgresql://')
    expect(raw).not.toContain('/opt/')
  })

  test('the model contract matches what the UI assumes', async ({ request }) => {
    const body = await (await request.get(`${API}/api/model`)).json()
    expect(body.output_dimensions).toBe(body.classes.length)
    expect(body.supported_crops).not.toContain('Wheat')
    expect(body.healthy_class_available.Rice).toBe(false)
  })

  test('the outage map is public and anonymised', async ({ request }) => {
    const response = await request.get(`${API}/api/outbreaks`)
    expect(response.status()).toBe(200)
    const body = await response.json()
    expect(body).toHaveProperty('points')
    expect(body).toHaveProperty('summary')
    const raw = JSON.stringify(body)
    for (const forbidden of ['farmer_name', 'owner_token', 'access_token', 'image_url']) {
      expect(raw, `public map leaks ${forbidden}`).not.toContain(forbidden)
    }
    // Coordinates must be rounded to about a kilometre.
    for (const point of body.points) {
      expect(Math.round(point.latitude * 100) / 100).toBe(point.latitude)
      expect(Math.round(point.longitude * 100) / 100).toBe(point.longitude)
    }
  })

  test('staff endpoints refuse anonymous callers', async ({ request }) => {
    expect((await request.get(`${API}/api/reports`)).status()).toBe(401)
    expect((await request.get(`${API}/api/stats`)).status()).toBe(401)
    expect((await request.get(`${API}/api/reports/1`)).status()).toBe(404)
    expect((await request.get(`${API}/api/expert-requests`)).status()).toBe(401)
  })

  test('CORS allows the deployed frontend origin', async ({ playwright }) => {
    const context = await playwright.request.newContext({
      extraHTTPHeaders: {
        Origin: process.env.E2E_BASE_URL,
        'Access-Control-Request-Method': 'POST',
      },
    })
    const response = await context.options(`${API}/api/analyze`)
    const allowed = response.headers()['access-control-allow-origin']
    expect(allowed, 'the API must allow the frontend origin').toBeTruthy()
    await context.dispose()
  })
})
