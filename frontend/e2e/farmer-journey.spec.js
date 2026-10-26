import { expect, test } from '@playwright/test'
import {
  analyzeNeedsReview,
  analyzeNotALeaf,
  analyzeVerified,
  mockApi,
  reportForFollowUp,
} from './mocks.js'

/**
 * The farmer journey, against a mocked API.
 *
 * These tests answer one question: given a particular API response, does the interface
 * show the right thing and avoid saying anything it cannot support? They do not exercise
 * the real model — the live-smoke spec does that.
 */

/**
 * Fill the fields the form genuinely requires. The app validates these before submitting
 * and names what is missing — which is why the earlier "click Analyse" test produced a
 * validation message rather than a diagnosis.
 */
async function fillRequiredFields(page, { name = 'Ravi Kumar' } = {}) {
  await page.getByLabel(/your name/i).fill(name)
  await page.getByText(/enter coordinates manually/i).click()
  await page.getByLabel(/^latitude$/i).fill('16.5062')
  await page.getByLabel(/^longitude$/i).fill('80.6480')
}

/**
 * Submit the diagnosis form.
 *
 * Uses the form's own submit event rather than a synthetic click: on the mobile layout the
 * sticky action bar overlaps the in-form button while Playwright scrolls it into view, so
 * a click is intermittently swallowed by hit-testing. The button's presence and enabled
 * state are asserted separately, so this does not hide a missing control.
 */
async function submitAnalysis(page) {
  await page.locator('form').first().evaluate((form) => form.requestSubmit())
}

/** Upload, fill and submit, without assuming a result arrives. */
async function submitLeaf(page) {
  await page.locator('input[type="file"]').first().setInputFiles('e2e/fixtures/leaf.jpg')
  await fillRequiredFields(page)
  await submitAnalysis(page)
}

async function analyseLeaf(page) {
  await submitLeaf(page)
  // Wait for the result view itself before asserting on anything inside it: the accordion
  // and confidence meter mount after the response, and asserting too early is what made
  // this test flaky.
  await expect(page.getByRole('button', { name: /check another leaf/i }).first()).toBeVisible()
}

test.describe('diagnosis page', () => {
  test('loads and shows the form', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    await expect(page.getByRole('heading', { name: /check a leaf/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /analyse|analyze/i })).toBeVisible()
  })

  test('warns that Wheat has no trained classes', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    // The crop coverage note is derived from the model contract, not hard-coded.
    await expect(page.getByText(/wheat has no trained classes/i)).toBeVisible()
  })

  test('refuses to submit without a photo and names the gap', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    const submit = page.getByRole('button', { name: /analyse|analyze/i }).first()
    await expect(submit).toBeEnabled()
    await submitAnalysis(page)

    // The form names what is missing rather than silently doing nothing — an earlier
    // version was simply disabled, which looked broken.
    // The message appears in the form and again in the mobile action bar; one copy is
    // off-screen, so assert on content rather than visibility.
    await expect(page.locator('body')).toContainText(/still needed/i)
    await expect(page.locator('body')).toContainText(/your name, your location/i)
  })

  test('rejects a non-image file before uploading', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    await page.locator('input[type="file"]').first().setInputFiles('e2e/fixtures/notes.txt')
    await expect(page.getByText(/jpeg|png|webp|not an image|unsupported/i).first()).toBeVisible()
  })

  test('shows a verified diagnosis with its advisory', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    await analyseLeaf(page)

    // The result section, not the crop card (which also mentions "late blight").
    await expect(page.getByText(/potential diagnosis/i).first()).toBeVisible()
    await expect(page.getByText(/AI verified/i).first()).toBeVisible()
    // Advisory content is present for a matching crop, inside a collapsed accordion —
    // expanding it is part of the journey, so the test does it.
    const cultural = page.getByRole('button', { name: /cultural control/i }).first()
    await expect(cultural).toBeVisible()
    // Opened with the keyboard: on the mobile layout the sticky action bar overlaps the
    // trigger, so a pointer click is intermittently swallowed by hit-testing. Keyboard
    // activation is a real path and is not affected by overlays.
    await cultural.focus()
    await page.keyboard.press('Enter')
    // Generous timeout: the accordion animates, and under parallel workers the render can
    // take longer than the default 7s.
    await expect(page.getByText(/remove and destroy infected plants/i)).toBeVisible({
      timeout: 20_000,
    })
    await expect(page.getByText(/93\s*%/)).toBeVisible()
  })

  test('routes a low-confidence result to review and explains why', async ({ page }) => {
    await mockApi(page, { analyze: analyzeNeedsReview })
    await page.goto('/')

    await analyseLeaf(page)

    await expect(page.getByText(/expert review pending/i).first()).toBeVisible()
    await expect(page.getByText(/below the 0\.75 threshold/i)).toBeVisible()
    // Still a "potential diagnosis", never a certainty.
    await expect(page.getByText(/potential diagnosis/i).first()).toBeVisible()
  })

  test('flags a photo that does not look like a leaf', async ({ page }) => {
    await mockApi(page, { analyze: analyzeNotALeaf })
    await page.goto('/')

    await analyseLeaf(page)

    await expect(page.getByText(/does not look like a crop leaf/i)).toBeVisible()
  })

  test('offers the access code once, and it can be copied', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await mockApi(page)
    await page.goto('/')

    await analyseLeaf(page)

    await page.getByRole('button', { name: /save report number|report number/i }).first().click()

    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('CG-00042')).toBeVisible()
    await expect(dialog.getByText(/shown once/i)).toBeVisible()
    await expect(dialog.getByText('test-access-token-abc123')).toBeVisible()
  })

  test('surfaces an API failure instead of pretending it worked', async ({ page }) => {
    await mockApi(page, { analyze: new Error('The model is not available.') })
    await page.goto('/')

    // No result view is expected here, so do not wait for one.
    await submitLeaf(page)

    await expect(page.getByText(/model is not available|could not|failed/i).first()).toBeVisible()
  })
})

