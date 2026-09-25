/** Tailwind build for LOCAL_ASSETS=1.
 *
 *  This must define the same theme as the inline CDN config in templates/_head.html, because a
 *  class that exists in one build and not the other produces a page that silently loses its
 *  colour. Both read their values from the CSS custom properties that _head.html emits from the
 *  project's own primary colour, so neither file hard-codes a palette.
 */
const brand = Object.fromEntries(
  [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((step) => [
    step,
    `rgb(var(--brand-${step}) / <alpha-value>)`,
  ]),
);

module.exports = {
  content: ["./testbench/templates/**/*.html", "./testbench/static/**/*.js"],
  theme: {
    extend: {
      fontFamily: { sans: ["Plus Jakarta Sans", "ui-sans-serif", "system-ui", "sans-serif"] },
      colors: {
        brand: { DEFAULT: "rgb(var(--brand-500) / <alpha-value>)", ...brand },
        nav: "rgb(var(--nav-rgb) / <alpha-value>)",
      },
      height: { 18: "4.5rem" },
      spacing: { 18: "4.5rem" },
    },
  },
};
