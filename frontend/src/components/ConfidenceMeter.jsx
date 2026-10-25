/**
 * Confidence display.
 *
 * The 0.75 marker is drawn on the bar because the number alone is not interpretable:
 * docs/calibration.md shows this model's scores cluster tightly near 0.90, so a farmer
 * needs to see where the value sits relative to the expert-review threshold rather
 * than reading 68% as "68% certain".
 */
const THRESHOLD = 0.75

function tone(confidence) {
  if (confidence >= 0.9) return { bar: 'bg-leaf-600', text: 'text-leaf-800' }
  if (confidence >= THRESHOLD) return { bar: 'bg-leaf-500', text: 'text-leaf-800' }
  if (confidence >= 0.5) return { bar: 'bg-straw-500', text: 'text-soil-800' }
  return { bar: 'bg-alert-500', text: 'text-alert-600' }
}

export default function ConfidenceMeter({ confidence }) {
  const value = Number.isFinite(confidence) ? confidence : 0
  const percent = Math.round(value * 100)
  const style = tone(value)

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-soil-600">
          Model confidence
        </span>
        <span className={`text-sm font-bold ${style.text}`}>{percent}%</span>
      </div>

      <div
        className="relative h-2.5 w-full overflow-hidden rounded-full bg-soil-200"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label="Model confidence"
      >
        <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${percent}%` }} />
        <div
          className="absolute top-0 h-full w-0.5 bg-soil-700"
          style={{ left: `${THRESHOLD * 100}%` }}
          title={`Expert review threshold (${THRESHOLD * 100}%)`}
        />
      </div>

      <p className="mt-1 text-[11px] text-soil-600">
        The mark at {THRESHOLD * 100}% is the expert-review threshold. This is a model
        score, not a probability of being correct.
      </p>
    </div>
  )
}
