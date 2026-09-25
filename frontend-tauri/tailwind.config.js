/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#060814",
        panel: "#0d1230",
        raised: "#151b45",
        line: "#2a3374",
        fg: "#e8ebff",
        muted: "#8b93c9",
        // follows the backend phase; set on :root by App.jsx (see styles/index.css)
        accent: "rgb(var(--accent-rgb) / <alpha-value>)",
      },
      fontFamily: {
        // Windows 11 ships Segoe UI Variable, so nothing is downloaded (offline rule, CSP).
        sans: ['"Segoe UI Variable Text"', '"Segoe UI"', "system-ui", "sans-serif"],
        display: ['"Segoe UI Variable Display"', '"Segoe UI Variable Text"', '"Segoe UI"', "system-ui", "sans-serif"],
        mono: ['"Cascadia Mono"', '"Cascadia Code"', "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
