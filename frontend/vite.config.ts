import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
  },
  server: {
    host: true,
    allowedHosts: ['.ts.net'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/play': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // The Play button's session URL is relative in same-origin mode — without this rule the
      // dev server answers /handoff with the SPA shell instead of the grant redirect.
      '/handoff': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
