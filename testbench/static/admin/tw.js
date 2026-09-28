// Tailwind Play CDN config. Load right after the CDN script.
tailwind.config = {
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: '#151A23', 2: '#2B3240' },
        paper: '#F5F6F8',
        line: { DEFAULT: '#E2E5EA', strong: '#CDD2DA' },
        muted: '#5A6373',
        ok: { DEFAULT: '#166534', soft: '#E8F5EC' },
        warn: { DEFAULT: '#92400E', soft: '#FEF3C7' },
        bad: { DEFAULT: '#B42318', soft: '#FDECEA' },
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