test.describe('navigation', () => {
  test('reaches every page from the header', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    for (const [label, href, heading] of [
      ['Outbreaks', '/outbreaks', /outbreak map/i],
      ['Follow up', '/follow-up', /follow up/i],
      ['Expert', '/expert', /expert review/i],
      ['Account', '/account', /sign in|create an account/i],
    ]) {
      const link = page.getByRole('link', { name: label }).first()
      await expect(link).toBeVisible()
      await expect(link).toHaveAttribute('href', href)

      await page.goto(href)
      await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible()
    }
  })

  test('an unknown route shows a 404 rather than a blank page', async ({ page }) => {
    await mockApi(page)
    await page.goto('/no-such-page')
    await expect(page.getByText(/not found|404/i).first()).toBeVisible()
  })
})

test.describe('outbreak map', () => {
  test('plots the points and lists hotspots', async ({ page }) => {
    await mockApi(page)
    await page.goto('/outbreaks')

    await expect(page.getByRole('heading', { name: /outbreak map/i })).toBeVisible()
    await expect(page.getByText(/4 reports/)).toBeVisible()
    await expect(page.getByText(/2 locations/)).toBeVisible()
    await expect(page.getByText('Kelambakkam')).toBeVisible()
    // The Leaflet canvas actually rendered.
    await expect(page.locator('.leaflet-container')).toBeVisible()
  })

  test('filters by crop', async ({ page }) => {
    await mockApi(page)
    await page.goto('/outbreaks')

    await page.getByRole('combobox').first().selectOption('Rice')
    await expect(page.getByText(/4 reports/)).toBeVisible()
  })
})

test.describe('follow-up page', () => {
  test('requires both the reference and the access code', async ({ page }) => {
    await mockApi(page)
    await page.goto('/follow-up')

    const open = page.getByRole('button', { name: /open report/i })
    await expect(open).toBeDisabled()

    await page.getByLabel(/report reference/i).fill('CG-00042')
    await expect(open).toBeDisabled()

    await page.getByLabel(/access code/i).fill('some-token')
    await expect(open).toBeEnabled()
  })

  test('shows the timeline including the expert decision', async ({ page }) => {
    await mockApi(page)
    await page.goto('/follow-up')

    await page.getByLabel(/report reference/i).fill('CG-00042')
    await page.getByLabel(/access code/i).fill('test-access-token-abc123')
    await page.getByRole('button', { name: /open report/i }).click()

    await expect(page.getByText(/reviewed by dr test expert/i)).toBeVisible()
    await expect(page.getByText(/follow-up: worsening/i)).toBeVisible()
    await expect(page.getByText(/an agricultural expert has reviewed this report/i)).toBeVisible()
  })

  test('says plainly when nobody has reviewed the report yet', async ({ page }) => {
    await mockApi(page, {
      followUpReport: { ...analyzeVerified.report, follow_ups: [], audit_events: [] },
    })
    await page.goto('/follow-up')

    await page.getByLabel(/report reference/i).fill('CG-00042')
    await page.getByLabel(/access code/i).fill('token')
    await page.getByRole('button', { name: /open report/i }).click()

    await expect(page.getByText(/no expert has reviewed this yet/i)).toBeVisible()
    await expect(page.getByText(/does not promise a response time/i)).toBeVisible()
  })

  test('reports a wrong access code without leaking whether the report exists', async ({ page }) => {
    await mockApi(page, { followUpReport: 'not-found' })
    await page.goto('/follow-up')

    await page.getByLabel(/report reference/i).fill('CG-99999')
    await page.getByLabel(/access code/i).fill('wrong')
    await page.getByRole('button', { name: /open report/i }).click()

    await expect(page.getByText(/could not open that report/i)).toBeVisible()
    await expect(page.getByText(/no report with that reference/i)).toBeVisible()
  })
})

