import axios from 'axios'

// Configured at build time. Vite only inlines VITE_-prefixed variables, so this is
// public by construction and cannot accidentally carry a secret.
// `import.meta.env` is replaced by Vite at build time. Reading it through a local
// binding (rather than inline) keeps this module importable from plain Node, which is
// what lets scripts/check-api-errors.mjs test the error mapping without a browser.
const env = import.meta.env || {}
const RAW_BASE = env.VITE_API_BASE_URL || 'http://localhost:8000'
export const API_BASE_URL = RAW_BASE.replace(/\/+$/, '')

const TOKEN_KEY = 'cropguard.access_token'

/**
 * The signed-in account's access token. Kept in localStorage so a refresh does not sign
 * the farmer out; it expires server-side after 24 hours and is revoked by the server on
 * logout or password change, so a stale copy cannot be used.
 */
export function getAuthToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || null
  } catch {
    return null
  }
}

export function setAuthToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private mode: the session simply will not survive a refresh */
  }
}

/**
 * Resolve a stored-image path to an absolute URL.
 *
 * The API returns paths relative to itself (`/api/images/<token>`) so that the same
 * payload works whether the backend is on localhost or Render. Anything already absolute
 * (a Cloudinary URL, for instance) is returned untouched.
 */
export function imageUrl(path) {
  if (!path) return null
  if (/^https?:\/\//i.test(path)) return path
  const base = API_BASE_URL.replace(/\/+$/, '')
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}

export const api = axios.create({
  baseURL: API_BASE_URL,
  // Generous: the free backend can take a minute to wake from idle.
  timeout: 45000,
})

// Attach the signed-in account's token to every request, unless the caller already set
// an explicit Authorization header (the expert page does, when using a staff token).
api.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token && !config.headers?.Authorization) {
    config.headers = { ...config.headers, Authorization: `Bearer ${token}` }
  }
  return config
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

/**
 * Explain a failure to reach the API, distinguishing the two causes that produce the
 * same "network error" symptom in a browser:
 *
 * 1. The bundle was built pointing at a localhost URL, so a deployed site is calling
 *    the visitor's own machine. Common when VITE_API_BASE_URL is not set at build time.
 * 2. The backend is reachable but rejects the origin — a CORS misconfiguration. Browsers
 *    report this as a network failure with no response, which is why the two look alike.
 */
