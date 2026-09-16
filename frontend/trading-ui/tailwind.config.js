/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./features/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#0a0e17",
        surface: "#111827",
        surfaceHover: "#1f2937",
        border: "#1e293b",
        tradeGreen: "#10b981",
        tradeRed: "#ef4444",
        accent: "#3b82f6",
      },
    },
  },
  plugins: [],
}