test.describe('expert page', () => {
  test('offers both sign-in paths', async ({ page }) => {
    await mockApi(page)
    await page.goto('/expert')

    await expect(page.getByLabel(/email/i)).toBeVisible()
    await expect(page.getByLabel(/password/i)).toBeVisible()
    // The staff-token fallback is still reachable.
    await expect(page.getByRole('button', { name: /staff token/i })).toBeVisible()
  })

  test('hides the admin approval queue from non-admins', async ({ page }) => {
    // The mock returns 403 for expert-request listing, so the section must not render.
    await mockApi(page)
    await page.goto('/expert')
    await expect(page.getByText(/expert access requests/i)).toHaveCount(0)
  })
})

test.describe('theme and disease grid', () => {
  test('dark mode can be turned on and is remembered', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    // Default follows the device; switch explicitly.
    await page.getByRole('button', { name: /dark theme/i }).click()
    await expect(page.locator('html')).toHaveClass(/dark/)
    expect(await page.evaluate(() => localStorage.getItem('cropguard.theme'))).toBe('dark')

    // And it survives a reload, applied before React mounts.
    await page.reload()
    await expect(page.locator('html')).toHaveClass(/dark/)

    await page.getByRole('button', { name: /light theme/i }).click()
    await expect(page.locator('html')).not.toHaveClass(/dark/)
  })

  test('the home page shows a disease preview and a ticker', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    await expect(page.getByRole('heading', { name: /what cropguard can recognise/i })).toBeVisible()
    // Only screenable conditions appear in the home preview.
    await expect(page.getByRole('button', { name: /late blight on tomato/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /yellow rust on wheat/i })).toHaveCount(0)

    // The ticker carries advisory facts, not invented text.
    await expect(page.getByLabel(/cropguard facts/i)).toBeVisible()
    await expect(page.getByText(/cool, wet weather/i).first()).toBeVisible()
  })

  test('the disease page filters and marks model coverage', async ({ page }) => {
    await mockApi(page)
    await page.goto('/diseases')

    await expect(page.getByRole('heading', { name: /what cropguard knows/i })).toBeVisible()
    await expect(page.getByText(/5 conditions/i)).toBeVisible()
    // Four of the five mock entries are screenable; the count is derived, not guessed.
    await expect(page.getByText(/4 screenable/i)).toBeVisible()

    // Wheat is in the knowledge base but not in the model, and says so.
    await expect(page.getByRole('button', { name: /yellow rust on wheat/i })).toBeVisible()
    await expect(page.getByText(/advisory only/i).first()).toBeVisible()

    // Filtering by crop narrows the grid.
    await page.getByLabel(/filter by crop/i).selectOption('Rice')
    await expect(page.getByRole('button', { name: /brown spot on rice/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /late blight on tomato/i })).toHaveCount(0)

    // And a search matches symptoms, not just names.
    await page.getByLabel(/filter by crop/i).selectOption('')
    await page.getByLabel(/search diseases/i).fill('mould')
    await expect(page.getByRole('button', { name: /late blight on tomato/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /early blight on tomato/i })).toHaveCount(0)
  })

  test('a disease card opens its details', async ({ page }) => {
    await mockApi(page)
    await page.goto('/diseases')

    await page.getByRole('button', { name: /late blight on tomato/i }).click()
    await expect(page.getByText(/the most destructive disease/i)).toBeVisible()
    await expect(page.getByRole('link', { name: /check a leaf/i })).toBeVisible()
  })
})

