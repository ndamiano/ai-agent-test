/** @type {import('tailwindcss').Config} */

// Names only — every value is a CSS variable defined in src/index.css, so tuning a colour is a
// stylesheet edit that hot-reloads instead of a config edit that needs the dev server restarted.
const hue = (name) => `rgb(var(--c-${name}) / <alpha-value>)`

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: hue('ink'),
        panel: hue('panel'),
        sunken: hue('sunken'),
        well: hue('well'),
        edge: hue('edge'),
        bone: hue('bone'),
        slate: hue('slate'),
        dim: hue('dim'),
        ember: hue('ember'),
        'ember-ink': hue('ember-ink'),
        mana: hue('mana'),
        live: hue('live'),
        wait: hue('wait'),
        fail: hue('fail'),
      },
      fontFamily: {
        display: ['"Iowan Old Style"', '"Palatino Linotype"', 'Palatino', '"Liberation Serif"', 'Georgia', 'serif'],
        sans: ['system-ui', '-apple-system', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'sans-serif'],
        mono: ['ui-monospace', '"JetBrains Mono"', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
