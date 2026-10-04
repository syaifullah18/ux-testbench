// Tailwind Play CDN config. Load right after the CDN script.
tailwind.config = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Neutral and status colours follow the CSS variables in app.css, so they flip with
        // <html class="dark">.
        ink: { DEFAULT: 'var(--ink)', 2: 'var(--ink)', muted: 'var(--muted)', subtle: 'var(--subtle)' },
        paper: 'var(--paper)',
        card: 'var(--card)',
        surface: 'var(--card)',
        nav: 'var(--nav)',
        line: { DEFAULT: 'var(--line)', strong: 'var(--line-strong)' },
        muted: 'var(--muted)',
        ok: { DEFAULT: 'var(--ok-fg)', soft: 'var(--ok-bg)', ink: 'var(--ok-fg)' },
        warn: { DEFAULT: 'var(--warn-fg)', soft: 'var(--warn-bg)', ink: 'var(--warn-fg)', line: 'var(--warn-fg)' },
        bad: { DEFAULT: 'var(--bad-fg)', soft: 'var(--bad-bg)', ink: 'var(--bad-fg)' },
        // Per-study accent. Set on <html> or body style.
        brand: {
          DEFAULT: 'var(--brand)',
          soft: 'var(--brand-soft)',
          line: 'var(--brand-line)',
          strong: 'var(--brand-strong)',
          on: 'var(--on-brand)',
        },
      },
      fontFamily: {
        sans: ['"Hanken Grotesk"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
};
