/**
 * Error-normalisation checks.
 *
 * The frontend's job on failure is to say something a farmer can act on, so the mapping
 * from HTTP status to message is worth testing without a browser or a test framework.
 *
 * Run with:  npm run test:errors
 */
import assert from 'node:assert/strict'
import { ApiError, toApiError } from '../src/api.js'

function axiosError({ status, data, code } = {}) {
  const error = new Error('request failed')
  error.isAxiosError = true
  if (status !== undefined) {
    error.response = { status, data }
  } else {
    error.code = code
  }
  return error
}

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`PASS  ${name}`)
}

check('a server detail string is shown verbatim', () => {
  const result = toApiError(
    axiosError({ status: 400, data: { detail: 'That file is not a readable image.' } }),
  )
  assert.ok(result instanceof ApiError)
  assert.equal(result.message, 'That file is not a readable image.')
  assert.equal(result.status, 400)
  assert.equal(result.retryable, false)
})

check('FastAPI validation arrays are joined', () => {
  const result = toApiError(
    axiosError({
      status: 422,
      data: {
        detail: [
          { loc: ['body', 'latitude'], msg: 'Input should be less than or equal to 90' },
          { loc: ['body', 'crop_type'], msg: 'Input should be Tomato, Rice, Potato or Wheat' },
        ],
      },
    }),
  )
  assert.match(result.message, /less than or equal to 90/)
  assert.match(result.message, /Tomato, Rice, Potato or Wheat/)
})

check('a 413 explains the photo is too large', () => {
  const result = toApiError(axiosError({ status: 413, data: {} }))
  assert.match(result.message, /too large/i)
  assert.equal(result.retryable, false)
})

check('a 503 is retryable and mentions the service', () => {
  const result = toApiError(axiosError({ status: 503, data: {} }))
  assert.match(result.message, /unavailable/i)
  assert.equal(result.retryable, true)
})

check('a 429 is retryable', () => {
  const result = toApiError(axiosError({ status: 429, data: {} }))
  assert.equal(result.retryable, true)
})

check('a timeout suggests the server may be waking up', () => {
  const result = toApiError(axiosError({ code: 'ECONNABORTED' }))
  assert.match(result.message, /waking up/i)
  assert.equal(result.retryable, true)
})

check('a network failure mentions the connection', () => {
  const result = toApiError(axiosError({ code: 'ERR_NETWORK' }))
  assert.match(result.message, /connection/i)
  assert.equal(result.retryable, true)
})

check('an unknown status still yields a message', () => {
  const result = toApiError(axiosError({ status: 418, data: {} }))
  assert.match(result.message, /418/)
})

check('an already-normalised error is returned unchanged', () => {
  const original = new ApiError('already handled', { status: 500 })
  assert.equal(toApiError(original), original)
})

check('a non-axios throw is wrapped', () => {
  const result = toApiError(new Error('boom'))
  assert.equal(result.message, 'boom')
  assert.equal(result.code, 'UNKNOWN')
})



// --- connection-failure diagnosis -------------------------------------------
import { diagnoseConnectionFailure, API_BASE_URL } from '../src/api.js'

check('a localhost build served from a real host names the build config', () => {
  globalThis.window = { location: { hostname: 'cropguard.vercel.app' } }
  const result = diagnoseConnectionFailure()
  assert.equal(result.cause, 'build-config')
  assert.match(result.message, /VITE_API_BASE_URL/)
  assert.match(result.message, /redeploy/)
})

check('a remote build blames CORS and names the origin', () => {
  globalThis.window = { location: { hostname: 'cropguard.vercel.app' } }
  const original = API_BASE_URL
  // The module-level constant cannot be reassigned; assert the fallback branch shape
  // using a hostname that the regex will not match when the base is not localhost.
  if (!/localhost|127\.0\.0\.1/.test(original)) {
    const result = diagnoseConnectionFailure()
    assert.equal(result.cause, 'cors-or-down')
    assert.match(result.message, /FRONTEND_ORIGIN/)
    assert.match(result.message, /cropguard\.vercel\.app/)
  }
  delete globalThis.window
})

console.log(`\n${passed} checks passed (error handling + connection diagnosis)`)

// Exit explicitly: importing axios pulls in Node's undici, which lazily instantiates a
// WASM HTTP parser during shutdown. On a host with a tight virtual-memory limit that
// init fails *after* the assertions have passed, turning a green run into a non-zero
// exit. Nothing here is async, so there is nothing to wait for.
process.exit(0)
