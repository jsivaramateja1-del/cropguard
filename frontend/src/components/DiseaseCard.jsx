import { AlertCircle, CheckCircle2, Eye, ImageOff } from 'lucide-react'
import { Badge } from './ui/badge.jsx'
import { diseaseImage } from '../lib/diseaseImage.js'
import { useT } from '../i18n/index.jsx'

/**
 * One condition from the advisory knowledge base.
 *
 * The `supported_by_model` flag is shown, not hidden: the knowledge base covers 21
 * conditions while the deployed model recognises 16, and implying a photo can diagnose
 * anything in this grid would be a lie the UI told on the model's behalf.
 */
export default function DiseaseCard({ disease, onSelect, index = 0 }) {
  const { t } = useT()
  const healthy = disease.is_healthy
  const photo = diseaseImage(disease.label)

  return (
    <button
      type="button"
      onClick={() => onSelect?.(disease)}
      className="animate-rise group flex h-full w-full flex-col gap-2 rounded-xl border border-border bg-card p-3.5 text-left transition-colors hover:border-primary/50 focus-visible:border-primary"
      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
      aria-label={`${disease.condition} on ${disease.crop}`}
    >
      {photo ? (
        <img
          src={photo}
          alt={`Reference photo: ${disease.condition} on ${disease.crop}`}
          loading="lazy"
          decoding="async"
          className="-mx-3.5 -mt-3.5 mb-0.5 h-24 w-[calc(100%+1.75rem)] rounded-t-xl object-cover"
        />
      ) : (
        <div className="-mx-3.5 -mt-3.5 mb-0.5 flex h-24 w-[calc(100%+1.75rem)] items-center justify-center rounded-t-xl bg-muted">
          <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
            <ImageOff size={12} aria-hidden="true" />
            {t('diseases.noPhoto')}
          </span>
        </div>
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
            {disease.crop}
          </p>
          <p className="truncate text-sm font-semibold leading-snug">{disease.condition}</p>
        </div>
        {healthy ? (
          <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-risk-low" aria-hidden="true" />
        ) : (
          <AlertCircle size={15} className="mt-0.5 shrink-0 text-risk-high" aria-hidden="true" />
        )}
      </div>

      {disease.symptoms?.length ? (
        <ul className="space-y-0.5">
          {disease.symptoms.slice(0, 2).map((symptom) => (
            <li
              key={symptom}
              className="flex gap-1.5 text-xs leading-relaxed text-muted-foreground"
            >
              <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-border" aria-hidden="true" />
              <span className="line-clamp-2">{symptom}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-2 pt-1">
        {disease.supported_by_model ? (
          <Badge variant="low">{t('diseases.screenable')}</Badge>
        ) : (
          <Badge variant="muted">{t('diseases.advisoryOnly')}</Badge>
        )}
        <span className="flex items-center gap-1 text-2xs text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          <Eye size={11} aria-hidden="true" />
          {t('diseases.details')}
        </span>
      </div>
    </button>
  )
}
