/**
 * Canned API responses for the mocked end-to-end suite.
 *
 * These mirror the real schemas in backend/schemas.py. If a field is renamed in the API,
 * these mocks go stale and the tests keep passing against a shape that no longer exists —
 * so the live-smoke spec exists to catch that drift against the real service.
 */

const BASE_URL = '**/cropguard-api'

export const health = {
  status: 'ok',
  version: '0.3.0',
  checks: {
    database: { ok: true, dialect: 'postgresql', initialised: true, reason: null, hint: null, host: { applies: false } },
    model: { ok: true, backend: 'onnx', classes: 16, artifact_type: 'trained', error: null },
    storage: { backend: 'postgres', writable: true },
    weather: { providers: ['open-meteo', 'met-no'], cached_locations: 0, api_key_configured: false },
    auth: { staff_configured: true },
  },
  thresholds: { confidence: 0.75, ood_floor: 0.88 },
  advisory_entries: 21,
}

export const modelContract = {
  model_version: 'cropguard-mobilenetv3-v1',
  artifact_type: 'trained',
  backend: 'onnx',
  classes: [
    'Tomato___Early_blight',
    'Tomato___Late_blight',
    'Tomato___healthy',
    'Potato___Early_blight',
    'Potato___Late_blight',
    'Potato___healthy',
    'Rice___Brown_spot',
    'Rice___Leaf_smut',
  ],
  output_dimensions: 8,
  image_size: 224,
  channel_order: 'RGB',
  normalization: { mean: [0.485, 0.456, 0.406], std: [0.229, 0.224, 0.225] },
  supported_crops: ['Potato', 'Rice', 'Tomato'],
  classes_by_crop: {
    Tomato: ['Tomato___Early_blight', 'Tomato___Late_blight', 'Tomato___healthy'],
    Potato: ['Potato___Early_blight', 'Potato___Late_blight', 'Potato___healthy'],
    Rice: ['Rice___Brown_spot', 'Rice___Leaf_smut'],
  },
  healthy_class_available: { Potato: true, Rice: false, Tomato: true },
  confidence_threshold: 0.75,
  ood_confidence_floor: 0.88,
  provenance: { artifact: 'cropguard_mobilenetv3' },
}

export const weather = {
  available: true,
  source: 'met.no',
  weather: { temperature_c: 29.2, humidity_pct: 78, rain_mm: 0, wind_kmh: 12.4 },
  risk: {
    level: 'Moderate',
    score: 5,
    max_score: 12,
    factors: ['Humidity 78% favours late blight'],
    spray_window: 'Wind is light enough to spray this morning.',
  },
}

/** A confident diagnosis: auto-verified, advisory attached. */
export const analyzeVerified = {
  report: {
    id: 42,
    reference: 'CG-00042',
    farmer_name: 'Ravi Kumar',
    crop_type: 'Tomato',
    image_url: '/api/images/abc123',
    predicted_disease: 'Tomato___Late_blight',
    confidence: 0.93,
    status: 'AI_Verified',
    created_at: '2026-10-26T11:44:00+00:00',
    latitude: 16.5062,
    longitude: 80.648,
    region_name: 'Kelambakkam',
    follow_ups: [],
    audit_events: [],
  },
  access_token: 'test-access-token-abc123',
  plausibility: { is_plausible_leaf: true, vegetation_fraction: 0.42, threshold: 0.15 },
  advisory: {
    label: 'Tomato___Late_blight',
    crop: 'Tomato',
    condition: 'Late blight',
    is_healthy: false,
    summary: 'The most destructive disease of tomato and potato.',
    symptoms: ['Water-soaked patches on leaves', 'White mould on the underside'],
    cultural_control: ['Remove and destroy infected plants', 'Improve airflow between plants'],
    organic_management: ['Copper-based sprays may slow the spread'],
    chemical_management: { recommended: true, notes: 'Follow the product label.' },
    monitoring: 'Check the field every two to three days.',
    disclaimer: 'General guidance, not a prescription.',
  },
  risk: weather.risk,
  model: { backend: 'onnx', classes: 16, artifact_type: 'trained' },
  review_required: false,
  review_reasons: [],
  warnings: [],
  disclaimer: 'This is general agricultural guidance, not a prescription.',
}