test.describe('password reset', () => {
  test('the account page offers a reset link and confirms without revealing accounts', async ({
    page,
  }) => {
    await mockApi(page)
    await page.goto('/account')

    await page.getByRole('button', { name: /forgot your password/i }).click()
    await page.getByLabel(/email for the reset link/i).fill('someone@example.com')
    await page.getByRole('button', { name: /send reset link/i }).click()

    // Same wording whether or not the address exists.
    await expect(page.getByText(/check your inbox/i)).toBeVisible()
    await expect(page.getByText(/if that address has an account/i)).toBeVisible()
  })

  test('a link without a token explains itself instead of showing a form', async ({ page }) => {
    await mockApi(page)
    await page.goto('/reset-password')

    await expect(page.getByText(/missing its token/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /set new password/i })).toHaveCount(0)
  })

  test('mismatched passwords are caught before submitting', async ({ page }) => {
    await mockApi(page)
    await page.goto('/reset-password?token=good-token-value')

    await page.getByLabel(/^new password$/i).fill('a-long-password-1')
    await page.getByLabel(/confirm password/i).fill('a-different-one-2')

    await expect(page.getByText(/do not match/i).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /set new password/i })).toBeDisabled()
  })

  test('a valid token sets the password and signs the user out', async ({ page }) => {
    await mockApi(page)
    await page.goto('/reset-password?token=good-token-value')

    await page.getByLabel(/^new password$/i).fill('a-long-password-1')
    await page.getByLabel(/confirm password/i).fill('a-long-password-1')
    await page.getByRole('button', { name: /set new password/i }).click()

    await expect(page.getByRole('heading', { name: /password changed/i })).toBeVisible()
    await expect(page.getByText(/signed out/i)).toBeVisible()
    await expect(page.getByRole('link', { name: /^sign in$/i })).toBeVisible()
  })

  test('an expired token is reported clearly', async ({ page }) => {
    await mockApi(page)
    await page.goto('/reset-password?token=expired-token')

    await page.getByLabel(/^new password$/i).fill('a-long-password-1')
    await page.getByLabel(/confirm password/i).fill('a-long-password-1')
    await page.getByRole('button', { name: /set new password/i }).click()

    await expect(page.getByText(/invalid or has expired/i)).toBeVisible()
    await expect(page.getByText(/request a new one/i).first()).toBeVisible()
  })
})

test.describe('stored photos', () => {
  test('the follow-up page shows the leaf and the follow-up photo', async ({ page }) => {
    await mockApi(page, {
      followUpReport: {
        ...reportForFollowUp,
        follow_ups: [
          {
            id: 1,
            report_id: 42,
            condition_status: 'Worsening',
            farmer_notes: 'Spreading to the next row.',
            followup_image_url: '/api/images/followup1',
            created_at: '2026-10-26T18:00:00+00:00',
          },
        ],
      },
    })
    await page.goto('/follow-up')

    await page.getByLabel(/report reference/i).fill('CG-00042')
    await page.getByLabel(/access code/i).fill('token')
    await page.getByRole('button', { name: /open report/i }).click()

    // The photo that was analysed, plus the one attached to the follow-up.
    const images = page.locator('img[src*="/api/images/"]')
    await expect(images).toHaveCount(2)
    await expect(images.first()).toHaveAttribute('src', /\/api\/images\/abc123$/)
  })

  test('a missing photo degrades to a placeholder, not a broken image', async ({ page }) => {
    await mockApi(page, {
      followUpReport: { ...reportForFollowUp, image_url: null, follow_ups: [], audit_events: [] },
    })
    await page.goto('/follow-up')

    await page.getByLabel(/report reference/i).fill('CG-00042')
    await page.getByLabel(/access code/i).fill('token')
    await page.getByRole('button', { name: /open report/i }).click()

    await expect(page.getByRole('img', { name: /no photo available/i })).toBeVisible()
  })

  test('an expert sees the photo of the leaf they are judging', async ({ page }) => {
    await mockApi(page)
    await page.goto('/expert')

    // Sign in with the staff token, which needs no account round-trip.
    await page.getByRole('button', { name: /staff token/i }).click()
    await page.getByLabel(/staff token/i).fill('test-staff-token')
    await page.getByRole('button', { name: /sign in with token/i }).click()

    await expect(page.getByText('CG-00042')).toBeVisible()
    const thumb = page.locator('img[src*="/api/images/"]').first()
    await expect(thumb).toBeVisible()
    await expect(thumb).toHaveAttribute('src', /\/api\/images\/abc123$/)
    // The image really loaded (naturalWidth is 0 for a broken image).
    expect(await thumb.evaluate((element) => element.naturalWidth)).toBeGreaterThan(0)
  })
})

test.describe('account page', () => {
  test('switches between sign in and registration', async ({ page }) => {
    await mockApi(page)
    await page.goto('/account')

    await expect(page.getByRole('heading', { name: /^sign in$/i })).toBeVisible()
    await page.getByRole('button', { name: /create an account instead/i }).click()
    await expect(page.getByRole('heading', { name: /create an account/i })).toBeVisible()
    await expect(page.getByLabel(/your name/i)).toBeVisible()
  })
})

test.describe('accessibility basics', () => {
  test('the form is keyboard reachable', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    // Tab from the top and confirm focus reaches an interactive control.
    for (let i = 0; i < 8; i += 1) {
      await page.keyboard.press('Tab')
    }
    const tag = await page.evaluate(() => document.activeElement?.tagName || '')
    expect(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']).toContain(tag)
  })

  test('has a visible focus indicator on the primary action', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')

    const submit = page.getByRole('button', { name: /analyse|analyze/i }).first()
    await submit.focus()
    const outline = await submit.evaluate((element) => {
      const styles = getComputedStyle(element)
      return `${styles.outlineStyle} ${styles.outlineWidth} ${styles.boxShadow}`
    })
    expect(outline).not.toMatch(/^none 0px none/)
  })
})
