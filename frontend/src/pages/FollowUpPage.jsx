import { useState } from 'react'
import { CalendarClock, History, Loader2, Search, Sprout, TriangleAlert, UserCheck } from 'lucide-react'
import { addFollowUp, fetchReportByReference } from '../api.js'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card.jsx'
import { Button } from '../components/ui/button.jsx'
import { Input } from '../components/ui/input.jsx'
import { Label } from '../components/ui/label.jsx'
import { Alert } from '../components/ui/alert.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import PhotoThumbnail from '../components/PhotoThumbnail.jsx'
import ConfidenceMeter from '../components/ConfidenceMeter.jsx'

const CONDITIONS = [
  { value: 'Recovering', label: 'Recovering', hint: 'Symptoms are easing' },
  { value: 'No_Change', label: 'No change', hint: 'About the same' },
  { value: 'Worsening', label: 'Worsening', hint: 'Spreading or getting worse' },
]

function conditionOf(label = '') {
  return (label.split('___')[1] || label).replace(/_/g, ' ')
}

function when(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Timeline({ report }) {
  const entries = [
    {
      at: report.created_at,
      title: 'Photo analysed',
      detail: `${conditionOf(report.predicted_disease)} at ${Math.round(report.confidence * 100)}% confidence`,
      icon: Sprout,
      // The analysed photo is shown on the report card above; repeating it here would
      // just be the same picture twice.
      photo: null,
    },
    ...(report.follow_ups || []).map((entry) => ({
      at: entry.created_at,
      title: `Follow-up: ${entry.condition_status.replace(/_/g, ' ').toLowerCase()}`,
      detail: entry.farmer_notes || 'No notes',
      icon: History,
      photo: entry.followup_image_url,
    })),
    ...(report.audit_events || []).map((entry) => ({
      at: entry.created_at,
      title: `Reviewed by ${entry.actor_name}`,
      detail:
        entry.new_state?.includes('Confirmed') || entry.new_state?.includes('Corrected')
          ? `${entry.previous_state} → ${entry.new_state}${entry.notes ? ` — ${entry.notes}` : ''}`
          : entry.notes || entry.action,
      icon: UserCheck,
    })),
  ].sort((a, b) => new Date(a.at) - new Date(b.at))

  return (
    <ol className="space-y-2.5">
      {entries.map((entry, index) => {
        const Icon = entry.icon
        return (
          <li key={`${entry.at}-${index}`} className="flex gap-2.5">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted">
              <Icon size={12} aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold">{entry.title}</p>
              <p className="text-2xs leading-relaxed text-muted-foreground">{entry.detail}</p>
              <p className="text-2xs text-muted-foreground/80">{when(entry.at)}</p>
              {entry.photo ? (
                <PhotoThumbnail
                  src={entry.photo}
                  alt={`Photo for: ${entry.title}`}
                  size="sm"
                  className="mt-1.5"
                />
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export default function FollowUpPage() {
  const [reference, setReference] = useState('')
  const [token, setToken] = useState('')
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const [condition, setCondition] = useState('Recovering')
  const [notes, setNotes] = useState('')
  const [file, setFile] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(null)

  async function lookup(event) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    setReport(null)
    setSaved(null)
    try {
      const data = await fetchReportByReference(reference, token)
      setReport(data)
    } catch (lookupError) {
      setError(lookupError)
    } finally {
      setLoading(false)
    }
  }

  async function submit(event) {
    event.preventDefault()
    if (!report) return
    setSaving(true)
    setError(null)
    try {
      await addFollowUp(report.id, token, {
        conditionStatus: condition,
        notes,
        file,
      })
      const refreshed = await fetchReportByReference(reference, token)
      setReport(refreshed)
      setSaved('Observation saved.')
      setNotes('')
      setFile(null)
    } catch (saveError) {
      setError(saveError)
    } finally {
      setSaving(false)
    }
  }

  const reviewed = (report?.audit_events || []).length > 0

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-xl font-bold">Follow up</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Open a report with its reference number and the access code you saved, then record
          how the crop is doing.
        </p>
      </header>

      <Card>
        <CardContent className="pt-4">
          <form onSubmit={lookup} className="space-y-3">
            <div>
              <Label htmlFor="reference">Report reference</Label>
              <Input
                id="reference"
                className="mt-1"
                placeholder="CG-00002"
                value={reference}
                onChange={(event) => setReference(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="access-code">Access code</Label>
              <Input
                id="access-code"
                className="mt-1"
                type="password"
                autoComplete="off"
                placeholder="The code shown when the report was created"
                value={token}
                onChange={(event) => setToken(event.target.value)}
              />
            </div>
            <Button
              type="submit"
              className="w-full"
              disabled={loading || !reference.trim() || !token.trim()}
            >
              {loading ? (
                <Loader2 size={15} className="animate-spin" aria-hidden="true" />
              ) : (
                <Search size={15} aria-hidden="true" />
              )}
              Open report
            </Button>
          </form>
        </CardContent>
      </Card>

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Could not open that report</p>
            <p className="mt-0.5">{error.message}</p>
          </div>
        </Alert>
      ) : null}

      {saved ? (
        <Alert>
          <History size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <p>{saved}</p>
        </Alert>
      ) : null}

      {report ? (
        <>
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-start gap-2.5">
                  <PhotoThumbnail
                    src={report.image_url}
                    alt={`Leaf submitted for ${report.crop_type}`}
                    size="md"
                  />
                  <div className="min-w-0">
                    <CardTitle className="text-sm">
                      {conditionOf(report.predicted_disease)}
                    </CardTitle>
                    <CardDescription>
                      {report.crop_type} · {report.reference || `#${report.id}`}
                    </CardDescription>
                  </div>
                </div>
                <StatusBadge status={report.status} />
              </div>
            </CardHeader>
            <CardContent className="space-y-2.5">
              <ConfidenceMeter value={report.confidence} />
              {reviewed ? (
                <p className="rounded-lg bg-muted px-2.5 py-2 text-2xs text-secondary-foreground">
                  An agricultural expert has reviewed this report. Their decision and notes
                  are in the timeline below.
                </p>
              ) : (
                <p className="rounded-lg bg-muted px-2.5 py-2 text-2xs text-secondary-foreground">
                  No expert has reviewed this yet. It is queued — CropGuard does not promise
                  a response time.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Record an observation</CardTitle>
              <CardDescription>How is the crop doing now?</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={submit} className="space-y-3">
                <div className="grid grid-cols-3 gap-1.5">
                  {CONDITIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setCondition(option.value)}
                      className={`rounded-lg border px-2 py-2 text-center text-2xs font-semibold transition-colors ${
                        condition === option.value
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border bg-card text-muted-foreground'
                      }`}
                      aria-pressed={condition === option.value}
                    >
                      {option.label}
                      <span className="mt-0.5 block text-[10px] font-normal">
                        {option.hint}
                      </span>
                    </button>
                  ))}
                </div>

                <div>
                  <Label htmlFor="followup-notes">Notes (optional)</Label>
                  <textarea
                    id="followup-notes"
                    rows={2}
                    className="mt-1 w-full rounded-lg border border-input bg-card px-2.5 py-2 text-sm"
                    placeholder="What changed since the last photo?"
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                  />
                </div>

                <div>
                  <Label htmlFor="followup-photo">New photo (optional)</Label>
                  <input
                    id="followup-photo"
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    className="mt-1 block w-full text-xs text-muted-foreground file:mr-2 file:rounded-md file:border-0 file:bg-secondary file:px-2.5 file:py-1.5 file:text-2xs file:font-semibold"
                    onChange={(event) => setFile(event.target.files?.[0] || null)}
                  />
                </div>

                <Button type="submit" className="w-full" disabled={saving}>
                  {saving ? (
                    <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <CalendarClock size={15} aria-hidden="true" />
                  )}
                  Save observation
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">History</CardTitle>
              <CardDescription>Everything that happened to this report</CardDescription>
            </CardHeader>
            <CardContent>
              <Timeline report={report} />
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  )
}
