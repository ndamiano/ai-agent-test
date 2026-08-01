/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: '#0B0E15',
        panel: '#131826',
        sunken: '#0E1220',
        well: '#080B12',
        edge: '#232B3D',
        bone: '#ECE7DC',
        slate: '#98A1B4',
        dim: '#6D7689',
        // The accent belongs to one idea: making the thing. Build, Play, the live pulse.
        ember: '#F2703F',
        'ember-ink': '#150A05',
        // Held back for the budget meter alone, so it still reads as its own signal.
        mana: '#7E6BD9',
        live: '#4FB477',
        wait: '#D9A441',
        fail: '#E05C55',
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
