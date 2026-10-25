import axios from 'axios'

// Configured at build time. Vite only inlines VITE_-prefixed variables, so this is
// public by construction and cannot accidentally carry a secret.
// `import.meta.env` is replaced by Vite at build time. Reading it through a local
// binding (rather than inline) keeps this module importable from plain Node, which is
// what lets scripts/check-api-errors.mjs test the error mapping without a browser.
const env = import.meta.env || {}
const RAW_BASE = env.VITE_API_BASE_URL || 'http://localhost:8000'
export const API_BASE_URL = RAW_BASE.replace(/\/+$/, '')

export const api = axios.create({
  baseURL: API_BASE_URL,
  // Generous: the free backend can take a minute to wake from idle.
  timeout: 45000,
})

/** An error with a message that is safe and useful to show a farmer. */
export class ApiError extends Error {
  constructor(message, { status = null, code = null, retryable = false } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryable = retryable
  }
}

const STATUS_MESSAGES = {
  400: 'The photo could not be read. Try taking it again in good light.',
  401: 'This request was not authorised.',
  403: 'This request was refused.',
  404: 'That report could not be found.',
  409: 'This report has already been reviewed.',
  413: 'The photo is too large. Take it at a lower resolution and try again.',
  422: 'Some of the details are not valid. Check the crop and the location.',
  429: 'Too many requests just now. Wait a moment and try again.',
  500: 'The server hit a problem. Please try again.',
  502: 'The server is restarting. Wait a moment and try again.',
  503: 'The diagnosis service is unavailable right now. Please try again shortly.',
  504: 'The server took too long to respond. Please try again.',
}

function detailFrom(data) {
  if (!data) return null
  const detail = data.detail ?? data.message
  if (typeof detail === 'string') return detail
  // FastAPI validation errors arrive as a list of objects.
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (typeof item === 'string' ? item : item?.msg || item?.loc?.join('.')))
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return null
}

/** Turn any axios failure into an ApiError with a human-readable message. */
export function toApiError(error) {
  if (error instanceof ApiError) return error

  if (axios.isAxiosError(error)) {
    if (error.response) {
      const status = error.response.status
      const detail = detailFrom(error.response.data)
      return new ApiError(detail || STATUS_MESSAGES[status] || `Request failed (${status}).`, {
        status,
        retryable: status >= 500 || status === 429,
      })
    }
    if (error.code === 'ECONNABORTED') {
      return new ApiError(
        'The server did not respond in time. It may be waking up — try again in a minute.',
        { code: error.code, retryable: true },
      )
    }
    return new ApiError(
      'Could not reach the CropGuard server. Check your connection and try again.',
      { code: error.code || 'NETWORK', retryable: true },
    )
  }

  return new ApiError(error?.message || 'Something went wrong.', { code: 'UNKNOWN' })
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------
export async function fetchHealth() {
  try {
    const { data } = await api.get('/health')
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/**
 * Submit a leaf photo for analysis.
 * Returns the full AnalyzeResponse: report, advisory, weather, risk, review flags.
 */
export async function analyzeLeaf({
  file,
  cropType,
  farmerName,
  latitude,
  longitude,
  regionName,
  requestExpertReview = false,
  onUploadProgress,
}) {
  const form = new FormData()
  form.append('image', file, file.name || 'leaf.jpg')
  form.append('crop_type', cropType)
  form.append('farmer_name', farmerName.trim())
  form.append('latitude', String(latitude))
  form.append('longitude', String(longitude))
  if (regionName && regionName.trim()) form.append('region_name', regionName.trim())
  form.append('request_expert_review', requestExpertReview ? 'true' : 'false')

  try {
    const { data } = await api.post('/api/analyze', form, {
      onUploadProgress,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function fetchReport(reportId) {
  try {
    const { data } = await api.get(`/api/reports/${reportId}`)
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function fetchWeather({ latitude, longitude, disease }) {
  try {
    const { data } = await api.get('/api/weather', {
      params: { latitude, longitude, ...(disease ? { disease } : {}) },
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}
