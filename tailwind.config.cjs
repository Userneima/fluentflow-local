module.exports = {
  darkMode: "class",
  content: ["./frontend/index.html", "./frontend/src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        "surface": "var(--c-surface)",
        "surface-dim": "var(--c-surface-dim)",
        "surface-bright": "var(--c-surface-bright)",
        "surface-variant": "var(--c-surface-variant)",
        "surface-container": "var(--c-surface-container)",
        "surface-container-low": "var(--c-surface-container-low)",
        "surface-container-lowest": "var(--c-surface-container-lowest)",
        "surface-container-high": "var(--c-surface-container-high)",
        "surface-container-highest": "var(--c-surface-container-highest)",
        "surface-tint": "var(--c-surface-tint)",
        "on-surface": "var(--c-on-surface)",
        "on-surface-variant": "var(--c-on-surface-variant)",
        "on-background": "var(--c-on-background)",
        "background": "var(--c-background)",
        "outline": "var(--c-outline)",
        "outline-variant": "var(--c-outline-variant)",
        "primary": "#3B82F6",
        "primary-container": "#2170e4",
        "on-primary": "#ffffff",
        "on-primary-container": "#fefcff",
        "primary-fixed": "#d8e2ff",
        "primary-fixed-dim": "#adc6ff",
        "on-primary-fixed": "#001a42",
        "on-primary-fixed-variant": "#004395",
        "inverse-primary": "#adc6ff",
        "secondary": "#495e8a",
        "secondary-container": "#b6ccff",
        "on-secondary": "#ffffff",
        "on-secondary-container": "#405682",
        "secondary-fixed": "#d8e2ff",
        "secondary-fixed-dim": "#b1c6f9",
        "on-secondary-fixed": "#001a42",
        "on-secondary-fixed-variant": "#304671",
        "tertiary": "#6b38d4",
        "tertiary-container": "#8455ef",
        "on-tertiary": "#ffffff",
        "on-tertiary-container": "var(--c-on-tertiary-container)",
        "tertiary-fixed": "var(--c-tertiary-fixed)",
        "tertiary-fixed-dim": "#d0bcff",
        "on-tertiary-fixed": "#23005c",
        "on-tertiary-fixed-variant": "var(--c-on-tertiary-fixed-variant)",
        "error": "#ba1a1a",
        "error-container": "var(--c-error-container)",
        "on-error": "#ffffff",
        "on-error-container": "var(--c-on-error-container)",
        "inverse-surface": "var(--c-inverse-surface)",
        "inverse-on-surface": "var(--c-inverse-on-surface)"
      },
      fontFamily: {
        "headline": ["Manrope"],
        "body": ["Inter"],
        "label": ["Inter"]
      },
      borderRadius: {
        "DEFAULT": "0.125rem",
        "lg": "0.25rem",
        "xl": "0.5rem",
        "full": "0.75rem"
      }
    }
  },
  plugins: [
    require("@tailwindcss/forms"),
    require("@tailwindcss/container-queries")
  ]
};
