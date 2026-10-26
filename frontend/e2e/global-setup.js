/**
 * Generate the end-to-end fixtures.
 *
 * They are generated rather than committed: a binary in the repository is easy to lose
 * track of, and CI needs the same file the developer has. Runs once per `playwright test`.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const FIXTURES = path.join(__dirname, 'fixtures')

// A 240x240 JPEG: a green leaf surface with brown lesions. Small, valid, and enough for
// the frontend's client-side checks (type, extension, size).
const LEAF_JPEG_BASE64 =
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCADwAPADASIAAhEBAxEB/8QAGAABAQEBAQAAAAAAAAAAAAAAAAIBAwb/xAAiEAEBAAIBBAMBAQEAAAAAAAAAAQIRMQMSIVEyQWETcSL/xAAZAQEAAwEBAAAAAAAAAAAAAAAAAQIEAwX/xAAjEQEAAgICAgIDAQEAAAAAAAAAAQIDEQQxEiETQSIyURQj/9oADAMBAAIRAxEAPwDzwDzXnAAAAAAAAAAAAAAAAAAAAAAAAAAAAANnmxCYjc6YOznnNZXSInbTm4/x13tICzKAASbuorsy9K6c8bWpNm/Fxa2p5WntwF9ScVC0TtkyU8LzUAS5gAAAAAAAAADZLeGOuE1j/qJnTvhxfJbSOzL0nh3RnPG/SsWaMvFitZtWenMBdgAAX/S/iLd3dBGl7ZL3jVpAEqAALwy14q9z3HEVmGrHybUr462rPLuv+JBLPe03t5SAJVAAAAAAAAAAHXG7xjk2WzhWY20YMvx23PTsnO6x/wBZM8srrw24S/d2rrU+2+15yUn43IbZq6rF3kzExOpAEoAAAAAAAAAAAAAAAAAAAAAAAAVhdXy6OIrMbasXJnHXx1tuV3ltgJZ7Wm0zMgCVQAAAAAAAAAGyW8N/nVdP4rUmXo4uNS1ImftwFZ/KpWYL18bTAAlUAAAAAAAAAAAAAAAAAAAAAAABuOVxV/T1ECNQ61zZKRqJOQEuQDrjhJPM8omdO2LFbJOoch27Z6gr5NMcKfuXEblNZaYsxWrNZmJAEqgAAAAAAAAAAAAAAAAAAAAADs4qxzsmuVZjbXxstcczFvt1TlnMU3qeoi3d3URX+u+XlREap223d3WAs86ZmZ3IAlAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABPN0J7bMblwr+f6uTU01zm0vUpxaRH5e5crhZx5ZJuyOznnNZbiYlxz8etPzjpcmpqI6k1d+2zqTXlOWXdSIna2fLjtj1CQF3nAAA2S3hvZl6QvWlre4hIa86bqz6FfGf4wBKAAAAAAAAAAAABuPyn+sEJidTt3E45bn6pze5W0XjcDn1PpdsnLlle67TWPbPyrxFPH7lgDo8oAAAB1xx7YpnLXJ71YiI1DGst1N1nfj7ETete50m9PzdJssuq6y7m4553eS0TLDyMNK18qpAXYAAAAAAAAAAAABvdfdYITEzHRvfICTsBePT3PKJnS9Mdsk6qgdL059VFll1SJ2tkw3x/swBLirHPt8fSv6T9cxXUO9ORkpGolWWVySCXK1ptO5AX/ADv4bTXHa/6wgLNXVEq9AAgAAAAAAAAGyW3ULjZzELRS0xuI9MASqC8cNzdL0/G4ruGiOPkmvlpOPyjs4cOuOUyiLNHDtEbr9qc+p9L4c88u6/iK9u3KtEY9T3KQHR5IAAACsPlHVwV35e1ZjbZx+RXHWa2hvU+SATDNkt52mwAlQAAAAAABWOPdfxC1azadQ3p/a8vjSYycQslmqpv29elLUx+P2nDGa2pmPiaUStirFaRDODhGWf8A14qblbzU+LhblUruIhgC7ywAAAAAAAAAAAAAAAAAAAB1w+McnTC+NelbdNfEmIye1gKPURnxtHdfdV1L9IXjp5XJt/0nUgCzKAAAAAAAAAAAAAAAAAAAAAAEuruAJ6XOp7hepfqIFdQ7f6MutbAFnAAAAAAAAAAAAAAAAAAAAB//2Q=='

export default async function globalSetup() {
  fs.mkdirSync(FIXTURES, { recursive: true })

  const leaf = path.join(FIXTURES, 'leaf.jpg')
  if (!fs.existsSync(leaf)) {
    fs.writeFileSync(leaf, Buffer.from(LEAF_JPEG_BASE64, 'base64'))
  }

  const notes = path.join(FIXTURES, 'notes.txt')
  if (!fs.existsSync(notes)) {
    fs.writeFileSync(notes, 'this is not a photo\n', 'utf8')
  }

  for (const file of [leaf, notes]) {
    if (!fs.existsSync(file)) {
      throw new Error(`fixture generation failed: ${file}`)
    }
  }
}
