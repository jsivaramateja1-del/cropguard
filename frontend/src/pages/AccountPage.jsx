import { useEffect, useState } from 'react'
import {
  CheckCircle2,
  Clock,
  LogIn,
  LogOut,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  UserRound,
  XCircle,
} from 'lucide-react'
import {
  changePassword,
  deleteAccount,
  fetchMe,
  fetchMyExpertRequests,
  getAuthToken,
  loginAccount,
  logoutAccount,
  registerAccount,
  requestExpertAccess,
} from '../api.js'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card.jsx'
import { Button } from '../components/ui/button.jsx'
import { Badge } from '../components/ui/badge.jsx'
import { Input } from '../components/ui/input.jsx'
import { Label } from '../components/ui/label.jsx'
import { Alert } from '../components/ui/alert.jsx'

export default function AccountPage() {
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(true)
  const [mode, setMode] = useState('signin') // signin | register
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  // Change password
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  // Delete
  const [deletePassword, setDeletePassword] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  // Expert access request
  const [applications, setApplications] = useState([])
  const [justification, setJustification] = useState('')
  const [affiliation, setAffiliation] = useState('')
  const [requestRegion, setRequestRegion] = useState('')
  const [requesting, setRequesting] = useState(false)

  useEffect(() => {
    let cancelled = false
    if (!getAuthToken()) {
      setChecking(false)
      return () => {
        cancelled = true
      }
    }
    fetchMe()
      .then((data) => {
        if (!cancelled) setUser(data)
        if (data.role === 'farmer') {
          fetchMyExpertRequests()
            .then((rows) => {
              if (!cancelled) setApplications(rows)
            })
            .catch(() => {})
        }
      })
      .catch(() => {
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setChecking(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function submitAuth(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const data =
        mode === 'register'
          ? await registerAccount({ email, password, displayName })
          : await loginAccount({ email, password })
      setUser(data.user)
      setPassword('')
      setNotice(
        mode === 'register'
          ? 'Account created. Your reports will be saved to it.'
          : 'Signed in.',
      )
    } catch (authError) {
      setError(authError)
    } finally {
      setBusy(false)
    }
  }

  async function submitExpertRequest(event) {
    event.preventDefault()
    setRequesting(true)
    setError(null)
    setNotice(null)
    try {
      await requestExpertAccess({ justification, affiliation, regionName: requestRegion })
      const rows = await fetchMyExpertRequests()
      setApplications(rows)
      setJustification('')
      setAffiliation('')
      setRequestRegion('')
      setNotice('Request sent. An administrator will review it.')
    } catch (requestError) {
      setError(requestError)
    } finally {
      setRequesting(false)
    }
  }

  async function signOut() {
    await logoutAccount()
    setUser(null)
    setNotice('Signed out on this device. Any other sessions were ended too.')
  }

  async function submitPassword(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await changePassword({ currentPassword, newPassword })
      setUser(null)
      setCurrentPassword('')
      setNewPassword('')
      setNotice('Password changed. Every session was signed out — sign in again.')
    } catch (passwordError) {
      setError(passwordError)
    } finally {
      setBusy(false)
    }
  }

  async function submitDelete(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await deleteAccount(deletePassword)
      setUser(null)
      setDeletePassword('')
      setConfirmDelete(false)
      setNotice('Account deleted. Your reports were kept for the outbreak map, anonymised.')
    } catch (deleteError) {
      setError(deleteError)
    } finally {
      setBusy(false)
    }
  }

  if (checking) {
    return <p className="py-12 text-center text-sm text-muted-foreground">Checking session…</p>
  }

  // ------------------------------------------------------------- signed out
  if (!user) {
    return (
      <div className="mx-auto max-w-md space-y-4">
        <header className="space-y-1">
          <h1 className="text-xl font-bold">
            {mode === 'register' ? 'Create an account' : 'Sign in'}
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            An account keeps your reports together so you can reopen them later without
            saving an access code each time. CropGuard works without one too.
          </p>
        </header>

        {notice ? (
          <Alert>
            <CheckCircle2 size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
            <p>{notice}</p>
          </Alert>
        ) : null}

        {error ? (
          <Alert variant="destructive">
            <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div>
              <p className="font-semibold">That did not work</p>
              <p className="mt-0.5">{error.message}</p>
            </div>
          </Alert>
        ) : null}

        <Card>
          <CardContent className="pt-4">
            <form onSubmit={submitAuth} className="space-y-3">
              {mode === 'register' ? (
                <div>
                  <Label htmlFor="display-name">Your name</Label>
                  <Input
                    id="display-name"
                    className="mt-1"
                    autoComplete="name"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                </div>
              ) : null}
              <div>
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  className="mt-1"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  className="mt-1"
                  autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                {mode === 'register' ? (
                  <p className="mt-1 text-2xs text-muted-foreground">At least 10 characters.</p>
                ) : null}
              </div>
              <Button type="submit" className="w-full" disabled={busy}>
                <LogIn size={15} aria-hidden="true" />
                {mode === 'register' ? 'Create account' : 'Sign in'}
              </Button>
            </form>

            <button
              type="button"
              className="mt-3 w-full text-center text-2xs text-muted-foreground underline"
              onClick={() => {
                setMode(mode === 'register' ? 'signin' : 'register')
                setError(null)
              }}
            >
              {mode === 'register'
                ? 'I already have an account'
                : 'Create an account instead'}
            </button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // -------------------------------------------------------------- signed in
  return (
    <div className="mx-auto max-w-md space-y-4">
      <header className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <h1 className="text-xl font-bold">Your account</h1>
          <p className="text-sm text-muted-foreground">{user.email}</p>
        </div>
        <Badge variant={user.role === 'farmer' ? 'muted' : 'low'}>
          {user.role === 'farmer' ? 'Farmer' : user.role === 'expert' ? 'Expert' : 'Admin'}
        </Badge>
      </header>

      {notice ? (
        <Alert>
          <CheckCircle2 size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <p>{notice}</p>
        </Alert>
      ) : null}

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">That did not work</p>
            <p className="mt-0.5">{error.message}</p>
          </div>
        </Alert>
      ) : null}

      <Card>
        <CardContent className="space-y-2 pt-4">
          <p className="flex items-center gap-1.5 text-sm font-semibold">
            <UserRound size={15} aria-hidden="true" />
            {user.display_name}
          </p>
          <p className="text-2xs text-muted-foreground">
            Reports you submit while signed in are linked to this account and can be
            reopened without an access code.
          </p>
          <Button variant="secondary" className="w-full" onClick={signOut}>
            <LogOut size={15} aria-hidden="true" />
            Sign out everywhere
          </Button>
        </CardContent>
      </Card>

      {user.role === 'farmer' ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-1.5 text-sm">
              <ShieldCheck size={14} aria-hidden="true" />
              Expert access
            </CardTitle>
            <CardDescription>
              Ask to review farmers' reports as an agricultural expert. An administrator
              decides; approval does not happen automatically.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {applications.length > 0 ? (
              <div className="space-y-1.5">
                {applications.slice(0, 3).map((application) => (
                  <div
                    key={application.id}
                    className="flex items-start gap-2 rounded-lg bg-muted px-2.5 py-2"
                  >
                    {application.status === 'approved' ? (
                      <CheckCircle2 size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
                    ) : application.status === 'rejected' ? (
                      <XCircle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
                    ) : (
                      <Clock size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
                    )}
                    <div className="min-w-0">
                      <p className="text-2xs font-semibold capitalize">
                        Request #{application.id}: {application.status}
                      </p>
                      {application.review_notes ? (
                        <p className="text-2xs text-muted-foreground">
                          {application.review_notes}
                        </p>
                      ) : null}
                      {application.status === 'pending' ? (
                        <p className="text-2xs text-muted-foreground">
                          Waiting for an administrator.
                        </p>
                      ) : null}
                      {application.status === 'approved' ? (
                        <p className="text-2xs text-muted-foreground">
                          Sign out and back in to receive the expert role.
                        </p>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {applications.some((application) => application.status === 'pending') ? null : (
              <form onSubmit={submitExpertRequest} className="space-y-2">
                <div>
                  <Label htmlFor="justification">Why should you be recognised?</Label>
                  <textarea
                    id="justification"
                    rows={3}
                    className="mt-1 w-full rounded-lg border border-input bg-card px-2.5 py-2 text-sm"
                    placeholder="Your experience, the crops you advise on, who you work with…"
                    value={justification}
                    onChange={(event) => setJustification(event.target.value)}
                  />
                  <p className="mt-1 text-2xs text-muted-foreground">At least 20 characters.</p>
                </div>
                <div>
                  <Label htmlFor="affiliation">Organisation (optional)</Label>
                  <Input
                    id="affiliation"
                    className="mt-1"
                    value={affiliation}
                    onChange={(event) => setAffiliation(event.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="request-region">Region (optional)</Label>
                  <Input
                    id="request-region"
                    className="mt-1"
                    value={requestRegion}
                    onChange={(event) => setRequestRegion(event.target.value)}
                  />
                </div>
                <Button
                  type="submit"
                  variant="secondary"
                  className="w-full"
                  disabled={requesting || justification.trim().length < 20}
                >
                  {requesting ? 'Sending…' : 'Request expert access'}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-sm">
            <ShieldCheck size={14} aria-hidden="true" />
            Change password
          </CardTitle>
          <CardDescription>This signs you out on every device.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submitPassword} className="space-y-3">
            <div>
              <Label htmlFor="current-password">Current password</Label>
              <Input
                id="current-password"
                type="password"
                className="mt-1"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                className="mt-1"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
              />
            </div>
            <Button
              type="submit"
              variant="secondary"
              className="w-full"
              disabled={busy || !currentPassword || !newPassword}
            >
              Change password
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-sm text-destructive">
            <Trash2 size={14} aria-hidden="true" />
            Delete account
          </CardTitle>
          <CardDescription>
            Your reports stay on the outbreak map but your name is removed from them.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submitDelete} className="space-y-3">
            <label className="flex items-start gap-2 text-2xs">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={confirmDelete}
                onChange={(event) => setConfirmDelete(event.target.checked)}
              />
              I understand this cannot be undone.
            </label>
            <div>
              <Label htmlFor="delete-password">Confirm with your password</Label>
              <Input
                id="delete-password"
                type="password"
                className="mt-1"
                autoComplete="current-password"
                value={deletePassword}
                onChange={(event) => setDeletePassword(event.target.value)}
              />
            </div>
            <Button
              type="submit"
              variant="destructive"
              className="w-full"
              disabled={busy || !confirmDelete || !deletePassword}
            >
              Delete my account
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
