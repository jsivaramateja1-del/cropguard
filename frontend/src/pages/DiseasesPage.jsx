import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2, Search, Sprout, TriangleAlert } from 'lucide-react'
import { fetchDiseases } from '../api.js'
import { Card, CardContent } from '../components/ui/card.jsx'
import { Button } from '../components/ui/button.jsx'
import { Badge } from '../components/ui/badge.jsx'
import { Alert } from '../components/ui/alert.jsx'
import { Input } from '../components/ui/input.jsx'
import DiseaseCard from '../components/DiseaseCard.jsx'
import { diseaseImage } from '../lib/diseaseImage.js'
import { useT } from '../i18n/index.jsx'

export default function DiseasesPage() {
  const { t } = useT()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [crop, setCrop] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    document.title = 'Diseases CropGuard knows · CropGuard'
    let cancelled = false
    fetchDiseases()
      .then((payload) => {
        if (!cancelled) setData(payload)
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const visible = useMemo(() => {
    if (!data) return []
    const needle = query.trim().toLowerCase()
    return data.diseases.filter((disease) => {
      if (crop && disease.crop !== crop) return false
      if (!needle) return true
      return (
        disease.condition.toLowerCase().includes(needle) ||
        disease.summary.toLowerCase().includes(needle) ||
        disease.symptoms.some((symptom) => symptom.toLowerCase().includes(needle))
      )
    })
  }, [data, crop, query])

  const screenable = data?.diseases.filter((disease) => disease.supported_by_model).length ?? 0

  return (
    <div className="space-y-4">
      <header className="space-y-1.5">
        <h1 className="text-xl font-bold">{t('diseases.title')}</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t('diseases.subtitle')}
        </p>
      </header>

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Could not load the disease list</p>
            <p className="mt-0.5">{error.message}</p>
          </div>
        </Alert>
      ) : null}

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={20} className="animate-spin text-muted-foreground" aria-hidden="true" />
        </div>
      ) : null}

      {data ? (
        <>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                className="h-10 pl-9 text-sm"
                placeholder={t('diseases.search')}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={t('diseases.search')}
              />
            </div>
            <select
              className="h-10 rounded-lg border border-input bg-card px-2.5 text-sm"
              value={crop}
              onChange={(event) => setCrop(event.target.value)}
              aria-label="Filter by crop"
            >
              <option value="">{t('diseases.allCrops')}</option>
              {data.crops.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="muted" className="tabular">
              {visible.length} / {data.total}
            </Badge>
            <Badge variant="low">
              {screenable} {t('diseases.screenable')}
            </Badge>
            <Badge variant="muted">
              {data.total - screenable} {t('diseases.advisoryOnly')}
            </Badge>
          </div>

          {visible.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-1 py-12 text-center">
                <Sprout size={22} className="text-muted-foreground" aria-hidden="true" />
                <p className="text-sm font-semibold">Nothing matches that</p>
                <p className="text-xs text-muted-foreground">
                  Try a shorter search, or clear the crop filter.
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
              {visible.map((disease, index) => (
                <DiseaseCard
                  key={disease.label}
                  disease={disease}
                  index={index}
                  onSelect={setSelected}
                />
              ))}
            </div>
          )}
        </>
      ) : null}

      {selected ? (
        <Card>
          <CardContent className="space-y-2.5 p-4">
            {diseaseImage(selected.label) ? (
              <img
                src={diseaseImage(selected.label)}
                alt={`Reference photo: ${selected.condition} on ${selected.crop}`}
                className="h-40 w-full rounded-lg border border-border object-cover"
              />
            ) : null}
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {selected.crop}
                </p>
                <h2 className="text-base font-bold">{selected.condition}</h2>
              </div>
              {selected.supported_by_model ? (
                <Badge variant="low">{t('diseases.screenable')}</Badge>
              ) : (
                <Badge variant="muted">{t('diseases.advisoryOnly')}</Badge>
              )}
            </div>

            {selected.summary ? (
              <p className="text-sm leading-relaxed text-secondary-foreground">
                {selected.summary}
              </p>
            ) : null}

            {selected.symptoms.length ? (
              <div>
                <p className="mb-1 text-2xs font-bold uppercase tracking-wide text-muted-foreground">
                  What to look for
                </p>
                <ul className="space-y-1">
                  {selected.symptoms.map((symptom) => (
                    <li key={symptom} className="text-xs leading-relaxed text-muted-foreground">
                      • {symptom}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {selected.favorable_conditions.length ? (
              <div>
                <p className="mb-1 text-2xs font-bold uppercase tracking-wide text-muted-foreground">
                  Spreads when
                </p>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {selected.favorable_conditions.join(' · ')}
                </p>
              </div>
            ) : null}

            <div className="flex gap-2 pt-1">
              {selected.supported_by_model ? (
                <Button asChild className="flex-1">
                  <Link to="/">Check a leaf</Link>
                </Button>
              ) : (
                <p className="flex-1 text-2xs text-muted-foreground">
                  The photo model was not trained on this one, so a leaf photo cannot
                  diagnose it. An expert can.
                </p>
              )}
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Close
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