export function diagnoseConnectionFailure(error, { baseUrl = API_BASE_URL } = {}) {
  const host = typeof window !== 'undefined' ? window.location.hostname : ''
  const isLocalHost = host === 'localhost' || host === '127.0.0.1' || host === ''
  // baseUrl is injectable so the branches can be tested without a real deployment.
  const pointsAtLocalhost = /localhost|127\.0\.0\.1/.test(baseUrl)

  if (pointsAtLocalhost && !isLocalHost) {
    return {
      cause: 'build-config',
      message:
        `This build is pointing at ${baseUrl}, which is this device — not the server. ` +
        'Set VITE_API_BASE_URL to your backend URL in the hosting environment and redeploy; ' +
        'Vite bakes it in at build time, so a refresh will not fix it.',
    }
  }

  // A cold start looks like a network failure but means the opposite of a CORS block:
  // the request reached the server and the server was not awake yet. Blaming CORS here
  // sends people to the wrong setting.
  const timedOut =
    error?.code === 'ECONNABORTED' ||
    error?.status === 504 ||
    error?.status === 502 ||
    /timed out|did not respond/i.test(error?.message || '')

  if (timedOut) {
    return {
      cause: 'cold-start',
      message:
        `${baseUrl} did not respond in time. Free instances sleep when idle and take ` +
        'about a minute to wake, so this is usually temporary — open ' +
        `${baseUrl}/health to wake it, then try again.`,
    }
  }

  return {
    cause: 'cors-or-down',
    message:
      `Could not reach ${baseUrl}. If the backend is up (open ${baseUrl}/health), ` +
      `the likely cause is CORS: add ${host || 'this site'} to FRONTEND_ORIGIN on the backend.`,
  }
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

/**
 * Fetch one report. Access needs the one-time token returned when it was created —
 * the report id alone deliberately grants nothing.
 */
export async function fetchReport(reportId, reportToken) {
  try {
    const { data } = await api.get(`/api/reports/${reportId}`, {
      headers: reportToken ? { 'X-Report-Token': reportToken } : {},
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** Look a report up by its public reference (CG-00042) using the farmer's access code. */
export async function fetchReportByReference(reference, reportToken) {
  try {
    const { data } = await api.get(
      `/api/reports/by-reference/${encodeURIComponent(reference.trim())}`,
      { headers: reportToken ? { 'X-Report-Token': reportToken } : {} },
    )
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** The expert review queue. Requires a staff bearer token. */
export async function fetchReviewQueue(expertToken, { status = 'Pending_Expert_Review', limit = 50 } = {}) {
  try {
    const { data } = await api.get('/api/reports', {
      params: { status, limit },
      headers: { Authorization: `Bearer ${expertToken}` },
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** Confirm or correct a diagnosis. Requires a staff bearer token. */
export async function verifyReport(reportId, expertToken, payload) {
  try {
    const { data } = await api.patch(`/api/reports/${reportId}/verify`, payload, {
      headers: { Authorization: `Bearer ${expertToken}` },
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** Add a follow-up observation to a report the caller owns. */
export async function addFollowUp(reportId, reportToken, { conditionStatus, notes, file }) {
  const form = new FormData()
  form.append('condition_status', conditionStatus)
  if (notes && notes.trim()) form.append('farmer_notes', notes.trim())
  if (file) form.append('image', file, file.name || 'followup.jpg')
  try {
    const { data } = await api.post(`/api/reports/${reportId}/followup`, form, {
      headers: {
        'Content-Type': 'multipart/form-data',
        ...(reportToken ? { 'X-Report-Token': reportToken } : {}),
      },
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

// --- accounts ---------------------------------------------------------------

export async function registerAccount({ email, password, displayName }) {
  try {
    const { data } = await api.post('/api/auth/register', {
      email,
      password,
      display_name: displayName,
    })
    setAuthToken(data.access_token)
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function loginAccount({ email, password }) {
  try {
    const { data } = await api.post('/api/auth/login', { email, password })
    setAuthToken(data.access_token)
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function fetchMe() {
  try {
    const { data } = await api.get('/api/auth/me')
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function logoutAccount() {
  try {
    await api.post('/api/auth/logout')
  } catch {
    /* the token is being discarded either way */
  } finally {
    setAuthToken(null)
  }
}

/** Ask for a reset email. The server answers the same way for unknown addresses. */
export async function forgotPassword(email) {
  try {
    const { data } = await api.post('/api/auth/forgot-password', { email })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** Redeem a reset token and set a new password. */
export async function resetPassword({ token, newPassword }) {
  try {
    const { data } = await api.post('/api/auth/reset-password', {
      token,
      new_password: newPassword,
    })
    // Every session was revoked server-side, so drop the local token too.
    setAuthToken(null)
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function changePassword({ currentPassword, newPassword }) {
  try {
    const { data } = await api.post('/api/auth/password', {
      current_password: currentPassword,
      new_password: newPassword,
    })
    // The server revoked every token, including this one.
    setAuthToken(null)
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function deleteAccount(password) {
  try {
    const { data } = await api.delete('/api/auth/account', { data: { password } })
    setAuthToken(null)
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

// --- expert applications ----------------------------------------------------

export async function requestExpertAccess({ justification, affiliation, regionName }) {
  try {
    const { data } = await api.post('/api/expert-requests', {
      justification,
      affiliation: affiliation || null,
      region_name: regionName || null,
    })
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function fetchMyExpertRequests() {
  try {
    const { data } = await api.get('/api/expert-requests/me')
    return data.applications || []
  } catch (error) {
    throw toApiError(error)
  }
}

/** Admin only. Returns null when the caller is not an administrator. */
export async function fetchExpertRequests(staffToken, status = 'pending') {
  try {
    const { data } = await api.get('/api/expert-requests', {
      params: { status },
      headers: staffToken ? { Authorization: `Bearer ${staffToken}` } : {},
    })
    return data.applications || []
  } catch (error) {
    if (error.status === 401 || error.status === 403) return null
    throw error
  }
}

export async function decideExpertRequest(staffToken, applicationId, decision, notes) {
  try {
    const { data } = await api.post(
      `/api/expert-requests/${applicationId}/${decision}`,
      { notes: notes || null },
      { headers: staffToken ? { Authorization: `Bearer ${staffToken}` } : {} },
    )
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** The advisory knowledge base as cards. Public reference material. */
export async function fetchDiseases() {
  try {
    const { data } = await api.get('/api/diseases')
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** The deployed model's contract: classes, crops, healthy-class coverage, thresholds. */
export async function fetchModelContract() {
  try {
    const { data } = await api.get('/api/model')
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/** Public, anonymised outbreak points for the map. No authentication required. */
export async function fetchOutbreaks({ cropType, risk, disease, regionName, days } = {}) {
  try {
    const { data } = await api.get('/api/outbreaks', {
      params: {
        ...(cropType ? { crop_type: cropType } : {}),
        ...(risk ? { risk } : {}),
        ...(disease ? { disease } : {}),
        ...(regionName ? { region_name: regionName } : {}),
        ...(days ? { days } : {}),
      },
    })
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
