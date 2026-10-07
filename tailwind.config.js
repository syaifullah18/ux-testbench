/** Tailwind build for LOCAL_ASSETS=1.
 *
 *  Must define the same theme as testbench/static/admin/tw.js (the CDN config), because a class
 *  that exists in one build and not the other produces a page that silently loses its colour.
 *  Both map the CSS variables in testbench/static/admin/app.css, so neither hard-codes a palette
 *  and every colour flips with the theme class and follows a study's own --brand.
 */
const c = (name) => `color-mix(in srgb, var(--${name}) calc(<alpha-value> * 100%), transparent)`;

const status = (name) => ({
  DEFAULT: c(`${name}-fg`),
  soft: c(`${name}-bg`),
  line: c(`${name}-line`),
  ink: c(`${name}-fg`),
});

const brand = {
  DEFAULT: c("brand"),
  soft: c("brand-soft"),
  line: c("brand-line"),
  strong: c("brand-strong"),
  on: c("on-brand"),
  ...Object.fromEntries([50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((s) => [s, c(`brand-${s}`)])),
};

module.exports = {
  darkMode: "class",
  content: ["./testbench/templates/**/*.html", "./testbench/static/**/*.js"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: c("ink"), 2: c("ink"), muted: c("muted"), subtle: c("subtle") },
        muted: c("muted"),
        subtle: c("subtle"),
        paper: c("paper"),
        sub: c("sub"),
        card: c("card"),
        surface: { DEFAULT: c("surface"), subtle: c("surface-subtle") },
        nav: c("nav"),
        line: { DEFAULT: c("line"), strong: c("line-strong") },
        ok: status("ok"),
        warn: status("warn"),
        bad: status("bad"),
        info: status("info"),
        brand,
      },
      fontFamily: {
        sans: ['"Hanken Grotesk"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      height: { 18: "4.5rem" },
      spacing: { 18: "4.5rem" },
    },
  },
  // Styles the rendered Markdown in the docs. The standalone Tailwind CLI that
  // scripts/build-assets.sh downloads bundles the first-party plugins, so this needs no install.
  plugins: [require("@tailwindcss/typography")],
};
