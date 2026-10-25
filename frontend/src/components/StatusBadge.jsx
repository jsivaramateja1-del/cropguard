import { CheckCircle2, ClipboardCheck, PencilLine, UserCheck } from 'lucide-react'

const STYLES = {
  AI_Verified: {
    label: 'AI verified',
    hint: 'Confident enough to act on, but still a screening result.',
    className: 'bg-leaf-100 text-leaf-800',
    Icon: CheckCircle2,
  },
  Pending_Expert_Review: {
    label: 'Expert review pending',
    hint: 'This report has been sent to an agricultural expert for confirmation.',
    className: 'bg-amber-100 text-soil-800',
    Icon: ClipboardCheck,
  },
  Expert_Confirmed: {
    label: 'Expert confirmed',
    hint: 'An agricultural expert confirmed this diagnosis.',
    className: 'bg-leaf-700 text-white',
    Icon: UserCheck,
  },
  Expert_Corrected: {
    label: 'Expert corrected',
    hint: 'An expert reviewed this report and changed the diagnosis.',
    className: 'bg-soil-700 text-white',
    Icon: PencilLine,
  },
}

export default function StatusBadge({ status, showHint = false }) {
  const style = STYLES[status] || {
    label: status || 'Unknown',
    hint: '',
    className: 'bg-soil-200 text-soil-800',
    Icon: ClipboardCheck,
  }
  const { Icon } = style

  return (
    <div className="flex flex-col gap-1">
      <span className={`chip ${style.className}`}>
        <Icon size={13} aria-hidden="true" />
        {style.label}
      </span>
      {showHint && style.hint && <span className="text-xs text-soil-600">{style.hint}</span>}
    </div>
  )
}
