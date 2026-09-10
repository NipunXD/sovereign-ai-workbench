import type { Config } from "tailwindcss";

/**
 * Design tokens for the Sovereign AI Workbench.
 *
 * The visual language is an industrial control panel rather than a consumer
 * chat app: dense, dark by default, and built around the idea that an operator
 * should be able to tell the state of the system at a glance.
 *
 * Two constraints shaped this:
 *
 *  - No webfonts. An air-gapped deployment cannot reach fonts.googleapis.com,
 *    and a font that silently fails to load is a layout that silently breaks.
 *    Everything uses the system stack.
 *  - Classification is not decoration. The four sensitivity levels get four
 *    deliberately distinguishable colours, and they are also distinguishable
 *    without colour (each badge carries its label), because a refinery control
 *    room is exactly where you find deuteranopia and bad monitors.
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic surfaces, driven by CSS variables so the theme can flip.
        bg: "hsl(var(--bg) / <alpha-value>)",
        surface: "hsl(var(--surface) / <alpha-value>)",
        "surface-raised": "hsl(var(--surface-raised) / <alpha-value>)",
        border: "hsl(var(--border) / <alpha-value>)",
        "border-strong": "hsl(var(--border-strong) / <alpha-value>)",
        fg: "hsl(var(--fg) / <alpha-value>)",
        "fg-muted": "hsl(var(--fg-muted) / <alpha-value>)",
        "fg-subtle": "hsl(var(--fg-subtle) / <alpha-value>)",

        // Signal amber: the plant accent. Used for primary actions and the
        // active state, never for danger.
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          fg: "hsl(var(--accent-fg) / <alpha-value>)",
          muted: "hsl(var(--accent-muted) / <alpha-value>)",
        },

        // The classification ladder. Ordered cool -> hot so that "more
        // sensitive" reads as "more urgent" without needing the label.
        classification: {
          public: "hsl(215 15% 55%)",
          internal: "hsl(205 75% 55%)",
          confidential: "hsl(38 92% 55%)",
          restricted: "hsl(0 78% 60%)",
        },

        // Status, used for health pills, OCR confidence and run outcomes.
        ok: "hsl(150 60% 45%)",
        warn: "hsl(38 92% 55%)",
        danger: "hsl(0 75% 60%)",
        info: "hsl(205 75% 55%)",

        // Citation highlight, tuned to sit over a scanned page without
        // obscuring the text underneath it.
        highlight: "hsl(48 100% 50%)",
      },
      fontFamily: {
        // System stack only — see the note above about air-gapped deployments.
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto",
          "Helvetica Neue", "Arial", "sans-serif",
        ],
        // Equipment tags live here. V-1201 vs V-12O1 has to be unambiguous,
        // and a proportional font makes columns of measurements unreadable.
        mono: [
          "ui-monospace", "SFMono-Regular", "SF Mono", "Menlo", "Consolas",
          "Liberation Mono", "monospace",
        ],
      },
      fontSize: {
        // A control panel is dense. The base is deliberately small, with tight
        // leading, because the alternative is scrolling to see one reading.
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
        xs: ["0.75rem", { lineHeight: "1.1rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.875rem", { lineHeight: "1.4rem" }],
      },
      borderRadius: {
        DEFAULT: "0.25rem",
        md: "0.375rem",
        lg: "0.5rem",
      },
      spacing: {
        // The app shell is a fixed frame; these keep it consistent.
        header: "3rem",
        sidebar: "14rem",
        rail: "3.25rem",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        "flash-highlight": {
          "0%": { backgroundColor: "hsl(48 100% 50% / 0.65)" },
          "100%": { backgroundColor: "hsl(48 100% 50% / 0.22)" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pulse-dot": "pulse-dot 1.6s ease-in-out infinite",
        "flash-highlight": "flash-highlight 1.2s ease-out forwards",
        "slide-up": "slide-up 140ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
