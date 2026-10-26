import { useCallback, useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from 'react-leaflet'
import { Filter, Loader2, MapPin, RefreshCw, TriangleAlert } from 'lucide-react'
import 'leaflet/dist/leaflet.css'
import { fetchOutbreaks } from '../api.js'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card.jsx'
import { Button } from '../components/ui/button.jsx'
import { Badge } from '../components/ui/badge.jsx'
import { Alert } from '../components/ui/alert.jsx'
import { Input } from '../components/ui/input.jsx'
import { CROPS } from '../components/CropPicker.jsx'

// Colour follows the same severity scale used everywhere else in the app.
const RISK_STYLE = {
  Low: { color: '#2f6b3f', label: 'Low' },
  Moderate: { color: '#b5830f', label: 'Moderate' },
  High: { color: '#d2691e', label: 'High' },
  Critical: { color: '#b3372a', label: 'Critical' },
}
const UNKNOWN_RISK = { color: '#6b7280', label: 'No weather risk' }

const CENTRE = [16.5062, 80.648]

function conditionOf(label = '') {
  return (label.split('___')[1] || label).replace(/_/g, ' ')
}

/** Recentre when the filtered points change, so a filter never leaves you looking at nothing. */
function FitToPoints({ points }) {
  const map = useMap()
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) {
      map.setView([points[0].latitude, points[0].longitude], 9)
      return
    }
    const bounds = points.map((point) => [point.latitude, point.longitude])
    map.fitBounds(bounds, { padding: [32, 32], maxZoom: 10 })
  }, [points, map])
  return null
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
      {Object.entries(RISK_STYLE).map(([level, style]) => (
        <span key={level} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: style.color }}
            aria-hidden="true"
          />
          {style.label}
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: UNKNOWN_RISK.color }}
          aria-hidden="true"
        />
        {UNKNOWN_RISK.label}
      </span>
    </div>
  )
}

