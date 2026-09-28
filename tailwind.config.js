/** Tailwind build for LOCAL_ASSETS=1.
 *
 *  This must define the same theme as the inline CDN config in templates/_head.html, because a
 *  class that exists in one build and not the other produces a page that silently loses its
 *  colour. Both read their values from the CSS custom properties that _head.html emits, so
 *  neither file hard-codes a palette and the surface tokens flip with the theme class.
 */
const scale = (name) =>
  Object.fromEntries(
    [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((step) => [
      step,
      `rgb(var(--${name}-${step}) / <alpha-value>)`,
    ]),
  );

const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  darkMode: "class",
  content: ["./testbench/templates/**/*.html", "./testbench/static/**/*.js"],
  theme: {
    extend: {
      fontFamily: { sans: ["Plus Jakarta Sans", "ui-sans-serif", "system-ui", "sans-serif"] },
      colors: {
        brand: { DEFAULT: token("brand-500"), ...scale("brand") },
        nav: token("nav-rgb"),
        // Surface tokens: one set of markup, two themes.
        surface: token("surface"),
        "surface-subtle": token("surface-subtle"),
        card: token("card"),
        line: token("line"),
        ink: token("ink"),
        "ink-muted": token("ink-muted"),
        "ink-subtle": token("ink-subtle"),
      },
      height: { 18: "4.5rem" },
      spacing: { 18: "4.5rem" },
    },
  },
  // Styles the rendered Markdown in the docs. The standalone Tailwind CLI that
  // scripts/build-assets.sh downloads bundles the first-party plugins, so this needs no install.
  plugins: [require("@tailwindcss/typography")],
};
