/** @type {import('tailwindcss').Config} */
// The Arbiter's Report token system (frontend-design-guide.md).
// SIX colors only. THREE font roles. Do not add hues or faces beyond these.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        felt: "#1E3A2E",
        "felt-light": "#274A3A",
        paper: "#EDE6D3",
        "paper-line": "#D8CDB0",
        ink: "#1E1B16",
        "ink-soft": "#58524A",
        chalk: "#F3EFE3",
        stamp: "#B3402E",
        "stamp-soft": "#C97260",
        brass: "#C9A24B",
        "brass-dim": "#8C7238",
      },
      fontFamily: {
        // Display / data — headers, stat numbers, usernames, kickers, axis labels.
        mono: ["'JetBrains Mono'", "monospace"],
        // Body — verdict prose, descriptions, longer copy.
        serif: ["'Libre Caslon Text'", "serif"],
        // Marginalia — ONLY the ?? / ?! glyph characters.
        marker: ["'Permanent Marker'", "cursive"],
      },
      keyframes: {
        "stamp-in": {
          "0%": { opacity: "0", transform: "rotate(-6deg) scale(2.4)" },
          "60%": { opacity: "1" },
          "100%": { opacity: "1", transform: "rotate(-6deg) scale(1)" },
        },
        tick: {
          to: { transform: "rotate(380deg) translate(-1px,-15px)" },
        },
        blink: {
          "50%": { opacity: "0" },
        },
        "tip-in": {
          from: { opacity: "0", transform: "translate(-50%,-115%)" },
          to: { opacity: "1", transform: "translate(-50%,-130%)" },
        },
      },
      animation: {
        "stamp-in": "stamp-in 0.5s cubic-bezier(.2,1.4,.4,1) both",
        blink: "blink 1s step-end infinite",
        "tip-in": "tip-in 0.12s ease-out",
      },
    },
  },
  plugins: [],
};
