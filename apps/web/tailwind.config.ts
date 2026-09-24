import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        nv: { DEFAULT: "#76B900", dark: "#5a8f00", light: "#a4d65e" },
        ops: {
          bg: "#070b12",
          panel: "#0d1420",
          panel2: "#111a29",
          line: "#1e2a3d",
          muted: "#7d8aa3",
        },
      },
      fontFamily: {
        sans: ["Pretendard", "Inter", "system-ui", "-apple-system", "Segoe UI", "Noto Sans KR", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      keyframes: {
        pulseRing: { "0%": { boxShadow: "0 0 0 0 rgba(118,185,0,.55)" }, "100%": { boxShadow: "0 0 0 10px rgba(118,185,0,0)" } },
        slideIn: { "0%": { opacity: "0", transform: "translateY(6px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
      },
      animation: { pulseRing: "pulseRing 1.4s ease-out infinite", slideIn: "slideIn .35s ease-out both" },
    },
  },
  plugins: [],
} satisfies Config;