export default function OutbreakMapPage() {
  const [points, setPoints] = useState([])
  const [summary, setSummary] = useState(null)
  const [attribution, setAttribution] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [crop, setCrop] = useState('')
  const [risk, setRisk] = useState('')
  const [disease, setDisease] = useState('')
  const [region, setRegion] = useState('')
  const [days, setDays] = useState(90)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchOutbreaks({
        cropType: crop || undefined,
        risk: risk || undefined,
        disease: disease || undefined,
        regionName: region.trim() || undefined,
        days,
      })
      setPoints(data.points || [])
      setSummary(data.summary)
      setAttribution(data.attribution || '')
    } catch (loadError) {
      setError(loadError)
      setPoints([])
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }, [crop, risk, disease, region, days])

  useEffect(() => {
    load()
  }, [load])

  const diseases = useMemo(
    () => Object.keys(summary?.by_disease || {}).sort(),
    [summary],
  )

  const totalReports = summary?.total_reports ?? 0

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-xl font-bold">Outbreak map</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Where disease is being reported, from farmer submissions in the selected period.
          Locations are rounded to about a kilometre and no farmer details are shown.
        </p>
      </header>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-sm">
            <Filter size={14} aria-hidden="true" />
            Filters
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="mb-1 block text-2xs font-semibold text-muted-foreground">Crop</span>
              <select
                aria-label="Filter by crop"
                className="h-10 w-full rounded-lg border border-input bg-card px-2 text-sm"
                value={crop}
                onChange={(event) => setCrop(event.target.value)}
              >
                <option value="">All crops</option>
                {CROPS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-2xs font-semibold text-muted-foreground">Risk</span>
              <select
                aria-label="Filter by risk"
                className="h-10 w-full rounded-lg border border-input bg-card px-2 text-sm"
                value={risk}
                onChange={(event) => setRisk(event.target.value)}
              >
                <option value="">Any risk</option>
                {Object.keys(RISK_STYLE).map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-2xs font-semibold text-muted-foreground">
                Disease
              </span>
              <select
                className="h-10 w-full rounded-lg border border-input bg-card px-2 text-sm"
                value={disease}
                onChange={(event) => setDisease(event.target.value)}
              >
                <option value="">All diseases</option>
                {diseases.map((label) => (
                  <option key={label} value={label}>
                    {conditionOf(label)}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-2xs font-semibold text-muted-foreground">
                Period
              </span>
              <select
                className="h-10 w-full rounded-lg border border-input bg-card px-2 text-sm"
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
              >
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
                <option value={365}>Last year</option>
              </select>
            </label>
          </div>

          <div className="flex gap-2">
            <Input
              className="h-10 text-sm"
              placeholder="Region, e.g. Vijayawada"
              value={region}
              onChange={(event) => setRegion(event.target.value)}
            />
            <Button variant="secondary" onClick={load} disabled={loading} aria-label="Refresh">
              {loading ? (
                <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw size={16} aria-hidden="true" />
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Could not load the outbreak map</p>
            <p className="mt-0.5">{error.message}</p>
          </div>
        </Alert>
      ) : null}

      <Card className="overflow-hidden">
        <div className="relative">
          <MapContainer
            center={CENTRE}
            zoom={6}
            scrollWheelZoom={false}
            style={{ height: '340px', width: '100%' }}
            className="z-0"
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <FitToPoints points={points} />
            {points.map((point, index) => {
              const style = RISK_STYLE[point.weather_risk_level] || UNKNOWN_RISK
              const radius = Math.min(22, 7 + Math.sqrt(point.reports) * 4)
              return (
                <CircleMarker
                  key={`${point.latitude}-${point.longitude}-${point.predicted_disease}-${index}`}
                  center={[point.latitude, point.longitude]}
                  radius={radius}
                  pathOptions={{
                    color: style.color,
                    fillColor: style.color,
                    fillOpacity: 0.55,
                    weight: 1.5,
                  }}
                >
                  <Popup>
                    <div className="space-y-1 text-xs">
                      <p className="text-sm font-semibold">
                        {point.crop_type} · {conditionOf(point.predicted_disease)}
                      </p>
                      <p>
                        <strong>{point.reports}</strong>{' '}
                        {point.reports === 1 ? 'report' : 'reports'} at this location
                      </p>
                      <p>Weather risk: {point.weather_risk_level || 'not available'}</p>
                      <p>Latest status: {point.status.replace(/_/g, ' ')}</p>
                      <p className="text-[10px] text-gray-500">
                        Latest report: {new Date(point.latest_at).toLocaleDateString('en-IN')}
                      </p>
                    </div>
                  </Popup>
                </CircleMarker>
              )
            })}
          </MapContainer>

          {!loading && points.length === 0 ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/80 p-6 text-center">
              <div>
                <MapPin size={22} className="mx-auto mb-1.5 text-muted-foreground" aria-hidden="true" />
                <p className="text-sm font-semibold">No reports in this period</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {totalReports === 0
                    ? 'Nothing has been submitted yet, or the filters are too narrow.'
                    : 'Try a longer period or clear a filter.'}
                </p>
              </div>
            </div>
          ) : null}
        </div>

        <CardContent className="space-y-3 p-3.5">
          <Legend />
          <div className="flex flex-wrap items-center gap-2 text-2xs text-muted-foreground">
            <Badge variant="muted" className="tabular">
              {totalReports} reports
            </Badge>
            <Badge variant="muted" className="tabular">
              {summary?.points ?? 0} locations
            </Badge>
            <span>Last {summary?.period_days ?? days} days</span>
          </div>
          {attribution ? <p className="text-2xs text-muted-foreground">{attribution}</p> : null}
        </CardContent>
      </Card>

      {summary && totalReports > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Hotspots</CardTitle>
            <CardDescription>Locations with the most reports in this period</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {summary.hotspots.slice(0, 6).map((spot, index) => (
              <div
                key={`${spot.latitude}-${spot.longitude}-${index}`}
                className="flex items-center justify-between gap-2 rounded-lg bg-muted px-2.5 py-2"
              >
                <span className="min-w-0 truncate text-xs text-secondary-foreground">
                  {spot.region_name || `${spot.latitude.toFixed(2)}, ${spot.longitude.toFixed(2)}`}
                </span>
                <Badge variant="low" className="tabular shrink-0">
                  {spot.reports}
                </Badge>
              </div>
            ))}
            <div className="grid grid-cols-2 gap-2 pt-1">
              {Object.entries(summary.by_crop).map(([name, count]) => (
                <div key={name} className="rounded-lg bg-muted px-2.5 py-2">
                  <p className="text-2xs text-muted-foreground">{name}</p>
                  <p className="tabular text-sm font-semibold">{count}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
