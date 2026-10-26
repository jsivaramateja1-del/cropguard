import { lazy, Suspense } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { Leaf, ShieldAlert } from 'lucide-react'
import DiagnosePage from './pages/DiagnosePage.jsx'
// Leaflet is ~150 kB; loading it only when the map is opened keeps the diagnosis
// page fast on a rural connection.
const OutbreakMapPage = lazy(() => import('./pages/OutbreakMapPage.jsx'))
import { Button } from './components/ui/button.jsx'

/**
 * Application shell.
 *
 * Only routes that work are linked. The outbreak map, expert portal and follow-up
 * pages arrive in later phases; dead links would make the app look finished when it
 * is not.
 */
function Layout({ children }) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/70">
        <div className="mx-auto flex w-full max-w-2xl items-center justify-between gap-3 px-4 py-2.5">
          <Link to="/" className="flex items-center gap-2.5 rounded-md">
            <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-primary text-primary-foreground shadow-sm">
              <Leaf size={19} strokeWidth={2.25} aria-hidden="true" />
            </span>
            <span className="leading-none">
              <span className="block text-[15px] font-bold tracking-tight text-foreground">
                CropGuard
              </span>
              <span className="mt-0.5 block text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                Crop disease screening
              </span>
            </span>
          </Link>

          <nav aria-label="Main" className="flex items-center gap-1">
            <NavLink to="/" end>
              {({ isActive }) => (
                <Button variant={isActive ? 'secondary' : 'ghost'} size="sm">
                  Diagnose
                </Button>
              )}
            </NavLink>
            <NavLink to="/outbreaks">
              {({ isActive }) => (
                <Button variant={isActive ? 'secondary' : 'ghost'} size="sm">
                  Outbreaks
                </Button>
              )}
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 px-4 pb-28 pt-4 sm:pb-8">{children}</main>

      <footer className="border-t border-border bg-card">
        <div className="mx-auto w-full max-w-2xl space-y-3 px-4 py-5 text-2xs leading-relaxed text-muted-foreground">
          <p className="flex items-start gap-2">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-muted-foreground/70" aria-hidden="true" />
            <span>
              CropGuard is a decision-support prototype. AI results are a{' '}
              <strong className="font-semibold text-foreground">potential diagnosis</strong> and
              an early-screening aid, not a replacement for an agricultural expert. Confirm with
              your local extension officer or Krishi Vigyan Kendra before applying any treatment.
            </span>
          </p>
          <p>
            Weather data by{' '}
            <a
              href="https://open-meteo.com/"
              target="_blank"
              rel="noreferrer noopener"
              className="font-medium text-primary underline decoration-primary/30 underline-offset-2 hover:decoration-primary"
            >
              Open-Meteo.com
            </a>{' '}
            (CC BY 4.0), with MET Norway as fallback. Interface components built on Radix UI and
            shadcn/ui (MIT).
          </p>
        </div>
      </footer>
    </div>
  )
}

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<DiagnosePage />} />
        <Route
          path="/outbreaks"
          element={
            <Suspense
              fallback={
                <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                  Loading the map…
                </div>
              }
            >
              <OutbreakMapPage />
            </Suspense>
          }
        />
        <Route
          path="*"
          element={
            <div className="rounded-lg border border-border bg-card p-6 text-center shadow-card">
              <h1 className="text-lg">Page not found</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">
                That page does not exist. Start from the diagnosis screen.
              </p>
              <Button asChild className="mt-4">
                <Link to="/">Go to Diagnose</Link>
              </Button>
            </div>
          }
        />
      </Routes>
    </Layout>
  )
}
