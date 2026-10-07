// Tailwind Play CDN config for every page. Load right after the CDN script.
//
// One design system: the colours below are the CSS variables in app.css, so they flip with
// <html class="dark"> and follow a study's own --brand. tailwind.config.js (the LOCAL_ASSETS=1
// build) must define the same names; tests/test_ui_tokens.py checks the two stay in step.
(function () {
  // A token with Tailwind's opacity modifier (bg-ink/40) still working on a hex variable.
  var c = function (name) {
    return 'color-mix(in srgb, var(--' + name + ') calc(<alpha-value> * 100%), transparent)';
  };
  var status = function (name) {
    return { DEFAULT: c(name + '-fg'), soft: c(name + '-bg'), line: c(name + '-line'), ink: c(name + '-fg') };
  };
  var brand = { DEFAULT: c('brand'), soft: c('brand-soft'), line: c('brand-line'), strong: c('brand-strong'), on: c('on-brand') };
  [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].forEach(function (step) { brand[step] = c('brand-' + step); });

  tailwind.config = {
    darkMode: 'class',
    theme: {
      extend: {
        colors: {
          ink: { DEFAULT: c('ink'), 2: c('ink'), muted: c('muted'), subtle: c('subtle') },
          muted: c('muted'),
          subtle: c('subtle'),
          paper: c('paper'),
          sub: c('sub'),
          card: c('card'),
          surface: { DEFAULT: c('surface'), subtle: c('surface-subtle') },
          nav: c('nav'),
          line: { DEFAULT: c('line'), strong: c('line-strong') },
          ok: status('ok'),
          warn: status('warn'),
          bad: status('bad'),
          info: status('info'),
          brand: brand,
        },
        fontFamily: {
          sans: ['"Hanken Grotesk"', 'system-ui', 'sans-serif'],
          mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        },
        height: { 18: '4.5rem' },
        spacing: { 18: '4.5rem' },
      },
    },
  };
})();
