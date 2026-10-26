import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import translations from './translations.json'

/**
 * Interface languages.
 *
 * English plus the ten most widely spoken Indian languages. Names are written in their own
 * script, because a farmer looking for their language should recognise it without reading
 * English.
 *
 * Scope, stated honestly: the interface chrome, the diagnosis form and the disease
 * reference are translated. Advisory body text and the legal disclaimer stay in English —
 * they come from the knowledge base and need a plant pathologist's review before being
 * translated, not a machine's.
 */
export const LANGUAGES = [
  { code: 'en', native: 'English', english: 'English' },
  { code: 'hi', native: 'हिन्दी', english: 'Hindi' },
  { code: 'bn', native: 'বাংলা', english: 'Bengali' },
  { code: 'mr', native: 'मराठी', english: 'Marathi' },
  { code: 'te', native: 'తెలుగు', english: 'Telugu' },
  { code: 'ta', native: 'தமிழ்', english: 'Tamil' },
  { code: 'gu', native: 'ગુજરાતી', english: 'Gujarati' },
  { code: 'kn', native: 'ಕನ್ನಡ', english: 'Kannada' },
  { code: 'ml', native: 'മലയാളം', english: 'Malayalam' },
  { code: 'or', native: 'ଓଡ଼ିଆ', english: 'Odia' },
  { code: 'pa', native: 'ਪੰਜਾਬੀ', english: 'Punjabi' },
]

const STORAGE_KEY = 'cropguard.language'
const DEFAULT_LANGUAGE = 'en'

const LanguageContext = createContext({
  language: DEFAULT_LANGUAGE,
  setLanguage: () => {},
  t: (key) => key,
})

export function readStoredLanguage() {
  if (typeof localStorage === 'undefined') return DEFAULT_LANGUAGE
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return LANGUAGES.some((entry) => entry.code === stored) ? stored : DEFAULT_LANGUAGE
  } catch {
    return DEFAULT_LANGUAGE
  }
}

export function LanguageProvider({ children }) {
  const [language, setLanguage] = useState(DEFAULT_LANGUAGE)

  useEffect(() => {
    setLanguage(readStoredLanguage())
  }, [])

  useEffect(() => {
    if (typeof document !== 'undefined') document.documentElement.lang = language
    try {
      localStorage.setItem(STORAGE_KEY, language)
    } catch {
      /* private mode: the choice just will not persist */
    }
  }, [language])

  const value = useMemo(() => {
    const dictionary = translations[language] || translations[DEFAULT_LANGUAGE]
    return {
      language,
      setLanguage,
      /**
       * Translate a key. Falls back to English, then to the key itself — never to an empty
       * string, so a missing translation is visible rather than silently blank.
       */
      t: (key) => dictionary[key] ?? translations[DEFAULT_LANGUAGE][key] ?? key,
    }
  }, [language])

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useT() {
  return useContext(LanguageContext)
}

export function LanguageSwitcher({ compact = false }) {
  const { language, setLanguage } = useT()

  return (
    <label className="inline-flex items-center gap-1.5">
      <span className="sr-only">Language</span>
      <select
        value={language}
        onChange={(event) => setLanguage(event.target.value)}
        aria-label="Language"
        className={`h-7 rounded-full border border-border bg-card pl-2 pr-1 text-2xs font-semibold text-foreground ${
          compact ? 'max-w-[5.5rem]' : ''
        }`}
      >
        {LANGUAGES.map((entry) => (
          <option key={entry.code} value={entry.code} lang={entry.code}>
            {entry.native}
          </option>
        ))}
      </select>
    </label>
  )
}

export default LanguageSwitcher
