import type { Config } from "tailwindcss";

/**
 * Design tokens for the Sovereign AI Workbench.
 *
 * The target is the visual language of enterprise SaaS — the tools people
 * actually buy and sit in front of all day — rather than the dark "control
 * panel" look, which reads as a hobby dashboard however much real engineering
 * is behind it. So: a light ground, white cards, generous radii, soft layered
 * shadows, and one confident accent.
 *
 * Three constraints shaped the specifics:
 *
 *  - No webfont requests at runtime. An air-gapped deployment cannot reach
 *    fonts.googleapis.com, so Plus Jakarta Sans is committed to the repository
 *    as a variable woff2 and loaded from our own origin. A font that silently
 *    fails to load is a layout that silently breaks.
 *  - Classification is not decoration. The four sensitivity levels keep four
 *    deliberately distinguishable hues, and each badge carries its label, so
 *    the ladder survives a bad projector and deuteranopia alike.
 *  - The accent is indigo, not blue, because "internal" on the classification
 *    ladder is blue. A primary button and a sensitivity badge must never be
 *    confusable at a glance.
 */
const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic surfaces, driven by CSS variables so the theme can flip.
        bg: "hsl(var(--bg) / <alpha-value>)",
        surface: "hsl(var(--surface) / <alpha-value>)",
        "surface-raised": "hsl(var(--surface-raised) / <alpha-value>)",
        "surface-sunken": "hsl(var(--surface-sunken) / <alpha-value>)",
        border: "hsl(var(--border) / <alpha-value>)",
        "border-strong": "hsl(var(--border-strong) / <alpha-value>)",
        fg: "hsl(var(--fg) / <alpha-value>)",
        "fg-muted": "hsl(var(--fg-muted) / <alpha-value>)",
        "fg-subtle": "hsl(var(--fg-subtle) / <alpha-value>)",

        // Indigo: primary actions, the active nav item, the live run.
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          fg: "hsl(var(--accent-fg) / <alpha-value>)",
          muted: "hsl(var(--accent-muted) / <alpha-value>)",
          strong: "hsl(var(--accent-strong) / <alpha-value>)",
        },

        // The classification ladder. Ordered cool -> hot so that "more
        // sensitive" reads as "more urgent" without needing the label.
        classification: {
          public: "hsl(215 16% 47%)",
          internal: "hsl(200 98% 39%)",
          confidential: "hsl(32 95% 44%)",
          restricted: "hsl(0 72% 51%)",
        },

        // Status, used for health pills, OCR confidence and run outcomes.
        ok: "hsl(160 84% 32%)",
        warn: "hsl(32 95% 44%)",
        danger: "hsl(0 72% 51%)",
        info: "hsl(200 98% 39%)",

        // Citation highlight, tuned to sit over a scanned page without
        // obscuring the text underneath it.
        highlight: "hsl(45 100% 51%)",
      },
      fontFamily: {
        // Self-hosted; see the note above. The stack after it is what renders
        // in the half-second before the woff2 lands.
        sans: ["var(--font-sans)", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        // Equipment tags live here. V-1201 vs V-12O1 has to be unambiguous,
        // and a proportional font makes columns of measurements unreadable.
        mono: ["ui-monospace", "SFMono-Regular", "SF Mono", "Menlo", "Consolas", "Liberation Mono", "monospace"],
      },
      fontSize: {
        // Raised a step across the board after an evaluation round where the
        // judges could not read the small text from the room. The smallest
        // label was 11px, used in 129 places — legible on the laptop it was
        // built on and not from four metres away through a projector. The
        // density is unchanged; every size simply starts one step higher.
        "2xs": ["0.75rem", { lineHeight: "1.0625rem", letterSpacing: "0.005em" }],
        xs: ["0.8125rem", { lineHeight: "1.1875rem" }],
        sm: ["0.875rem", { lineHeight: "1.3125rem" }],
        base: ["0.9375rem", { lineHeight: "1.4375rem" }],
        md: ["1rem", { lineHeight: "1.5625rem" }],
        lg: ["1.125rem", { lineHeight: "1.6875rem", letterSpacing: "-0.01em" }],
        xl: ["1.375rem", { lineHeight: "1.875rem", letterSpacing: "-0.015em" }],
        "2xl": ["1.625rem", { lineHeight: "2.125rem", letterSpacing: "-0.02em" }],
        "3xl": ["2rem", { lineHeight: "2.375rem", letterSpacing: "-0.025em" }],
        "4xl": ["2.5rem", { lineHeight: "2.75rem", letterSpacing: "-0.03em" }],
      },
      borderRadius: {
        DEFAULT: "0.375rem",
        md: "0.5rem",
        lg: "0.625rem",
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      spacing: {
        // The app shell is a fixed frame; these keep it consistent.
        header: "3.5rem",
        sidebar: "15rem",
        "sidebar-sm": "3.5rem",
      },
      boxShadow: {
        // A four-step elevation ramp. Enterprise UI gets its depth from soft,
        // wide, low-opacity shadows rather than from borders — that is most of
        // the difference between "card" and "div with a line around it".
        xs: "0 1px 2px 0 hsl(222 47% 11% / 0.04)",
        sm: "0 1px 2px 0 hsl(222 47% 11% / 0.05), 0 1px 3px 0 hsl(222 47% 11% / 0.04)",
        card: "0 1px 2px 0 hsl(222 47% 11% / 0.04), 0 4px 12px -3px hsl(222 47% 11% / 0.07)",
        lifted: "0 2px 4px -1px hsl(222 47% 11% / 0.05), 0 12px 28px -8px hsl(222 47% 11% / 0.12)",
        popover: "0 4px 8px -2px hsl(222 47% 11% / 0.08), 0 24px 48px -12px hsl(222 47% 11% / 0.18)",
        // A soft halo in the accent, for the one thing on screen that is live.
        glow: "0 0 0 3px hsl(var(--accent) / 0.12)",
        "glow-sm": "0 0 0 3px hsl(var(--accent) / 0.10)",
      },
      keyframes: {
        "fade-in-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "node-pulse": {
          "0%, 100%": { opacity: "0.35", transform: "scale(1)" },
          "50%": { opacity: "0.9", transform: "scale(1.25)" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        "flash-highlight": {
          "0%": { backgroundColor: "hsl(45 100% 51% / 0.6)" },
          "100%": { backgroundColor: "hsl(45 100% 51% / 0.2)" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-in-up": "fade-in-up 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "node-pulse": "node-pulse 2.4s ease-in-out infinite",
        "pulse-dot": "pulse-dot 1.6s ease-in-out infinite",
        "flash-highlight": "flash-highlight 1.2s ease-out forwards",
        "slide-up": "slide-up 160ms cubic-bezier(0.22, 1, 0.36, 1)",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
