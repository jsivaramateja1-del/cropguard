import { useCallback, useState } from 'react'
import { Crosshair, Loader2, MapPin, TriangleAlert } from 'lucide-react'

/**
 * Location capture.
 *
 * GPS is requested on demand rather than on mount: a permission prompt that appears
 * before the farmer understands why is usually denied, and a denial is sticky. The
 * manual fallback exists because GPS fails in sheds, valleys and on tablets with
 * location disabled — and the API requires coordinates either way.
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
      latitude: field === 'latitude' ? (Number.isFinite(parsed) ? parsed : null) : value?.latitude ?? null,
      longitude: field === 'longitude' ? (Number.isFinite(parsed) ? parsed : null) : value?.longitude ?? null,
      source: 'manual',
    }
    setAccuracy(null)
    setStatus(next.latitude != null && next.longitude != null ? 'ready' : 'manual')
    onChange(next)
  }

  const hasCoords = value?.latitude != null && value?.longitude != null

  return (
    <div className="space-y-2">
      <span className="field-label">Field location</span>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={locate}
          disabled={disabled || status === 'locating'}
        >
          {status === 'locating' ? (
            <Loader2 size={18} className="animate-spin" aria-hidden="true" />
          ) : (
            <Crosshair size={18} aria-hidden="true" />
          )}
          {status === 'locating' ? 'Locating…' : hasCoords ? 'Update location' : 'Use my location'}
        </button>

        {hasCoords && (
          <span className="chip bg-leaf-100 text-leaf-800">
            <MapPin size={13} aria-hidden="true" />
            {value.latitude.toFixed(4)}, {value.longitude.toFixed(4)}
            {accuracy ? ` · ±${Math.round(accuracy)} m` : ''}
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-soil-700">
          <TriangleAlert size={14} className="mt-0.5 shrink-0 text-straw-600" aria-hidden="true" />
          {error}
        </p>
      )}

      <details className="rounded-lg border border-soil-200 bg-white px-3 py-2">
        <summary className="cursor-pointer text-xs font-semibold text-leaf-700">
          Enter coordinates manually
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <label className="block">
            <span className="mb-1 block text-[11px] font-semibold text-soil-600">Latitude</span>
            <input
              className="input py-2 text-sm"
              type="number"
              step="0.0001"
              min="-90"
              max="90"
              inputMode="decimal"
              placeholder="16.5062"
              value={value?.latitude ?? ''}
              onChange={(event) => updateManual('latitude', event.target.value)}
              disabled={disabled}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] font-semibold text-soil-600">Longitude</span>
            <input
              className="input py-2 text-sm"
              type="number"
              step="0.0001"
              min="-180"
              max="180"
              inputMode="decimal"
              placeholder="80.6480"
              value={value?.longitude ?? ''}
              onChange={(event) => updateManual('longitude', event.target.value)}
              disabled={disabled}
            />
          </label>
        </div>
        <p className="field-hint">
          Weather risk needs real coordinates. Extremes such as -90, -180 return no
          weather data.
        </p>
      </details>
    </div>
  )
}
