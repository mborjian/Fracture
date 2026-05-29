import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "var(--color-background)",
        panel: "var(--color-panel)",
        panelAlt: "var(--color-panel-alt)",
        panelHover: "var(--color-panel-hover)",
        overlay: "var(--color-overlay)",
        overlaySoft: "var(--color-overlay-soft)",
        importDrag: "var(--color-import-drag-bg)",
        border: "var(--color-border)",
        text: "var(--color-text)",
        textMuted: "var(--color-text-muted)",
        accent: "var(--color-accent)",
        accentHover: "var(--color-accent-hover)",
        success: "var(--color-success)",
        warning: "var(--color-warning)",
        danger: "var(--color-danger)",
        dangerHover: "var(--color-danger-hover)"
      },
      boxShadow: {
        soft: "0 12px 32px rgba(1,6,16,0.35)"
      }
    }
  },
  plugins: []
};

export default config;
