import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2, KeyRound, MailCheck, TriangleAlert } from 'lucide-react'
import { resetPassword } from '../api.js'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card.jsx'
import { Button } from '../components/ui/button.jsx'
import { Input } from '../components/ui/input.jsx'
import { Label } from '../components/ui/label.jsx'
import { Alert } from '../components/ui/alert.jsx'

const MIN_LENGTH = 10

export default function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    document.title = 'Choose a new password · CropGuard'
  }, [])

  async function submit(event) {
    event.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError({ message: 'The two passwords do not match.' })
      return
    }
    setBusy(true)
    try {
      await resetPassword({ token, newPassword: password })
      setDone(true)
    } catch (resetError) {
      setError(resetError)
    } finally {
      setBusy(false)
    }
  }

  // ------------------------------------------------------------- no token
  if (!token) {
    return (
      <div className="mx-auto max-w-md space-y-4">
        <header className="space-y-1">
          <h1 className="text-xl font-bold">Choose a new password</h1>
        </header>
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">This link is missing its token</p>
            <p className="mt-0.5">
              Open the link from your email exactly as it arrived. If it has expired, request
              a new one.
            </p>
          </div>
        </Alert>
        <Button asChild variant="secondary" className="w-full">
          <Link to="/account">Back to sign in</Link>
        </Button>
      </div>
    )
  }

  // ----------------------------------------------------------------- done
  if (done) {
    return (
      <div className="mx-auto max-w-md space-y-4">
        <header className="space-y-1">
          <h1 className="text-xl font-bold">Password changed</h1>
        </header>
        <Card>
          <CardContent className="space-y-3 pt-5 text-center">
            <CheckCircle2 size={26} className="mx-auto text-primary" aria-hidden="true" />
            <p className="text-sm text-secondary-foreground">
              Your password has been updated. Every device that was signed in has been
              signed out, so sign in again with the new password.
            </p>
            <Button asChild className="w-full">
              <Link to="/account">Sign in</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // ------------------------------------------------------------------ form
  const tooShort = password.length > 0 && password.length < MIN_LENGTH
  const mismatch = confirm.length > 0 && password !== confirm

  return (
    <div className="mx-auto max-w-md space-y-4">
      <header className="space-y-1">
        <h1 className="text-xl font-bold">Choose a new password</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Pick something you have not used elsewhere. Signing in again is required on every
          device afterwards.
        </p>
      </header>

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Could not change the password</p>
            <p className="mt-0.5">{error.message}</p>
          </div>
        </Alert>
      ) : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-sm">
            <KeyRound size={14} aria-hidden="true" />
            New password
          </CardTitle>
          <CardDescription>At least {MIN_LENGTH} characters.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-3">
            <div>
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                className="mt-1"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-describedby={tooShort ? 'password-hint' : undefined}
              />
              {tooShort ? (
                <p id="password-hint" className="mt-1 text-2xs text-destructive">
                  {MIN_LENGTH - password.length} more characters needed.
                </p>
              ) : null}
            </div>

            <div>
              <Label htmlFor="confirm-password">Confirm password</Label>
              <Input
                id="confirm-password"
                type="password"
                className="mt-1"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                aria-describedby={mismatch ? 'confirm-hint' : undefined}
              />
              {mismatch ? (
                <p id="confirm-hint" className="mt-1 text-2xs text-destructive">
                  The two passwords do not match.
                </p>
              ) : null}
            </div>

            <Button
              type="submit"
              className="w-full"
              disabled={busy || password.length < MIN_LENGTH || password !== confirm}
            >
              <MailCheck size={15} aria-hidden="true" />
              {busy ? 'Saving…' : 'Set new password'}
            </Button>
          </form>
        </CardContent>
      </Card>

      <p className="text-center text-2xs text-muted-foreground">
        <Link to="/account" className="underline">
          Back to sign in
        </Link>
      </p>
    </div>
  )
}
