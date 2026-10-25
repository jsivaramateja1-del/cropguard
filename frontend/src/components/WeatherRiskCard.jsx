import { CloudRain, Droplets, Info, Thermometer, Wind } from 'lucide-react'

const LEVEL_STYLES = {
  Low: { chip: 'bg-leaf-100 text-leaf-800', bar: 'bg-leaf-500' },
  Moderate: { chip: 'bg-amber-100 text-soil-800', bar: 'bg-straw-400' },
  High: { chip: 'bg-orange-100 text-soil-900', bar: 'bg-straw-600' },
  Critical: { chip: 'bg-red-100 text-alert-600', bar: 'bg-alert-500' },
}

function Metric({ Icon, label, value, unit }) {
  if (value == null) return null
  return (
    <div className="flex items-center gap-2 rounded-lg bg-soil-50 px-2.5 py-2">
      <Icon size={15} className="shrink-0 text-soil-500" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-soil-600">{label}</p>
        <p className="text-sm font-semibold text-leaf-900">
          {value}
          {unit ? <span className="text-xs font-normal text-soil-600"> {unit}</span> : null}
        </p>
      </div>
    </div>
  )
}

/**
 * Weather-driven risk.
 *
 * Shows the level *and* the factors that produced it, because the score is a
 * deterministic table rather than a model — a farmer can check the reasoning. When
 * weather is unavailable the card says so plainly instead of showing an empty level.
 */
export default function WeatherRiskCard({ weather, risk }) {
  const available = risk?.available && weather
  const level = risk?.level || 'Low'
  const styles = LEVEL_STYLES[level] || LEVEL_STYLES.Low
  const score = risk?.score ?? 0
  const maxScore = risk?.max_score || 9

  return (
    <section className="card space-y-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold">Weather disease risk</h3>
          <p className="text-[11px] text-soil-600">
            {available ? 'Current conditions at this location' : 'Not available for this report'}
          </p>
        </div>
        {available ? (
          <span className={`chip ${styles.chip}`}>{level}</span>
        ) : (
          <span className="chip bg-soil-200 text-soil-700">Unavailable</span>
        )}
      </div>

      {available ? (
        <>
          <div>
            <div className="mb-1 flex items-center justify-between text-xs text-soil-600">
              <span>
                Score {score} of {maxScore}
              </span>
              {risk.advisory_adjustment > 0 && (
                <span>+{risk.advisory_adjustment} from this disease</span>
              )}
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-soil-200">
              <div
                className={`h-full rounded-full ${styles.bar}`}
                style={{ width: `${Math.min(100, (score / maxScore) * 100)}%` }}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Metric Icon={Thermometer} label="Temperature" value={weather.temperature_c} unit="°C" />
            <Metric Icon={Droplets} label="Humidity" value={Math.round(weather.humidity_pct)} unit="%" />
            <Metric Icon={CloudRain} label="Rainfall" value={weather.precipitation_mm} unit="mm" />
            <Metric Icon={Wind} label="Wind" value={weather.wind_kph} unit="km/h" />
          </div>

          {risk.factors?.length > 0 && (
            <ul className="space-y-1 border-t border-soil-200 pt-2">
              {risk.factors
                .filter((factor) => factor.points > 0)
                .map((factor) => (
                  <li key={factor.name} className="flex items-start justify-between gap-2 text-xs">
                    <span className="text-soil-700">{factor.detail}</span>
                    <span className="shrink-0 font-semibold text-soil-800">+{factor.points}</span>
                  </li>
                ))}
            </ul>
          )}

          <p className="text-[11px] text-soil-500">
            {weather.cached ? 'Cached reading · ' : ''}
            Source: {weather.source === 'met.no' ? 'MET Norway' : 'Open-Meteo'}
          </p>
        </>
      ) : (
        <p className="flex items-start gap-2 rounded-lg bg-soil-100 px-3 py-2 text-xs leading-relaxed text-soil-700">
          <Info size={14} className="mt-0.5 shrink-0 text-soil-500" aria-hidden="true" />
          <span>
            {risk?.reason ||
              'Weather could not be retrieved, so no weather risk is shown. The diagnosis above is unaffected.'}
          </span>
        </p>
      )}
    </section>
  )
}
