import { FlaskConical, Leaf, ShieldCheck, Sprout, TriangleAlert } from 'lucide-react'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from './ui/accordion.jsx'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from './ui/card.jsx'
import { Badge } from './ui/badge.jsx'

function Bullets({ items }) {
  if (!items?.length) return null
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => (
        <li key={index} className="flex gap-2 text-sm leading-relaxed text-secondary-foreground">
          <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-border" aria-hidden="true" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Advisory rendering.
 *
 * Sections are accordions, not tabs: a field study of crop-scouting tools found farmers
 * did not notice scrollable tabs and the interface was rebuilt around vertical scrolling
 * with expandable cards. Chemical guidance is collapsed by default and never carries a
 * dose — the knowledge base deliberately holds active-ingredient groups only.
 */
export default function AdvisoryPanel({ advisory }) {
  if (!advisory) return null

  const chemical = advisory.chemical_management || {}
  const groups = chemical.active_ingredient_groups || []
  const hasChemical = chemical.recommended && groups.length > 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {advisory.crop} · {advisory.condition}
        </CardTitle>
        <CardDescription className="text-sm leading-relaxed text-secondary-foreground">
          {advisory.summary}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {advisory.is_healthy ? (
          <div className="rounded-lg bg-primary/5 px-3 py-2.5 text-sm leading-relaxed text-primary">
            No disease was detected. Keep scouting — this result is a useful baseline.
          </div>
        ) : null}

        <Accordion type="multiple" defaultValue={['cultural']} className="w-full">
          {advisory.symptoms?.length ? (
            <AccordionItem value="symptoms">
              <AccordionTrigger>
                <Leaf size={15} className="text-muted-foreground" aria-hidden="true" />
                What to look for
              </AccordionTrigger>
              <AccordionContent>
                <Bullets items={advisory.symptoms} />
              </AccordionContent>
            </AccordionItem>
          ) : null}

          <AccordionItem value="cultural">
            <AccordionTrigger>
              <Sprout size={15} className="text-primary" aria-hidden="true" />
              Cultural control
            </AccordionTrigger>
            <AccordionContent>
              <Bullets items={advisory.cultural_control} />
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="organic">
            <AccordionTrigger>
              <ShieldCheck size={15} className="text-primary" aria-hidden="true" />
              Organic / low-risk management
            </AccordionTrigger>
            <AccordionContent>
              <Bullets items={advisory.organic_management} />
            </AccordionContent>
          </AccordionItem>

          {hasChemical ? (
            <AccordionItem value="chemical">
              <AccordionTrigger
                badge={
                  <Badge variant="accent" className="text-2xs">
                    Last resort
                  </Badge>
                }
              >
                <FlaskConical size={15} className="text-accent" aria-hidden="true" />
                Chemical management
              </AccordionTrigger>
              <AccordionContent className="space-y-3">
                <div>
                  <p className="mb-1.5 text-2xs font-bold uppercase tracking-wide text-muted-foreground">
                    Active-ingredient groups
                  </p>
                  <ul className="space-y-1">
                    {groups.map((group, index) => (
                      <li key={index} className="text-sm text-secondary-foreground">
                        {group}
                      </li>
                    ))}
                  </ul>
                </div>

                {chemical.notes ? (
                  <p className="text-xs leading-relaxed text-muted-foreground">{chemical.notes}</p>
                ) : null}

                {chemical.safety?.length ? (
                  <div className="rounded-lg bg-accent/10 px-3 py-2.5">
                    <p className="mb-1.5 flex items-center gap-1.5 text-2xs font-bold uppercase tracking-wide text-accent-foreground">
                      <TriangleAlert size={12} aria-hidden="true" />
                      Safety
                    </p>
                    <ul className="space-y-1">
                      {chemical.safety.map((item, index) => (
                        <li key={index} className="text-xs leading-relaxed text-accent-foreground">
                          {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <p className="text-2xs leading-relaxed text-muted-foreground">
                  No dosage is given here on purpose. Follow the product label exactly, check
                  that the product is registered for your crop and target, and ask your local
                  agricultural officer before applying anything.
                </p>
              </AccordionContent>
            </AccordionItem>
          ) : null}

          {advisory.monitoring ? (
            <AccordionItem value="monitoring">
              <AccordionTrigger>Monitoring</AccordionTrigger>
              <AccordionContent>
                <p className="text-sm leading-relaxed text-secondary-foreground">
                  {advisory.monitoring}
                </p>
              </AccordionContent>
            </AccordionItem>
          ) : null}
        </Accordion>

        {!chemical.recommended && chemical.notes ? (
          <div className="rounded-lg bg-muted px-3 py-2.5 text-xs leading-relaxed text-secondary-foreground">
            <strong className="font-semibold">No chemical control advised. </strong>
            {chemical.notes}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
