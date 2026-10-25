import { CloudRain, Droplets, Info, Thermometer, Wind } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from './ui/card.jsx'
import { Badge } from './ui/badge.jsx'
import { Progress } from './ui/progress.jsx'

const LEVEL_VARIANT = { Low: 'low', Moderate: 'moderate', High: 'high', Critical: 'critical' }
const LEVEL_BAR = {
  Low: 'bg-risk-low',
  Moderate: 'bg-risk-moderate',
  High: 'bg-risk-high',
  Critical: 'bg-risk-critical',
}

function Metric({ Icon, label, value, unit }) {
  if (value == null) return null
  return (
    <div className="flex items-center gap-2 rounded-lg bg-muted px-2.5 py-2">
      <Icon size={15} className="shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="tabular text-sm font-semibold text-foreground">
          {value}
          {unit ? <span className="ml-0.5 text-xs font-normal text-muted-foreground">{unit}</span> : null}
        </p>
      </div>
    </div>
  )
}

/**
 * Turn the weather numbers into a field action.
 *
 * Real agritech apps surface "spray windows" rather than raw meteorology: spraying
 * before rain wastes the chemical, spraying in wind causes drift, and a warm humid night
 * is itself an infection warning. The thresholds are stated so the advice is checkable.
 */
function sprayWindow(weather) {
  if (!weather) return null
  const wind = weather.wind_kph ?? 0
  const rain = weather.precipitation_mm ?? 0
  const chance = weather.precipitation_probability_pct ?? 0
  const humidity = weather.humidity_pct ?? 0
  const temp = weather.temperature_c ?? 0

  if (wind > 15) {
    return { tone: 'caution', text: `Wind at ${Math.round(wind)} km/h is too strong to spray — drift risk.` }
  }
  if (rain > 0 || chance >= 60) {
    return {
      tone: 'caution',
      text: `Rain now or likely (${Math.round(chance)}%) — a spray would wash off. Wait for a dry window.`,
    }
  }
  if (humidity >= 90 && temp >= 10 && temp <= 25) {
    return {
      tone: 'alert',
      text: 'Warm and very humid: a high infection window. Protect the crop now rather than waiting for symptoms.',
    }
  }
  return { tone: 'ok', text: 'Conditions are reasonable for field work and spraying.' }
}

export default function WeatherRiskCard({ weather, risk }) {
  const available = Boolean(risk?.available && weather)
  const level = risk?.level || 'Low'
  const score = risk?.score ?? 0
  const maxScore = risk?.max_score || 12
  const action = sprayWindow(weather)

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
        <div>
          <CardTitle className="text-sm">Weather disease risk</CardTitle>
          <CardDescription>
            {available ? 'Current conditions at this location' : 'Not available for this report'}
          </CardDescription>
        </div>
        {available ? (
          <Badge variant={LEVEL_VARIANT[level] || 'muted'}>{level}</Badge>
        ) : (
          <Badge variant="muted">Unavailable</Badge>
        )}
      </CardHeader>

      <CardContent className="space-y-3">
        {available ? (
          <>
            <div>
              <Progress
                value={(score / maxScore) * 100}
                barClassName={LEVEL_BAR[level]}
                aria-label="Weather risk score"
                label={{
                  text:
                    risk.advisory_adjustment > 0
                      ? `Score ${score} of ${maxScore} · +${risk.advisory_adjustment} for this disease`
                      : `Score ${score} of ${maxScore}`,
                  value: level,
                  className: 'text-foreground',
                }}
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <Metric Icon={Thermometer} label="Temperature" value={weather.temperature_c} unit="°C" />
              <Metric Icon={Droplets} label="Humidity" value={Math.round(weather.humidity_pct)} unit="%" />
              <Metric Icon={CloudRain} label="Rainfall" value={weather.precipitation_mm} unit="mm" />
              <Metric Icon={Wind} label="Wind" value={weather.wind_kph} unit="km/h" />
            </div>

            {action ? (
              <p
                className={
                  action.tone === 'alert'
                    ? 'rounded-lg bg-risk-critical/10 px-3 py-2 text-xs font-medium leading-relaxed text-risk-critical'
                    : action.tone === 'caution'
                      ? 'rounded-lg bg-accent/10 px-3 py-2 text-xs font-medium leading-relaxed text-accent-foreground'
                      : 'rounded-lg bg-primary/5 px-3 py-2 text-xs font-medium leading-relaxed text-primary'
                }
              >
                {action.text}
              </p>
            ) : null}

            {risk.factors?.length > 0 ? (
              <ul className="space-y-1 border-t border-border pt-2">
                {risk.factors
                  .filter((factor) => factor.points > 0)
                  .map((factor) => (
                    <li key={factor.name} className="flex items-start justify-between gap-2 text-xs">
                      <span className="text-muted-foreground">{factor.detail}</span>
                      <span className="tabular shrink-0 font-semibold text-foreground">
                        +{factor.points}
                      </span>
                    </li>
                  ))}
              </ul>
            ) : null}

            <p className="text-2xs text-muted-foreground">
              {weather.cached ? 'Cached reading · ' : ''}
              Source: {weather.source === 'met.no' ? 'MET Norway' : 'Open-Meteo'}
            </p>
          </>
        ) : (
          <p className="flex items-start gap-2 rounded-lg bg-muted px-3 py-2.5 text-xs leading-relaxed text-secondary-foreground">
            <Info size={14} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span>
              {risk?.reason ||
                'Weather could not be retrieved, so no weather risk is shown. The diagnosis above is unaffected.'}
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  )
}
