import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  ClipboardList,
  Loader2,
  RotateCcw,
  ScanLine,
  Stethoscope,
} from 'lucide-react'
import { analyzeLeaf, diagnoseConnectionFailure, fetchHealth } from '../api.js'
import CropPicker from '../components/CropPicker.jsx'
import ImagePicker from '../components/ImagePicker.jsx'
import LocationField from '../components/LocationField.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import ConfidenceMeter from '../components/ConfidenceMeter.jsx'
import AdvisoryPanel from '../components/AdvisoryPanel.jsx'
import WeatherRiskCard from '../components/WeatherRiskCard.jsx'

const dateFormatter = new Intl.DateTimeFormat('en-IN', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

function formatWhen(iso) {
  if (!iso) return ''
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormatter.format(parsed)
}

function splitLabel(label = '') {
  const [crop, condition] = label.split('___')
  return { crop, condition: (condition || '').replace(/_/g, ' ') }
}

/** A quiet banner shown when the backend is reachable but not fully healthy. */
function HealthBanner() {
  const [problem, setProblem] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then((health) => {
        if (cancelled) return
        if (health.status === 'ok') return
        const modelProblem = health.model && health.model.available === false
        setProblem(
          modelProblem
            ? `The diagnosis model is not loaded on the server${
                health.model.error ? `: ${health.model.error}` : '.'
              }`
            : 'The server reports a degraded state. Some features may not work.',
        )
      })
      .catch(() => {
        if (cancelled) return
        // A bare "network error" hides the two causes that look identical in a browser.
        setProblem(diagnoseConnectionFailure().message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!problem) return null
  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-soil-800">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-straw-600" aria-hidden="true" />
      <div>
        <p className="font-semibold">Service notice</p>
        <p>{problem}</p>
      </div>
    </div>
  )
}

export default function DiagnosePage() {
  const [crop, setCrop] = useState('Tomato')
  const [farmerName, setFarmerName] = useState('')
  const [regionName, setRegionName] = useState('')
  const [location, setLocation] = useState(null)
  const [file, setFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [askExpert, setAskExpert] = useState(false)

  const [phase, setPhase] = useState('idle') // idle | uploading | analysing | done
  const [uploadPercent, setUploadPercent] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  // Shown when the form is incomplete. The button used to be disabled in that case,
  // so a tap did nothing and explained nothing — the worst kind of dead end.
  const [notice, setNotice] = useState(null)

  const previewRef = useRef(null)

  useEffect(() => {
    return () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    }
  }, [])

  const handleSelect = useCallback((chosen) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    const url = URL.createObjectURL(chosen)
    previewRef.current = url
    setPreviewUrl(url)
    setFile(chosen)
    setError(null)
  }, [])

  const handleClear = useCallback(() => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = null
    setPreviewUrl(null)
    setFile(null)
  }, [])

  const hasCoords = location?.latitude != null && location?.longitude != null
  const canSubmit =
    Boolean(file) && Boolean(crop) && farmerName.trim().length >= 2 && hasCoords && phase === 'idle'

  const missing = useMemo(() => {
    const gaps = []
    if (!file) gaps.push('a leaf photo')
    if (farmerName.trim().length < 2) gaps.push('your name')
    if (!hasCoords) gaps.push('a location')
    return gaps
  }, [file, farmerName, hasCoords])

  async function handleSubmit(event) {
    event.preventDefault()
    if (busy) return

    if (!canSubmit) {
      const gaps = []
      if (!file) gaps.push('a leaf photo')
      if (farmerName.trim().length < 2) gaps.push('your name (at least 2 letters)')
      if (!hasCoords) gaps.push('your location — tap “Use my location” or enter coordinates')
      setNotice(`Before analysing, add ${gaps.join(', ')}.`)
      return
    }

    setNotice(null)

    setError(null)
    setResult(null)
    setPhase('uploading')
    setUploadPercent(0)

    try {
      const response = await analyzeLeaf({
        file,
        cropType: crop,
        farmerName,
        latitude: location.latitude,
        longitude: location.longitude,
        regionName,
        requestExpertReview: askExpert,
        onUploadProgress: (progressEvent) => {
          const total = progressEvent.total || file.size
          const percent = total ? Math.round((progressEvent.loaded / total) * 100) : 0
          setUploadPercent(Math.min(99, percent))
          if (percent >= 99) setPhase('analysing')
        },
      })
      setResult(response)
      setPhase('done')
    } catch (submitError) {
      setError(submitError)
      setPhase('idle')
    }
  }

  function reset() {
    setResult(null)
    setError(null)
    setPhase('idle')
    handleClear()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const busy = phase === 'uploading' || phase === 'analysing'
  const report = result?.report
  const diagnosis = report ? splitLabel(report.final_diagnosis || report.predicted_disease) : null

  return (
    <div className="space-y-5">
      <HealthBanner />

      {!result && (
        <header className="space-y-1">
          <h1 className="text-xl font-bold">Check a leaf</h1>
          <p className="text-sm leading-relaxed text-soil-700">
            Photograph one affected leaf, tell us the crop, and CropGuard will screen it
            and suggest what to do next.
          </p>
        </header>
      )}

      {!result && (
        <form onSubmit={handleSubmit} className="card space-y-5 p-4">
          <CropPicker value={crop} onChange={setCrop} disabled={busy} />

          <ImagePicker
            file={file}
            previewUrl={previewUrl}
            onSelect={handleSelect}
            onClear={handleClear}
            disabled={busy}
          />

          <div>
            <label className="field-label" htmlFor="farmer-name">
              Your name
            </label>
            <input
              id="farmer-name"
              className="input"
              value={farmerName}
              onChange={(event) => setFarmerName(event.target.value)}
              placeholder="So the expert can address you"
              maxLength={120}
              autoComplete="name"
              disabled={busy}
            />
          </div>

          <LocationField value={location} onChange={setLocation} disabled={busy} />

          <div>
            <label className="field-label" htmlFor="region">
              Village or district <span className="font-normal text-soil-500">(optional)</span>
            </label>
            <input
              id="region"
              className="input"
              value={regionName}
              onChange={(event) => setRegionName(event.target.value)}
              placeholder="e.g. Vijayawada, Andhra Pradesh"
              maxLength={160}
              disabled={busy}
            />
          </div>

          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg bg-soil-50 px-3 py-2.5">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-soil-400 text-leaf-700 focus:ring-leaf-600"
              checked={askExpert}
              onChange={(event) => setAskExpert(event.target.checked)}
              disabled={busy}
            />
            <span className="text-xs leading-relaxed text-soil-700">
              <strong className="font-semibold text-soil-800">Ask an expert to review this.</strong>{' '}
              Recommended if you are unsure, the damage is spreading, or the plant is a
              significant part of your income.
            </span>
          </label>

          {error && (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-alert-600">
                <AlertTriangle size={15} aria-hidden="true" />
                {error.status === 503 ? 'Diagnosis service unavailable' : 'Could not analyse the photo'}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-soil-700">{error.message}</p>
            </div>
          )}

          <div className="space-y-2">
            {notice && (
              <p
                role="alert"
                className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-soil-800"
              >
                {notice}
              </p>
            )}
            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy ? (
                <Loader2 size={18} className="animate-spin" aria-hidden="true" />
              ) : (
                <ScanLine size={18} aria-hidden="true" />
              )}
              {phase === 'uploading'
                ? `Uploading photo… ${uploadPercent}%`
                : phase === 'analysing'
                  ? 'Analysing leaf…'
                  : 'Analyse leaf'}
            </button>
            {missing.length > 0 && !busy && (
              <p className="text-center text-xs text-soil-600">
                Still needed: {missing.join(', ')}.
              </p>
            )}
            {busy && (
              <p className="text-center text-xs text-soil-600">
                This can take a few seconds on a slow connection.
              </p>
            )}
          </div>
        </form>
      )}

      {result && (
        <div className="space-y-4">
          <section className="card space-y-3 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="section-title">Potential diagnosis</p>
                <h1 className="mt-0.5 text-lg font-bold leading-snug">
                  {diagnosis.condition}
                </h1>
                <p className="text-sm text-soil-700">{diagnosis.crop}</p>
              </div>
              {previewUrl && (
                <img
                  src={previewUrl}
                  alt="Analysed leaf"
                  className="h-16 w-16 shrink-0 rounded-lg border border-soil-200 object-cover"
                />
              )}
            </div>

            <StatusBadge status={report.status} showHint />

            <ConfidenceMeter confidence={report.confidence} />

            {result.review_required && result.review_reasons?.length > 0 && (
              <div className="rounded-lg bg-amber-50 px-3 py-2">
                <p className="mb-1 text-xs font-semibold text-soil-800">
                  Why this needs a human
                </p>
                <ul className="space-y-1">
                  {result.review_reasons.map((reason, index) => (
                    <li key={index} className="text-xs leading-relaxed text-soil-700">
                      {reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {report.final_diagnosis && report.final_diagnosis !== report.predicted_disease && (
              <p className="rounded-lg bg-soil-100 px-3 py-2 text-xs leading-relaxed text-soil-700">
                An expert changed this diagnosis. The AI had suggested{' '}
                <strong className="font-semibold">
                  {splitLabel(report.predicted_disease).condition}
                </strong>{' '}
                at {(report.confidence * 100).toFixed(0)}% confidence.
              </p>
            )}

            {result.report.top_predictions?.length > 1 && (
              <details className="text-xs">
                <summary className="cursor-pointer font-semibold text-leaf-700">
                  Other possibilities the model considered
                </summary>
                <ul className="mt-2 space-y-1">
                  {result.report.top_predictions.slice(1).map((item) => (
                    <li key={item.label} className="flex justify-between gap-2 text-soil-700">
                      <span>{splitLabel(item.label).condition}</span>
                      <span className="font-medium">{(item.probability * 100).toFixed(1)}%</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <p className="border-t border-soil-200 pt-2 text-[11px] leading-relaxed text-soil-500">
              Report #{report.id} · {formatWhen(report.created_at)} ·{' '}
              {result.model?.backend === 'onnx' ? 'ONNX' : result.model?.backend} ·{' '}
              {result.model?.inference_ms} ms
            </p>
          </section>

          <AdvisoryPanel advisory={result.advisory} />

          <WeatherRiskCard weather={result.weather} risk={result.risk} />

          {result.warnings?.length > 0 && (
            <section className="rounded-lg border border-soil-200 bg-white px-3 py-2.5">
              <p className="mb-1 text-xs font-semibold text-soil-700">Notes</p>
              <ul className="space-y-1">
                {result.warnings.map((warning, index) => (
                  <li key={index} className="text-[11px] leading-relaxed text-soil-600">
                    {warning}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="rounded-lg border border-soil-200 bg-white px-3 py-2.5">
            <p className="flex items-start gap-2 text-[11px] leading-relaxed text-soil-600">
              <Stethoscope size={14} className="mt-0.5 shrink-0 text-soil-500" aria-hidden="true" />
              <span>{result.disclaimer}</span>
            </p>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <button type="button" className="btn-secondary flex-1" onClick={reset}>
              <RotateCcw size={17} aria-hidden="true" />
              Check another leaf
            </button>
            <a
              className="btn-secondary flex-1"
              href={`#report-${report.id}`}
              onClick={(event) => {
                event.preventDefault()
                navigator.clipboard
                  ?.writeText(String(report.id))
                  .then(() => alert(`Report number ${report.id} copied. Use it to look the report up later.`))
                  .catch(() => alert(`Your report number is ${report.id}.`))
              }}
            >
              <ClipboardList size={17} aria-hidden="true" />
              Save report number
              <ArrowRight size={15} aria-hidden="true" />
            </a>
          </div>
        </div>
      )}
    </div>
  )
}
