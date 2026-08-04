import type { Config } from "tailwindcss";
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f6f6f3", surface: "#ffffff", ink: "#1c1d22",
        muted: "#5b6070", accent: "#6d5efc",
        ok: "#067647", okbg: "#e7f7ee",
        bad: "#b42318", badbg: "#fee4e2",
        busy: "#5b52d6", busybg: "#ecebff",
      },
      borderRadius: { card: "0.75rem" },
      boxShadow: { soft: "0 1px 2px rgba(16,24,40,.04), 0 4px 16px rgba(16,24,40,.06)" },
    },
  },
  plugins: [],
} satisfies Config;
