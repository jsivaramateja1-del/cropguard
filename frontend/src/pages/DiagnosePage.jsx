import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ClipboardList,
  Copy,
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
import PhotoThumbnail from '../components/PhotoThumbnail.jsx'
import ConfidenceMeter from '../components/ConfidenceMeter.jsx'
import AdvisoryPanel from '../components/AdvisoryPanel.jsx'
import WeatherRiskCard from '../components/WeatherRiskCard.jsx'
import { Button } from '../components/ui/button.jsx'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card.jsx'
import { Input } from '../components/ui/input.jsx'
import { Label } from '../components/ui/label.jsx'
import { Checkbox } from '../components/ui/checkbox.jsx'
import { Alert } from '../components/ui/alert.jsx'
import { Badge } from '../components/ui/badge.jsx'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '../components/ui/dialog.jsx'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion.jsx'

const dateFormatter = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' })

function formatWhen(iso) {
  if (!iso) return ''
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormatter.format(parsed)
}

function splitLabel(label = '') {
  const [crop, condition] = label.split('___')
  return { crop, condition: (condition || '').replace(/_/g, ' ') }
}

/** A quiet banner when the backend is reachable but not fully healthy, or not reachable. */
function HealthBanner() {
  const [problem, setProblem] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then((health) => {
        if (cancelled || health.status === 'ok') return
        // Matches the /health contract: status plus per-component checks. The model is
        // essential, so an unloaded model means the app cannot diagnose at all.
        const model = health.checks?.model
        const storage = health.checks?.storage
        if (model && model.ok === false) {
          setProblem(
            `The diagnosis model is not loaded on the server${model.error ? `: ${model.error}` : '.'}`,
          )
        } else if (storage && storage.writable === false) {
          setProblem('The server cannot store photos right now, so analyses will fail.')
        } else {
          setProblem('The server reports a problem. Some features may not work.')
        }
      })
      .catch((error) => {
        if (!cancelled) setProblem(diagnoseConnectionFailure(error).message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!problem) return null
  return (
    <Alert variant="warning" className="mb-4">
      <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">Service notice</p>
        <p>{problem}</p>
      </div>
    </Alert>
  )
}

function ReportNumberDialog({ reportId, reference, accessToken }) {
  const [copied, setCopied] = useState(null)

  async function copy(value, which) {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(which)
    } catch {
      setCopied(null)
    }
  }

  return (
    <Dialog onOpenChange={() => setCopied(false)}>
      <DialogTrigger asChild>
        <Button variant="secondary" className="flex-1">
          <ClipboardList size={16} aria-hidden="true" />
          Save report number
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Save your access code</DialogTitle>
        <DialogDescription>
          This code is shown once and is the only way to reopen your report or add follow-up
          observations. Keep it private — anyone with it can read this report.
        </DialogDescription>

        <div className="my-3 flex items-center justify-center rounded-lg border border-border bg-muted py-3">
          <span className="tabular text-2xl font-bold tracking-tight text-foreground">
            {reference || `#${reportId}`}
          </span>
        </div>

        {accessToken ? (
          <div className="mb-3">
            <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
              Access code
            </p>
            <code className="block break-all rounded-md border border-border bg-muted px-2.5 py-2 text-2xs text-foreground">
              {accessToken}
            </code>
          </div>
        ) : null}

        <div className="flex flex-col gap-2">
          <Button
            variant="secondary"
            onClick={() => copy(accessToken || '', 'token')}
            disabled={!accessToken}
          >
            {copied === 'token' ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
            {copied === 'token' ? 'Access code copied' : 'Copy access code'}
          </Button>
          <Button onClick={() => copy(reference || String(reportId), 'ref')} variant="secondary">
            {copied === 'ref' ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
            {copied === 'ref' ? 'Reference copied' : 'Copy reference number'}
          </Button>
          <DialogClose asChild>
            <Button>Done</Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
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

  const [phase, setPhase] = useState('idle') // idle | uploading | analysing
  const [uploadPercent, setUploadPercent] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const previewRef = useRef(null)

  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
  }, [])

  const handleSelect = useCallback((chosen) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    const url = URL.createObjectURL(chosen)
    previewRef.current = url
    setPreviewUrl(url)
    setFile(chosen)
    setError(null)
    setNotice(null)
  }, [])

  const handleClear = useCallback(() => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = null
    setPreviewUrl(null)
    setFile(null)
  }, [])

  const hasCoords =
    location?.latitude != null &&
    location?.longitude != null &&
    Number.isFinite(location.latitude) &&
    Number.isFinite(location.longitude)

  const busy = phase === 'uploading' || phase === 'analysing'
  const canSubmit = Boolean(file) && farmerName.trim().length >= 2 && hasCoords

  const missing = useMemo(() => {
    const gaps = []
    if (!file) gaps.push('a leaf photo')
    if (farmerName.trim().length < 2) gaps.push('your name')
    if (!hasCoords) gaps.push('your location')
    return gaps
  }, [file, farmerName, hasCoords])

  async function handleSubmit(event) {
    event?.preventDefault()
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
      setPhase('idle')
    } catch (submitError) {
      setError(submitError)
      setPhase('idle')
    }
  }

  function reset() {
    setResult(null)
    setError(null)
    setNotice(null)
    setPhase('idle')
    handleClear()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const report = result?.report
  const diagnosis = report ? splitLabel(report.final_diagnosis || report.predicted_disease) : null

  const submitLabel = busy
    ? phase === 'uploading'
      ? `Uploading… ${uploadPercent}%`
      : 'Analysing…'
    : 'Analyse leaf'

  return (
    <div className="space-y-4">
      <HealthBanner />

      {!result ? (
        <form onSubmit={handleSubmit} className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Check a leaf</CardTitle>
              <CardDescription className="text-sm leading-relaxed text-secondary-foreground">
                Photograph one affected leaf, tell us the crop, and CropGuard will screen it and
                suggest what to do next.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <CropPicker value={crop} onChange={setCrop} disabled={busy} />
              <ImagePicker
                file={file}
                previewUrl={previewUrl}
                onSelect={handleSelect}
                onClear={handleClear}
                disabled={busy}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">About this field</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="farmer-name">Your name</Label>
                <Input
                  id="farmer-name"
                  value={farmerName}
                  onChange={(event) => setFarmerName(event.target.value)}
                  placeholder="So the expert can address you"
                  maxLength={120}
                  autoComplete="name"
                  disabled={busy}
                />
              </div>

              <LocationField value={location} onChange={setLocation} disabled={busy} />

              <div className="space-y-1.5">
                <Label htmlFor="region" hint="optional">
                  Village or district
                </Label>
                <Input
                  id="region"
                  value={regionName}
                  onChange={(event) => setRegionName(event.target.value)}
                  placeholder="e.g. Vijayawada, Andhra Pradesh"
                  maxLength={160}
                  disabled={busy}
                />
              </div>

              <label className="flex cursor-pointer items-start gap-2.5 rounded-lg bg-muted px-3 py-2.5">
                <Checkbox
                  className="mt-0.5"
                  checked={askExpert}
                  onCheckedChange={(checked) => setAskExpert(checked === true)}
                  disabled={busy}
                  id="ask-expert"
                />
                <span className="text-xs leading-relaxed text-secondary-foreground">
                  <strong className="font-semibold text-foreground">
                    Ask an expert to review this.
                  </strong>{' '}
                  Recommended if you are unsure, the damage is spreading, or the plant is a
                  significant part of your income.
                </span>
              </label>
            </CardContent>
          </Card>

          {error ? (
            <Alert variant="destructive">
              <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
              <div>
                <p className="font-semibold">
                  {error.status === 503 ? 'Diagnosis service unavailable' : 'Could not analyse the photo'}
                </p>
                <p className="mt-0.5">{error.message}</p>
              </div>
            </Alert>
          ) : null}

          {notice ? <Alert variant="warning">{notice}</Alert> : null}

          {/* Desktop action */}
          <div className="hidden sm:block">
            <Button type="submit" size="lg" className="w-full" disabled={busy}>
              {busy ? (
                <Loader2 size={18} className="animate-spin" aria-hidden="true" />
              ) : (
                <ScanLine size={18} aria-hidden="true" />
              )}
              {submitLabel}
            </Button>
            {missing.length > 0 && !busy ? (
              <p className="mt-2 text-center text-2xs text-muted-foreground">
                Still needed: {missing.join(', ')}.
              </p>
            ) : null}
          </div>

          {/* Mobile action: a fixed bar, because one hand is usually holding the phone */}
          <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/95 px-4 py-3 backdrop-blur sm:hidden">
            <Button type="submit" size="lg" className="w-full" disabled={busy}>
              {busy ? (
                <Loader2 size={18} className="animate-spin" aria-hidden="true" />
              ) : (
                <ScanLine size={18} aria-hidden="true" />
              )}
              {submitLabel}
            </Button>
          </div>
        </form>
      ) : (
        <div className="space-y-4">
          {result.plausibility && result.plausibility.is_plausible_leaf === false ? (
            <Alert variant="destructive">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
              <div>
                <p className="font-semibold">This does not look like a crop leaf</p>
                <p className="mt-0.5">
                  Only {Math.round(result.plausibility.vegetation_fraction * 100)}% of the photo
                  looks like plant material. The model can only choose from its 16 known
                  diseases, so the result below is likely meaningless. Take a clear photo of an
                  affected leaf and try again.
                </p>
              </div>
            </Alert>
          ) : null}

          <Card className="animate-fade-in">
            <CardContent className="space-y-3.5 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-2xs font-bold uppercase tracking-[0.08em] text-muted-foreground">
                    Potential diagnosis
                  </p>
                  <h1 className="mt-0.5 text-xl font-bold leading-tight">{diagnosis.condition}</h1>
                  <p className="mt-0.5 text-sm text-muted-foreground">{diagnosis.crop}</p>
                </div>
                {/* The local preview is instant; the stored copy is what remains after a
                    reload, so it is the fallback rather than the primary. */}
                {previewUrl ? (
                  <img
                    src={previewUrl}
                    alt="Analysed leaf"
                    className="h-16 w-16 shrink-0 rounded-lg border border-border object-cover"
                  />
                ) : (
                  <PhotoThumbnail src={report.image_url} alt="Analysed leaf" size="md" />
                )}
              </div>

              <StatusBadge status={report.status} showHint />

              <ConfidenceMeter confidence={report.confidence} />

              {result.review_required && result.review_reasons?.length > 0 ? (
                <div className="rounded-lg bg-accent/10 px-3 py-2.5">
                  <p className="mb-1 text-2xs font-bold uppercase tracking-wide text-accent-foreground">
                    Why this needs a human
                  </p>
                  <ul className="space-y-1">
                    {result.review_reasons.map((reason, index) => (
                      <li key={index} className="text-xs leading-relaxed text-accent-foreground">
                        {reason}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {report.final_diagnosis && report.final_diagnosis !== report.predicted_disease ? (
                <p className="rounded-lg bg-muted px-3 py-2 text-xs leading-relaxed text-secondary-foreground">
                  An expert changed this diagnosis. The AI had suggested{' '}
                  <strong className="font-semibold">
                    {splitLabel(report.predicted_disease).condition}
                  </strong>{' '}
                  at {(report.confidence * 100).toFixed(0)}% confidence.
                </p>
              ) : null}

              {report.top_predictions?.length > 1 ? (
                <Accordion type="single" collapsible className="border-t border-border pt-1">
                  <AccordionItem value="others" className="border-0">
                    <AccordionTrigger className="py-2 text-xs font-semibold text-primary">
                      Other possibilities the model considered
                    </AccordionTrigger>
                    <AccordionContent>
                      <ul className="space-y-1">
                        {report.top_predictions.slice(1).map((item) => (
                          <li key={item.label} className="flex justify-between gap-2 text-xs">
                            <span className="text-muted-foreground">
                              {splitLabel(item.label).condition}
                            </span>
                            <span className="tabular font-medium text-foreground">
                              {(item.probability * 100).toFixed(1)}%
                            </span>
                          </li>
                        ))}
                      </ul>
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
              ) : null}

              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-border pt-2.5 text-2xs text-muted-foreground">
                <Badge variant="muted" className="tabular">
                  Report #{report.id}
                </Badge>
                <span>{formatWhen(report.created_at)}</span>
                <span aria-hidden="true">·</span>
                <span className="tabular">
                  {String(result.model?.backend || '').toUpperCase()} · {result.model?.inference_ms} ms
                </span>
              </div>
            </CardContent>
          </Card>

          <AdvisoryPanel advisory={result.advisory} />

          <WeatherRiskCard weather={result.weather} risk={result.risk} />

          {result.warnings?.length > 0 ? (
            <Card>
              <CardContent className="p-3.5">
                <p className="mb-1 text-2xs font-bold uppercase tracking-wide text-muted-foreground">
                  Notes
                </p>
                <ul className="space-y-1">
                  {result.warnings.map((warning, index) => (
                    <li key={index} className="text-2xs leading-relaxed text-muted-foreground">
                      {warning}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardContent className="p-3.5">
              <p className="flex items-start gap-2 text-2xs leading-relaxed text-muted-foreground">
                <Stethoscope size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                <span>{result.disclaimer}</span>
              </p>
            </CardContent>
          </Card>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Button variant="secondary" className="flex-1" onClick={reset}>
              <RotateCcw size={16} aria-hidden="true" />
              Check another leaf
            </Button>
            <ReportNumberDialog
              reportId={report.id}
              reference={report.reference}
              accessToken={result.access_token}
            />
          </div>
        </div>
      )}
    </div>
  )
}
