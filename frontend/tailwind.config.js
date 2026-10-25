/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Agricultural palette: soil, leaf, straw. Restrained on purpose — this is a
        // field tool, not a dashboard demo. Contrast checked against the cream
        // background for body text (ink) and against white for the leaf shades.
        leaf: {
          50: '#f2f7f2',
          100: '#dfeade',
          200: '#c0d6bf',
          300: '#94b894',
          400: '#679768',
          500: '#4a7c4b',
          600: '#386239',
          700: '#2d4e2f',
          800: '#253f27',
          900: '#1e3420',
        },
        soil: {
          50: '#faf7f2',
          100: '#f0e9dd',
          200: '#e0d2ba',
          300: '#c9b291',
          400: '#b0936b',
          500: '#967a52',
          600: '#7d6444',
          700: '#655039',
          800: '#544333',
          900: '#47392d',
        },
        straw: {
          400: '#e3b23c',
          500: '#d29b1f',
          600: '#b07f14',
        },
        alert: {
          500: '#c0392b',
          600: '#a32f23',
        },
      },
      fontFamily: {
        sans: [
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Noto Sans',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
      },
      fontSize: {
        // Larger base sizes than a typical web app: this is read on a phone in daylight.
        base: ['1rem', { lineHeight: '1.6' }],
        lg: ['1.125rem', { lineHeight: '1.6' }],
      },
      minHeight: {
        touch: '3rem',
      },
      boxShadow: {
        card: '0 1px 2px rgba(30, 52, 32, 0.06), 0 4px 12px rgba(30, 52, 32, 0.05)',
      },
    },
  },
  plugins: [],
}