/** Low confidence: routed to review, still shows a potential diagnosis. */
export const analyzeNeedsReview = {
  ...analyzeVerified,
  report: {
    ...analyzeVerified.report,
    id: 43,
    reference: 'CG-00043',
    confidence: 0.68,
    status: 'Pending_Expert_Review',
  },
  review_required: true,
  review_reasons: [
    'Confidence 0.68 is below the 0.75 threshold.',
    'Confidence 0.68 is below the 0.88 that genuine leaf photos normally reach.',
  ],
}

/** A non-leaf photo: the plausibility guard fires. */
export const analyzeNotALeaf = {
  ...analyzeVerified,
  report: { ...analyzeVerified.report, id: 44, reference: 'CG-00044', confidence: 0.25, status: 'Pending_Expert_Review' },
  plausibility: { is_plausible_leaf: false, vegetation_fraction: 0.0, threshold: 0.15 },
  advisory: null,
  review_required: true,
  review_reasons: [
    'The photo does not look like crop foliage — only 0% of it is green or brown plant material.',
  ],
  warnings: ['This photo may not be a crop leaf.'],
}

export const outbreaks = {
  points: [
    {
      latitude: 16.51,
      longitude: 80.65,
      crop_type: 'Tomato',
      predicted_disease: 'Tomato___Late_blight',
      weather_risk_level: 'Moderate',
      status: 'Pending_Expert_Review',
      reports: 3,
      latest_at: '2026-10-26T11:44:00+00:00',
    },
    {
      latitude: 13.08,
      longitude: 80.27,
      crop_type: 'Rice',
      predicted_disease: 'Rice___Brown_spot',
      weather_risk_level: 'Low',
      status: 'AI_Verified',
      reports: 1,
      latest_at: '2026-10-25T09:10:00+00:00',
    },
  ],
  summary: {
    total_reports: 4,
    points: 2,
    period_days: 90,
    by_crop: { Tomato: 3, Rice: 1 },
    by_risk: { Moderate: 3, Low: 1 },
    by_disease: { 'Tomato___Late_blight': 3, 'Rice___Brown_spot': 1 },
    hotspots: [
      { latitude: 16.51, longitude: 80.65, region_name: 'Kelambakkam', reports: 3 },
      { latitude: 13.08, longitude: 80.27, region_name: 'Chennai', reports: 1 },
    ],
  },
  attribution: 'Weather data by Open-Meteo.com (CC BY 4.0).',
}

/** One row of the staff review queue, as the expert page receives it. */
/** The advisory knowledge base, as /api/diseases returns it. */
export const diseases = {
  total: 5,
  crops: ['Potato', 'Rice', 'Tomato'],
  model_classes: 8,
  diseases: [
    {
      label: 'Tomato___Late_blight',
      crop: 'Tomato',
      condition: 'Late blight',
      pathogen_type: 'Oomycete',
      severity: 'high',
      is_healthy: false,
      summary: 'The most destructive disease of tomato and potato.',
      symptoms: ['Water-soaked patches on leaves', 'White mould on the underside'],
      favorable_conditions: ['Cool, wet weather'],
      monitoring: 'Check every two days',
      supported_by_model: true,
    },
    {
      label: 'Tomato___Early_blight',
      crop: 'Tomato',
      condition: 'Early blight',
      pathogen_type: 'Fungus',
      severity: 'moderate',
      is_healthy: false,
      summary: 'Concentric brown rings on older leaves.',
      symptoms: ['Brown spots with rings', 'Yellowing around spots'],
      favorable_conditions: ['Warm, humid weather'],
      monitoring: 'Weekly',
      supported_by_model: true,
    },
    {
      label: 'Rice___Brown_spot',
      crop: 'Rice',
      condition: 'Brown spot',
      pathogen_type: 'Fungus',
      severity: 'moderate',
      is_healthy: false,
      summary: 'Oval brown lesions on rice leaves.',
      symptoms: ['Oval brown lesions', 'Grey centres'],
      favorable_conditions: ['Poor nutrition', 'High humidity'],
      monitoring: 'Weekly',
      supported_by_model: true,
    },
    {
      label: 'Wheat___Yellow_rust',
      crop: 'Wheat',
      condition: 'Yellow rust',
      pathogen_type: 'Fungus',
      severity: 'high',
      is_healthy: false,
      summary: 'Yellow stripes of pustules along wheat leaves.',
      symptoms: ['Yellow pustules in stripes'],
      favorable_conditions: ['Cool nights, mild days'],
      monitoring: 'Weekly',
      supported_by_model: false,
    },
    {
      label: 'Tomato___healthy',
      crop: 'Tomato',
      condition: 'Healthy',
      pathogen_type: null,
      severity: null,
      is_healthy: true,
      summary: 'No disease detected.',
      symptoms: [],
      favorable_conditions: [],
      monitoring: null,
      supported_by_model: true,
    },
  ],
}

