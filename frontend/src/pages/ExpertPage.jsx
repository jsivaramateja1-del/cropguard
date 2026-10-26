import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  ClipboardList,
  KeyRound,
  Loader2,
  LogOut,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import {
  decideExpertRequest,
  fetchExpertRequests,
  fetchModelContract,
  fetchReviewQueue,
  loginAccount,
  verifyReport,
} from '../api.js'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card.jsx'
import { Button } from '../components/ui/button.jsx'
import { Badge } from '../components/ui/badge.jsx'
import { Input } from '../components/ui/input.jsx'
import { Label } from '../components/ui/label.jsx'
import { Alert } from '../components/ui/alert.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import ConfidenceMeter from '../components/ConfidenceMeter.jsx'

const TOKEN_KEY = 'cropguard.expert_token'

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

export default function ExpertPage() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) || '')
  const [tokenDraft, setTokenDraft] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signInMode, setSignInMode] = useState('account')
  const [queue, setQueue] = useState([])
  const [contract, setContract] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [action, setAction] = useState('confirm')
  const [corrected, setCorrected] = useState('')
  const [notes, setNotes] = useState('')
  const [expertName, setExpertName] = useState('')
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState(null)
  // Admin only: null means "not an administrator", so the section stays hidden.
  const [applications, setApplications] = useState(null)
  const [deciding, setDeciding] = useState(null)
  const [decisionNotes, setDecisionNotes] = useState('')

  useEffect(() => {
    fetchModelContract()
      .then(setContract)
      .catch(() => setContract(null))
  }, [])

  const load = useCallback(
    async (activeToken) => {
      if (!activeToken) return
      setLoading(true)
      setError(null)
      try {
        const data = await fetchReviewQueue(activeToken)
        setQueue(data.reports || [])
      } catch (loadError) {
        setError(loadError)
        if (loadError.status === 401 || loadError.status === 403) {
          sessionStorage.removeItem(TOKEN_KEY)
          setToken('')
        }
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  const loadApplications = useCallback(
    async (activeToken) => {
      if (!activeToken) return
      try {
        setApplications(await fetchExpertRequests(activeToken))
      } catch {
        setApplications(null)
      }
    },
    [],
  )

  useEffect(() => {
    if (token) {
      load(token)
      loadApplications(token)
    }
  }, [token, load, loadApplications])

  async function decide(applicationId, decision) {
    setDeciding(applicationId)
    setError(null)
    try {
      await decideExpertRequest(token, applicationId, decision, decisionNotes)
      setDecisionNotes('')
      await loadApplications(token)
    } catch (decisionError) {
      setError(decisionError)
    } finally {
      setDeciding(null)
    }
  }

  function signInWithToken(event) {
    event.preventDefault()
    const value = tokenDraft.trim()
    if (!value) return
    sessionStorage.setItem(TOKEN_KEY, value)
    setToken(value)
    setTokenDraft('')
  }

  async function signInWithAccount(event) {
    event.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await loginAccount({ email, password })
      if (!['expert', 'admin'].includes(data.user.role)) {
        setError({
          message:
            'This account is a farmer account. Expert review needs an expert or administrator account.',
        })
        setLoading(false)
        return
      }
      sessionStorage.setItem(TOKEN_KEY, data.access_token)
      setToken(data.access_token)
      setPassword('')
    } catch (signInError) {
      setError(signInError)
    } finally {
      setLoading(false)
    }
  }

  function signOut() {
    sessionStorage.removeItem(TOKEN_KEY)
    setToken('')
    setQueue([])
    setSelected(null)
    setResult(null)
  }

  const options = useMemo(() => {
    if (!selected || !contract) return []
    return contract.classes_by_crop?.[selected.crop_type] || []
  }, [selected, contract])

  async function submit(event) {
    event.preventDefault()
    if (!selected) return
    if (action === 'correct' && !corrected) {
      setError({ message: 'Choose the diagnosis you are correcting it to.' })
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await verifyReport(selected.id, token, {
        action,
        corrected_disease: action === 'correct' ? corrected : null,
        expert_notes: notes.trim() || null,
        expert_name: expertName.trim() || 'Agricultural expert',
      })
      setResult(updated)
      setSelected(null)
      setNotes('')
      setCorrected('')
      await load(token)
    } catch (saveError) {
      setError(saveError)
    } finally {
      setSaving(false)
    }
  }

  // ---------------------------------------------------------------- sign in
  if (!token) {
    return (
      <div className="mx-auto max-w-md space-y-4">
        <header className="space-y-1">
          <h1 className="text-xl font-bold">Expert review</h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            Sign in with the expert token configured on the server. Reports flagged for
            review are listed here; confirming or correcting one is recorded against your
            name.
          </p>
        </header>

        {error ? (
          <Alert variant="destructive">
            <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div>
              <p className="font-semibold">Could not sign in</p>
              <p className="mt-0.5">{error.message}</p>
            </div>
          </Alert>
        ) : null}

        <Card>
          <CardContent className="pt-4">
            {signInMode === 'account' ? (
              <form onSubmit={signInWithAccount} className="space-y-3">
                <div>
                  <Label htmlFor="expert-email">Email</Label>
                  <Input
                    id="expert-email"
                    type="email"
                    autoComplete="email"
                    className="mt-1"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="expert-password">Password</Label>
                  <Input
                    id="expert-password"
                    type="password"
                    autoComplete="current-password"
                    className="mt-1"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </div>
                <Button
                  type="submit"
                  className="w-full"
                  disabled={loading || !email.trim() || !password}
                >
                  {loading ? (
                    <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <KeyRound size={15} aria-hidden="true" />
                  )}
                  Sign in
                </Button>
              </form>
            ) : (
              <form onSubmit={signInWithToken} className="space-y-3">
                <div>
                  <Label htmlFor="expert-token">Staff token</Label>
                  <Input
                    id="expert-token"
                    type="password"
                    autoComplete="off"
                    className="mt-1"
                    placeholder="Paste the EXPERT_TOKEN value"
                    value={tokenDraft}
                    onChange={(event) => setTokenDraft(event.target.value)}
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">
                    The shared secret configured on the server. Kept in this tab only.
                  </p>
                </div>
                <Button type="submit" className="w-full" disabled={!tokenDraft.trim()}>
                  <KeyRound size={15} aria-hidden="true" />
                  Sign in with token
                </Button>
              </form>
            )}

            <button
              type="button"
              className="mt-3 w-full text-center text-2xs text-muted-foreground underline"
              onClick={() => {
                setSignInMode(signInMode === 'account' ? 'token' : 'account')
                setError(null)
              }}
            >
              {signInMode === 'account'
                ? 'Use a staff token instead'
                : 'Sign in with an email account'}
            </button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // ------------------------------------------------------------------ queue
  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <h1 className="text-xl font-bold">Expert review</h1>
          <p className="text-sm text-muted-foreground">
            {queue.length} report{queue.length === 1 ? '' : 's'} waiting for a decision
          </p>
        </div>
        <div className="flex gap-1.5">
          <Button variant="secondary" size="sm" onClick={() => load(token)} disabled={loading}>
            {loading ? (
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw size={14} aria-hidden="true" />
            )}
            Refresh
          </Button>
          <Button variant="ghost" size="sm" onClick={signOut}>
            <LogOut size={14} aria-hidden="true" />
            Sign out
          </Button>
        </div>
      </header>

      {result ? (
        <Alert>
          <CheckCircle2 size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">
              Report {result.reference || `#${result.id}`} marked{' '}
              {result.status.replace(/_/g, ' ').toLowerCase()}
            </p>
            <p className="mt-0.5">
              The AI prediction is preserved alongside your decision, and the audit trail
              records who changed what.
            </p>
          </div>
        </Alert>
      ) : null}

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Could not complete that</p>
            <p className="mt-0.5">{error.message}</p>
          </div>
        </Alert>
      ) : null}

      {selected ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              Reviewing {selected.reference || `#${selected.id}`}
            </CardTitle>
            <CardDescription>
              {selected.crop_type} · {conditionOf(selected.predicted_disease)} ·{' '}
              {Math.round(selected.confidence * 100)}% confidence
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-3">
              <fieldset className="space-y-1.5">
                <legend className="mb-1 text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Decision
                </legend>
                <label className="flex items-start gap-2 rounded-lg border border-border p-2.5">
                  <input
                    type="radio"
                    name="decision"
                    className="mt-0.5"
                    checked={action === 'confirm'}
                    onChange={() => setAction('confirm')}
                  />
                  <span className="text-xs">
                    <strong>Confirm</strong> the AI prediction
                    <span className="block text-muted-foreground">
                      {conditionOf(selected.predicted_disease)}
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2 rounded-lg border border-border p-2.5">
                  <input
                    type="radio"
                    name="decision"
                    className="mt-0.5"
                    checked={action === 'correct'}
                    onChange={() => setAction('correct')}
                  />
                  <span className="text-xs">
                    <strong>Correct</strong> to a different diagnosis
                    <span className="block text-muted-foreground">
                      The AI prediction stays in the record.
                    </span>
                  </span>
                </label>
              </fieldset>

              {action === 'correct' ? (
                <div>
                  <Label htmlFor="corrected">Correct diagnosis</Label>
                  <select
                    id="corrected"
                    className="mt-1 h-10 w-full rounded-lg border border-input bg-card px-2 text-sm"
                    value={corrected}
                    onChange={(event) => setCorrected(event.target.value)}
                  >
                    <option value="">Choose…</option>
                    {options.map((label) => (
                      <option key={label} value={label}>
                        {conditionOf(label)}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}

              <div>
                <Label htmlFor="expert-name">Your name</Label>
                <Input
                  id="expert-name"
                  className="mt-1"
                  placeholder="Shown to the farmer on the report"
                  value={expertName}
                  onChange={(event) => setExpertName(event.target.value)}
                />
              </div>

              <div>
                <Label htmlFor="notes">Review notes</Label>
                <textarea
                  id="notes"
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-input bg-card px-2.5 py-2 text-sm"
                  placeholder="What you saw, and what the farmer should do"
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </div>

              <div className="flex gap-2">
                <Button type="submit" disabled={saving} className="flex-1">
                  {saving ? (
                    <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <ShieldCheck size={15} aria-hidden="true" />
                  )}
                  Save decision
                </Button>
                <Button type="button" variant="ghost" onClick={() => setSelected(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {applications && applications.length > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Expert access requests</CardTitle>
            <CardDescription>
              Farmers asking to review reports. Approving gives the account the expert
              role; they must sign in again for it to take effect.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {applications.map((application) => (
              <div key={application.id} className="rounded-lg border border-border p-2.5">
                <p className="text-xs font-semibold">
                  {application.applicant_name || `User ${application.user_id}`}
                  {application.applicant_email ? (
                    <span className="ml-1 font-normal text-muted-foreground">
                      · {application.applicant_email}
                    </span>
                  ) : null}
                </p>
                <p className="mt-1 text-2xs leading-relaxed text-secondary-foreground">
                  {application.justification}
                </p>
                {application.affiliation || application.region_name ? (
                  <p className="mt-1 text-2xs text-muted-foreground">
                    {[application.affiliation, application.region_name].filter(Boolean).join(' · ')}
                  </p>
                ) : null}
                <input
                  className="mt-2 w-full rounded-md border border-input bg-card px-2 py-1.5 text-2xs"
                  placeholder="Review note (optional, shown to the applicant)"
                  value={decisionNotes}
                  onChange={(event) => setDecisionNotes(event.target.value)}
                />
                <div className="mt-2 flex gap-1.5">
                  <Button
                    size="sm"
                    className="flex-1"
                    disabled={deciding === application.id}
                    onClick={() => decide(application.id, 'approve')}
                  >
                    {deciding === application.id ? (
                      <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <ShieldCheck size={13} aria-hidden="true" />
                    )}
                    Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="flex-1"
                    disabled={deciding === application.id}
                    onClick={() => decide(application.id, 'reject')}
                  >
                    Reject
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {!loading && queue.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-1 py-10 text-center">
            <ClipboardList size={22} className="text-muted-foreground" aria-hidden="true" />
            <p className="text-sm font-semibold">Nothing waiting for review</p>
            <p className="text-xs text-muted-foreground">
              Reports appear here when the model is unsure, the photo does not match the
              selected crop, or the farmer asks for a second opinion.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <div className="space-y-2">
        {queue.map((report) => (
          <Card key={report.id}>
            <CardContent className="space-y-2 p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">
                    {conditionOf(report.predicted_disease)}
                  </p>
                  <p className="text-2xs text-muted-foreground">
                    {report.crop_type} · {report.reference || `#${report.id}`} ·{' '}
                    {report.region_name || 'location not named'}
                  </p>
                </div>
                <StatusBadge status={report.status} />
              </div>
              <ConfidenceMeter value={report.confidence} />
              <div className="flex items-center justify-between gap-2">
                <span className="text-2xs text-muted-foreground">
                  {when(report.created_at)}
                </span>
                <Button size="sm" variant="secondary" onClick={() => setSelected(report)}>
                  Review
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
