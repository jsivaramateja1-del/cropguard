import js from '@eslint/js'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'

/**
 * Deliberately narrow: rules that catch mistakes, not style preferences. Formatting is
 * not enforced here — a lint run should fail for a bug, not for a wrapping choice.
 */
export default [
  { ignores: ['dist/**', 'node_modules/**', 'playwright-report/**', 'test-results/**'] },

  js.configs.recommended,

  {
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Without this, every component imported and used only inside JSX is reported as
      // unused — 189 false positives across the app.
      'react/jsx-uses-vars': 'error',
      'react/jsx-uses-react': 'off',
      // Unused variables are a smell, but an ignored rest-sibling or an intentionally
      // unused argument is common in components; allow the underscore convention.
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },

  {
    files: ['e2e/**/*.{js,jsx}', 'scripts/**/*.{js,mjs,jsx}', '*.config.js'],
    plugins: { react },
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.node, ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      // These files render JSX too, so the same rule applies.
      'react/jsx-uses-vars': 'error',
    },
  },
]
