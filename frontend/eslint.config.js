import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // A wire payload is genuinely unknown until a caller narrows it — `any` is the honest type
      // for a parsed JSON body, and `unknown` would only push casts to the read sites.
      '@typescript-eslint/no-explicit-any': 'off',
      // Panels export their pure helpers so the tests can reach them without mounting React. The
      // cost is a full reload instead of a hot one; the benefit is testable logic.
      'react-refresh/only-export-components': 'off',
      // Fetch-into-state on mount is what every panel does. The rule wants an external store; the
      // data here IS external (the API), and each effect already guards against a stale response.
      'react-hooks/set-state-in-effect': 'off',
    },
  },
])
