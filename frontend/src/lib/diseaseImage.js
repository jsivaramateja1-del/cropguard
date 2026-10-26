import manifest from './disease-images.json'

/**
 * Reference photos for the disease grid.
 *
 * One photo per condition, from the PlantVillage dataset (Mohanty et al., 2016), used
 * under CC BY-SA 3.0 with attribution in the footer. They are lab photographs of a single
 * leaf — useful for recognising a condition, not a substitute for the farmer's own photo.
 *
 * Rice is not in PlantVillage, so those three conditions have no reference image and the
 * UI says so rather than showing a broken frame.
 */
export function diseaseSlug(label) {
  return String(label || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}

const AVAILABLE = new Set(manifest.slugs)

export function diseaseImage(label) {
  const slug = diseaseSlug(label)
  return AVAILABLE.has(slug) ? `/diseases/${slug}.jpg` : null
}

export const REFERENCE_IMAGE_SOURCE = manifest.source