export const reviewQueue = {
  total: 1,
  items: [
    {
      id: 42,
      reference: 'CG-00042',
      crop_type: 'Tomato',
      image_url: '/api/images/abc123',
      predicted_disease: 'Tomato___Late_blight',
      confidence: 0.68,
      status: 'Pending_Expert_Review',
      region_name: 'Kelambakkam',
      created_at: '2026-10-26T11:44:00+00:00',
    },
  ],
}

// A 1x1 PNG, so the mocked image endpoint returns real bytes and the <img> actually loads.
const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

export const reportForFollowUp = {
  ...analyzeVerified.report,
  status: 'Expert_Confirmed',
  final_diagnosis: 'Tomato___Late_blight',
  expert_name: 'Dr Test Expert',
  expert_notes: 'Consistent with late blight. Remove affected plants.',
  verified_at: '2026-10-26T12:30:00+00:00',
  follow_ups: [
    {
      id: 1,
      report_id: 42,
      condition_status: 'Worsening',
      farmer_notes: 'Spreading to the next row.',
      created_at: '2026-10-26T18:00:00+00:00',
    },
  ],
  audit_events: [
    {
      id: 1,
      action: 'verify',
      actor_role: 'expert',
      actor_name: 'Dr Test Expert',
      previous_state: 'Pending_Expert_Review:Tomato___Late_blight',
      new_state: 'Expert_Confirmed:Tomato___Late_blight',
      notes: 'Consistent with late blight.',
      created_at: '2026-10-26T12:30:00+00:00',
    },
  ],
}

/**
 * Install the mock routes. `overrides` lets a test replace a single response.
 */
export async function mockApi(page, overrides = {}) {
  const responses = {
    health,
    model: modelContract,
    analyze: analyzeVerified,
    outbreaks,
    followUpReport: reportForFollowUp,
    reviewQueue,
    diseases,
    ...overrides,
  }

  await page.route('**/health', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses.health) }),
  )
  await page.route('**/api/model', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses.model) }),
  )
  await page.route('**/api/diseases', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses.diseases) }),
  )
  await page.route('**/api/outbreaks**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses.outbreaks) }),
  )
  await page.route('**/api/weather**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(weather) }),
  )
  await page.route('**/api/analyze', async (route) => {
    if (responses.analyze instanceof Error) {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: responses.analyze.message }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responses.analyze),
    })
  })
  await page.route('**/api/reports/by-reference/**', (route) => {
    if (responses.followUpReport === 'not-found') {
      return route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'No report with that reference.' }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responses.followUpReport),
    })
  })
  await page.route('**/api/reports/*/followup', (route) =>
    route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ id: 2, report_id: 42, condition_status: 'Recovering' }),
    }),
  )
  await page.route('**/api/images/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(TINY_PNG_BASE64, 'base64'),
    }),
  )
  // Exact path plus optional query — a plain glob would also catch
  // /api/reports/by-reference/... and /api/reports/<id>/followup.
  await page.route(/\/api\/reports(\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responses.reviewQueue),
    }),
  )
  await page.route('**/api/auth/**', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Sign in to use this endpoint.' }),
    }),
  )
  await page.route('**/api/auth/forgot-password', (route) =>
    route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'accepted',
        detail: 'If that address has an account, a reset link is on its way.',
      }),
    }),
  )
  await page.route('**/api/auth/reset-password', (route) => {
    const body = route.request().postDataJSON() || {}
    if (body.token === 'expired-token') {
      return route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'This reset link is invalid or has expired. Request a new one.',
        }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'password_reset', signed_out_everywhere: true }),
    })
  })
  await page.route('**/api/expert-requests**', (route) =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'This account does not have staff access.' }),
    }),
  )

  return responses
}

export { BASE_URL }
