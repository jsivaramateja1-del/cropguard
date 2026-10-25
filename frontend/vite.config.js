import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API base URL comes from VITE_API_BASE_URL at build time. Vite only exposes
// variables prefixed with VITE_, which is deliberate: nothing secret can leak into
// the bundle because nothing secret is ever named VITE_*.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Keep the bundle small: the app is used on rural mobile connections.
    chunkSizeWarningLimit: 600,
  },
})
