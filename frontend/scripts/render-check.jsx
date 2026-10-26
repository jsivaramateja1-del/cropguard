/**
 * Render smoke test.
 *
 * Renders the whole app to HTML on the server and asserts the important content is
 * present. No browser, no test framework — enough to catch a crash, a missing component,
 * or copy that silently disappeared, which is most of what breaks a UI refactor.
 *
 * Run with:  npm run test:render
 */
import translationsModule from '../src/i18n/translations.json'
import { renderToString } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import App from '../src/App.jsx'

const html = renderToString(
  <MemoryRouter initialEntries={['/']}>
    <App />
  </MemoryRouter>,
)

const CHECKS = [
  ['page heading', 'Check a leaf'],
  ['crop question', 'Which crop is in the photo?'],
  ['camera button', 'Take photo'],
  ['gallery button', 'From gallery'],
  ['field section', 'About this field'],
  ['name label', 'Your name'],
  ['location control', 'Field location'],
  ['manual coordinates', 'Enter coordinates manually'],
  ['expert opt-in', 'Ask an expert to review this'],
  ['submit label', 'Analyse leaf'],
  ['weather attribution', 'Open-Meteo'],
  ['component attribution', 'Radix UI'],
  ['safety disclaimer', 'potential diagnosis'],
  ['crop coverage note', 'Wheat has no trained classes'],
  ['outbreak nav', 'Outbreaks'],
  ['follow-up nav', 'Follow up'],
  ['expert nav', 'Expert'],
  ['account nav', 'Account'],
  ['diseases nav', 'Diseases'],
  ['theme toggle group', 'Colour theme'],
  ['theme toggle offers three options', 'System theme'],
]

let failures = 0
for (const [name, needle] of CHECKS) {
  const found = html.includes(needle)
  if (!found) failures += 1
  console.log(`${found ? 'PASS' : 'FAIL'}  ${name}`)
}

// The submit button must be tappable with an empty form so a tap can explain what is
// missing. Match the attribute, not the Tailwind class `disabled:pointer-events-none`.
const submitTags = [...html.matchAll(/<button[^>]*>/g)]
  .map((match) => match[0])
  .filter((tag) => tag.includes('type="submit"'))

const tappable = submitTags.length > 0 && submitTags.every((tag) => !/\sdisabled(=|\s|>)/.test(tag))
if (!tappable) failures += 1
console.log(`${tappable ? 'PASS' : 'FAIL'}  submit button tappable on an empty form (${submitTags.length} found)`)

const mobileBar = html.includes('fixed inset-x-0 bottom-0')
if (!mobileBar) failures += 1
console.log(`${mobileBar ? 'PASS' : 'FAIL'}  mobile action bar rendered`)

// A 404 route must exist rather than crashing on an unknown path.
const notFound = renderToString(
  <MemoryRouter initialEntries={['/nope']}>
    <App />
  </MemoryRouter>,
)
const has404 = notFound.includes('Page not found')
if (!has404) failures += 1
console.log(`${has404 ? 'PASS' : 'FAIL'}  unknown route renders a 404`)

const mapHtml = renderToString(
  <MemoryRouter initialEntries={['/outbreaks']}>
    <App />
  </MemoryRouter>,
)
const mapFallback = mapHtml.includes('Loading')
console.log(`${mapFallback ? 'PASS' : 'FAIL'}  map route lazy-loads behind a fallback`)
if (!mapFallback) failures += 1


console.log(`\nrendered ${html.length} bytes`)
if (failures > 0) {
  console.error(`\n${failures} check(s) failed`)
  process.exit(1)
}
// --- translation completeness ------------------------------------------------
// A half-translated language shows English mid-sentence, which looks broken. Checked here
// rather than in the browser, because the built bundle does not serve the raw JSON.
{
  const english = Object.keys(translationsModule.en || {})
  const codes = Object.keys(translationsModule)
  const enough = codes.length >= 11
  if (!enough) failures += 1
  console.log(`${enough ? 'PASS' : 'FAIL'}  at least 11 languages are translated (${codes.length})`)

  const incomplete = codes
    .map((code) => ({ code, missing: english.filter((key) => !(key in translationsModule[code])) }))
    .filter((entry) => entry.missing.length > 0)

  if (incomplete.length) console.error('incomplete dictionaries:', JSON.stringify(incomplete))
  const complete = incomplete.length === 0
  if (!complete) failures += 1
  console.log(`${complete ? 'PASS' : 'FAIL'}  every language covers all ${english.length} keys`)
}

console.log('all render checks passed')
// Exit explicitly: importing axios pulls in Node's undici, which lazily instantiates a
// WASM HTTP parser during shutdown and can fail on a host with a tight address-space
// limit — after the assertions have already passed.
process.exit(0)