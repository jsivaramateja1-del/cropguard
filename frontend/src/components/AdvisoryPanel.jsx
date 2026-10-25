import { useState } from 'react'
import { ChevronDown, FlaskConical, Leaf, ShieldCheck, Sprout, TriangleAlert } from 'lucide-react'

function Section({ title, Icon, items, tone = 'default' }) {
  if (!items?.length) return null
  return (
    <div>
      <h4 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-leaf-900">
        <Icon size={15} className={tone === 'warn' ? 'text-straw-600' : 'text-leaf-600'} aria-hidden="true" />
        {title}
      </h4>
      <ul className="space-y-1.5 pl-0.5">
        {items.map((item, index) => (
          <li key={index} className="flex gap-2 text-sm leading-relaxed text-soil-800">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-soil-400" aria-hidden="true" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Advisory rendering.
 *
 * The chemical section is collapsed by default and never presents a dose. That is
 * deliberate: the knowledge base deliberately carries active-ingredient groups and
 * safety warnings only, and the product must not imply a prescription.
 */
export default function AdvisoryPanel({ advisory }) {
  const [showChemical, setShowChemical] = useState(false)
  if (!advisory) return null

  const chemical = advisory.chemical_management || {}
  const groups = chemical.active_ingredient_groups || []

  return (
    <section className="card space-y-4 p-4">
      <header className="space-y-1">
        <h3 className="text-base font-bold">
          {advisory.crop} · {advisory.condition}
        </h3>
        <p className="text-sm leading-relaxed text-soil-700">{advisory.summary}</p>
      </header>

      {advisory.is_healthy ? (
        <p className="rounded-lg bg-leaf-50 px-3 py-2 text-sm text-leaf-800">
          No disease was detected. Keep scouting — this result is a useful baseline.
        </p>
      ) : (
        <Section title="What to look for" Icon={Leaf} items={advisory.symptoms} />
      )}

      <Section title="Cultural control" Icon={Sprout} items={advisory.cultural_control} />
      <Section title="Organic / low-risk management" Icon={ShieldCheck} items={advisory.organic_management} />

      {advisory.monitoring && (
        <p className="rounded-lg bg-soil-100 px-3 py-2 text-xs leading-relaxed text-soil-700">
          <strong className="font-semibold">Monitoring: </strong>
          {advisory.monitoring}
        </p>
      )}

      {chemical.recommended && groups.length > 0 && (
        <div className="rounded-lg border border-soil-200">
          <button
            type="button"
            className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
            onClick={() => setShowChemical((open) => !open)}
            aria-expanded={showChemical}
          >
            <span className="flex items-center gap-1.5 text-sm font-semibold text-leaf-900">
              <FlaskConical size={15} className="text-straw-600" aria-hidden="true" />
              Chemical management
            </span>
            <ChevronDown
              size={16}
              className={`text-soil-500 transition-transform ${showChemical ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>

          {showChemical && (
            <div className="space-y-3 border-t border-soil-200 px-3 py-3">
              <div>
                <p className="section-title mb-1">Active-ingredient groups</p>
                <ul className="space-y-1">
                  {groups.map((group, index) => (
                    <li key={index} className="text-sm text-soil-800">
                      {group}
                    </li>
                  ))}
                </ul>
              </div>

              {chemical.notes && (
                <p className="text-xs leading-relaxed text-soil-700">{chemical.notes}</p>
              )}

              {chemical.safety?.length > 0 && (
                <div className="rounded-lg bg-amber-50 px-3 py-2">
                  <p className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-soil-700">
                    <TriangleAlert size={12} className="text-straw-600" aria-hidden="true" />
                    Safety
                  </p>
                  <ul className="space-y-1">
                    {chemical.safety.map((item, index) => (
                      <li key={index} className="text-xs leading-relaxed text-soil-800">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <p className="text-[11px] leading-relaxed text-soil-600">
                No dosage is given here on purpose. Follow the product label exactly,
                check that the product is registered for your crop and target, and ask
                your local agricultural officer before applying anything.
              </p>
            </div>
          )}
        </div>
      )}

      {!chemical.recommended && chemical.notes && (
        <p className="rounded-lg bg-soil-100 px-3 py-2 text-xs leading-relaxed text-soil-700">
          <strong className="font-semibold">No chemical control advised. </strong>
          {chemical.notes}
        </p>
      )}
    </section>
  )
}
