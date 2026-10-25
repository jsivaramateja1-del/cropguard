import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Leaf, ShieldAlert } from 'lucide-react'
import DiagnosePage from './pages/DiagnosePage.jsx'

/**
 * Application shell.
 *
 * Only routes that actually work are linked. The outbreak map, expert portal and
 * follow-up pages arrive in later phases; adding them here as dead links would make
 * the app look finished when it is not.
 */
function Layout({ children }) {
  const location = useLocation()

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-soil-200 bg-soil-50/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-2xl items-center justify-between gap-3 px-4 py-3">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-leaf-700 text-white">
              <Leaf size={20} strokeWidth={2.2} aria-hidden="true" />
            </span>
            <span className="leading-tight">
              <span className="block text-base font-bold text-leaf-900">CropGuard</span>
              <span className="block text-[11px] font-medium uppercase tracking-wide text-soil-600">
                Crop disease screening
              </span>
            </span>
          </Link>

          <nav className="flex items-center gap-1" aria-label="Main">
            <NavLink
              to="/"
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm font-semibold ${
                  isActive || location.pathname === '/'
                    ? 'bg-leaf-100 text-leaf-800'
                    : 'text-leaf-700 hover:bg-leaf-50'
                }`
              }
            >
              Diagnose
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-5">{children}</main>

      <footer className="border-t border-soil-200 bg-white">
        <div className="mx-auto w-full max-w-2xl space-y-3 px-4 py-5 text-xs leading-relaxed text-soil-600">
          <p className="flex items-start gap-2">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-soil-500" aria-hidden="true" />
            <span>
              CropGuard is a decision-support prototype. AI results are a{' '}
              <strong className="font-semibold text-soil-700">potential diagnosis</strong> and
              an early-screening aid, not a replacement for an agricultural expert. Confirm
              with your local extension officer or Krishi Vigyan Kendra before applying any
              treatment.
            </span>
          </p>
          <p>
            Weather data by{' '}
            <a
              href="https://open-meteo.com/"
              target="_blank"
              rel="noreferrer noopener"
              className="font-medium text-leaf-700 underline decoration-leaf-300 underline-offset-2 hover:text-leaf-900"
            >
              Open-Meteo.com
            </a>{' '}
            (CC BY 4.0), with MET Norway as fallback.
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
          path="*"
          element={
            <div className="card p-6 text-center">
              <h1 className="text-lg">Page not found</h1>
              <p className="mt-2 text-sm text-soil-600">
                That page does not exist. Start from the diagnosis screen.
              </p>
              <Link to="/" className="btn-primary mt-4">
                Go to Diagnose
              </Link>
            </div>
          }
        />
      </Routes>
    </Layout>
  )
}
