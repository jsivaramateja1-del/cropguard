import { useEffect, useState } from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'

const STORAGE_KEY = 'cropguard.theme'
const OPTIONS = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
]

/**
 * Theme switcher: light, dark, or follow the device.
 *
 * Applied by toggling a class on <html> rather than by React state alone, so the colours
 * are correct before the first paint (see the inline script in index.html, which reads the
 * same localStorage key). Nothing here touches `window` during render, which keeps the
 * server-side render checks working.
 */
export function applyTheme(theme) {
  if (typeof document === 'undefined') return
  const prefersDark =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches

  const dark = theme === 'dark' || (theme === 'system' && prefersDark)
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
}

export function readStoredTheme() {
  if (typeof localStorage === 'undefined') return 'system'
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return OPTIONS.some((option) => option.value === stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

export default function ThemeToggle({ compact = false }) {
  const [theme, setTheme] = useState('system')
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setTheme(readStoredTheme())
    setReady(true)
  }, [])

  useEffect(() => {
    if (!ready) return
    applyTheme(theme)
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* private mode: the choice just will not persist */
    }
  }, [theme, ready])

  // Follow the device while "system" is selected.
  useEffect(() => {
    if (!ready || theme !== 'system' || typeof window === 'undefined') return undefined
    if (typeof window.matchMedia !== 'function') return undefined

    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => applyTheme('system')
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [theme, ready])

  return (
    <div
      role="group"
      aria-label="Colour theme"
      className="inline-flex items-center gap-0.5 rounded-full border border-border bg-card p-0.5"
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = ready && theme === value
        return (
          <button
            key={value}
            type="button"
            onClick={() => setTheme(value)}
            aria-pressed={active}
            aria-label={`${label} theme`}
            title={`${label} theme`}
            className={`flex h-7 items-center gap-1 rounded-full px-2 text-2xs font-semibold transition-colors ${
              active
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Icon size={13} aria-hidden="true" />
            {compact ? null : <span className="hidden sm:inline">{label}</span>}
          </button>
        )
      })}
    </div>
  )
}
