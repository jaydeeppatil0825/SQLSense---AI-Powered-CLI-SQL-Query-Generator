import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#071016",
          900: "#0b1720",
          800: "#102330",
        },
        signal: {
          400: "#22d3ee",
          500: "#06b6d4",
          600: "#0891b2",
        },
      },
      boxShadow: {
        glow: "0 0 50px rgba(34, 211, 238, 0.16)",
      },
    },
  },
  plugins: [],
} satisfies Config;
