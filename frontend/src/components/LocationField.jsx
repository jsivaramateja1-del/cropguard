import { useCallback, useState } from 'react'
import { Crosshair, Loader2, MapPin, TriangleAlert } from 'lucide-react'
import { Button } from './ui/button.jsx'
import { Badge } from './ui/badge.jsx'
import { Alert } from './ui/alert.jsx'
import { Input } from './ui/input.jsx'

/**
 * Location capture.
 *
 * GPS is requested on demand rather than on mount: a permission prompt that appears
 * before the farmer understands why is usually denied, and denial is sticky. The manual
 * fallback exists because GPS fails in sheds and valleys — and the API needs coordinates
 * either way.
 */
export default function LocationField({ value, onChange, disabled }) {
  const [status, setStatus] = useState(value ? 'ready' : 'idle')
  const [error, setError] = useState(null)
  const [accuracy, setAccuracy] = useState(null)

  const locate = useCallback(() => {
    if (!('geolocation' in navigator)) {
      setStatus('unsupported')
      setError('This browser cannot read your location. Enter the coordinates below.')
      return
    }
    setStatus('locating')
    setError(null)

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude, accuracy: acc } = position.coords
        setAccuracy(acc)
        setStatus('ready')
        onChange({
          latitude: Number(latitude.toFixed(6)),
          longitude: Number(longitude.toFixed(6)),
          source: 'gps',
        })
      },
      (geoError) => {
        setStatus(geoError.code === geoError.PERMISSION_DENIED ? 'denied' : 'error')
        setError(
          geoError.code === geoError.PERMISSION_DENIED
            ? 'Location permission was denied. Allow it in your browser settings, or enter the coordinates below.'
            : 'Your location could not be read. Move outdoors and try again, or enter the coordinates below.',
        )
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
    )
  }, [onChange])

  function updateManual(field, raw) {
    const parsed = Number(raw)
    const next = {
      latitude: field === 'latitude' ? (raw === '' ? null : parsed) : (value?.latitude ?? null),
      longitude: field === 'longitude' ? (raw === '' ? null : parsed) : (value?.longitude ?? null),
      source: 'manual',
    }
    setAccuracy(null)
    setStatus(next.latitude != null && next.longitude != null ? 'ready' : 'manual')
    onChange(next)
  }

  const hasCoords =
    value?.latitude != null &&
    value?.longitude != null &&
    Number.isFinite(value.latitude) &&
    Number.isFinite(value.longitude)

  return (
    <div className="space-y-2">
      <span className="block text-sm font-semibold text-foreground">Field location</span>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={locate}
          disabled={disabled || status === 'locating'}
        >
          {status === 'locating' ? (
            <Loader2 size={17} className="animate-spin" aria-hidden="true" />
          ) : (
            <Crosshair size={17} aria-hidden="true" />
          )}
          {status === 'locating' ? 'Locating…' : hasCoords ? 'Update location' : 'Use my location'}
        </Button>

        {hasCoords ? (
          <Badge variant="low" className="tabular px-2.5 py-1">
            <MapPin size={12} aria-hidden="true" />
            {value.latitude.toFixed(4)}, {value.longitude.toFixed(4)}
            {accuracy ? ` · ±${Math.round(accuracy)} m` : ''}
          </Badge>
        ) : null}
      </div>

      {error ? (
        <Alert variant="warning">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </Alert>
      ) : null}

      <details className="group rounded-lg border border-border bg-card px-3 py-2">
        <summary className="cursor-pointer list-none text-xs font-semibold text-primary marker:hidden">
          <span className="group-open:hidden">Enter coordinates manually</span>
          <span className="hidden group-open:inline">Hide manual entry</span>
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <label className="block">
            <span className="mb-1 block text-2xs font-semibold text-muted-foreground">Latitude</span>
            <Input
              type="number"
              step="0.0001"
              min="-90"
              max="90"
              inputMode="decimal"
              placeholder="16.5062"
              className="tabular h-10 text-sm"
              value={value?.latitude ?? ''}
              onChange={(event) => updateManual('latitude', event.target.value)}
              disabled={disabled}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-2xs font-semibold text-muted-foreground">Longitude</span>
            <Input
              type="number"
              step="0.0001"
              min="-180"
              max="180"
              inputMode="decimal"
              placeholder="80.6480"
              className="tabular h-10 text-sm"
              value={value?.longitude ?? ''}
              onChange={(event) => updateManual('longitude', event.target.value)}
              disabled={disabled}
            />
          </label>
        </div>
        <p className="mt-2 text-2xs leading-relaxed text-muted-foreground">
          Weather risk needs real coordinates. Extremes such as −90, −180 return no weather data.
        </p>
      </details>
    </div>
  )
}
