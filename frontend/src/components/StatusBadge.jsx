import { CheckCircle2, ClipboardCheck, PencilLine, UserCheck } from 'lucide-react'
import { Badge } from './ui/badge.jsx'

const STYLES = {
  AI_Verified: {
    label: 'AI verified',
    hint: 'Confident enough to act on, but still a screening result.',
    variant: 'low',
    Icon: CheckCircle2,
  },
  Pending_Expert_Review: {
    label: 'Expert review pending',
    hint: 'Sent to an agricultural expert for confirmation.',
    variant: 'moderate',
    Icon: ClipboardCheck,
  },
  Expert_Confirmed: {
    label: 'Expert confirmed',
    hint: 'An agricultural expert confirmed this diagnosis.',
    variant: 'default',
    Icon: UserCheck,
  },
  Expert_Corrected: {
    label: 'Expert corrected',
    hint: 'An expert reviewed this report and changed the diagnosis.',
    variant: 'secondary',
    Icon: PencilLine,
  },
}

export default function StatusBadge({ status, showHint = false }) {
  const style =
    STYLES[status] ||
    { label: status || 'Unknown', hint: '', variant: 'muted', Icon: ClipboardCheck }
  const { Icon } = style

  return (
    <div className="flex flex-col gap-1">
      <Badge variant={style.variant} className="w-fit px-2.5 py-1 text-2xs">
        <Icon size={13} aria-hidden="true" />
        {style.label}
      </Badge>
      {showHint && style.hint ? (
        <span className="text-2xs leading-relaxed text-muted-foreground">{style.hint}</span>
      ) : null}
    </div>
  )
}
