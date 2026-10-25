import * as AccordionPrimitive from '@radix-ui/react-accordion'
import { ChevronDown } from 'lucide-react'
import { cn } from '../../lib/utils.js'

/**
 * Vertical accordion, not tabs.
 *
 * A crop-scouting field study found scrollable tabs went unnoticed by farmers and
 * replaced them with vertical scrolling plus expandable cards. Advisory sections are
 * long and read top-to-bottom, so accordions suit both the content and the evidence.
 */
export const Accordion = AccordionPrimitive.Root

export function AccordionItem({ className, ...props }) {
  return (
    <AccordionPrimitive.Item className={cn('border-b border-border last:border-0', className)} {...props} />
  )
}

export function AccordionTrigger({ className, children, badge, ...props }) {
  return (
    <AccordionPrimitive.Header className="flex">
      <AccordionPrimitive.Trigger
        className={cn(
          'group flex flex-1 items-center justify-between gap-3 py-3 text-left text-sm font-semibold text-foreground transition-colors hover:text-primary',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          className,
        )}
        {...props}
      >
        <span className="flex items-center gap-2">
          {children}
          {badge}
        </span>
        <ChevronDown
          size={16}
          className="shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180"
          aria-hidden="true"
        />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  )
}

export function AccordionContent({ className, children, ...props }) {
  return (
    <AccordionPrimitive.Content
      className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down"
      {...props}
    >
      <div className={cn('pb-4 pt-0', className)}>{children}</div>
    </AccordionPrimitive.Content>
  )
}
