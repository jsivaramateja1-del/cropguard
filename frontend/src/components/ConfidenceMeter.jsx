import { Progress } from './ui/progress.jsx'

/**
 * Confidence display.
 *
 * The 0.75 marker is drawn on the bar because the number alone misleads: the model's
 * scores cluster tightly near 0.90 (see docs/calibration.md), so 68% needs to be read
 * against the expert-review threshold, not as "68% certain".
 */
const THRESHOLD = 0.75

function tone(confidence) {
  if (confidence >= 0.9) return { bar: 'bg-risk-low', text: 'text-risk-low' }
  if (confidence >= THRESHOLD) return { bar: 'bg-risk-low/80', text: 'text-risk-low' }
  if (confidence >= 0.5) return { bar: 'bg-risk-moderate', text: 'text-[hsl(38_85%_28%)]' }
  return { bar: 'bg-risk-critical', text: 'text-risk-critical' }
}

export default function ConfidenceMeter({ confidence }) {
  const value = Number.isFinite(confidence) ? confidence : 0
  const percent = Math.round(value * 100)
  const style = tone(value)

  return (
    <div>
      <Progress
        value={percent}
        barClassName={style.bar}
        markerAt={THRESHOLD * 100}
        aria-label="Model confidence"
        label={{
          text: 'Model confidence',
          value: `${percent}%`,
          className: style.text,
        }}
      />
      <p className="mt-1.5 text-2xs leading-relaxed text-muted-foreground">
        The mark at {THRESHOLD * 100}% is the expert-review threshold. This is a model score,
        not a probability of being correct.
      </p>
    </div>
  )
}
