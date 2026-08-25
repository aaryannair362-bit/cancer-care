import type { Config } from 'tailwindcss'

/*
 * Aivana Oncology OS — Tailwind theme
 * Tokens are defined in app/globals.css (:root). This file only exposes them
 * as utilities. Colors use hsl(var(--token)) so opacity modifiers work.
 */
const config: Config = {
  darkMode: ['class'], // dark palette intentionally undefined — not specified in the style rules
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      fontFamily: {
        sans: ['var(--font-sans)'],
        display: ['var(--font-display)'],
      },
      colors: {
        // shadcn/ui core
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },

        // Aivana brand
        brand: {
          soft: 'hsl(var(--brand-soft))',
          DEFAULT: 'hsl(var(--brand))',
          strong: 'hsl(var(--brand-strong))',
          deep: 'hsl(var(--brand-deep))',
          indigo: 'hsl(var(--brand-indigo))',
        },
        charcoal: 'hsl(var(--charcoal))',
        highlight: 'hsl(var(--highlight))',

        // Surfaces
        surface: {
          DEFAULT: 'hsl(var(--surface))',
          app: 'hsl(var(--surface-app))',
          elevated: 'hsl(var(--surface-elevated))',
          panel: 'hsl(var(--surface-panel))',
          clinical: 'hsl(var(--surface-clinical))',
        },
        'input-background': 'hsl(var(--input-background))',

        // Text
        supporting: 'hsl(var(--supporting))',
        metadata: 'hsl(var(--metadata))',
        disabled: 'hsl(var(--disabled))',

        // Borders
        divider: 'hsl(var(--divider))',
        emphasized: 'hsl(var(--border-emphasized))',

        // Semantic (clinical status)
        success: {
          DEFAULT: 'hsl(var(--success))',
          subtle: 'hsl(var(--success-subtle))',
          strong: 'hsl(var(--success-strong))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          subtle: 'hsl(var(--warning-subtle))',
          strong: 'hsl(var(--warning-strong))',
        },
        critical: {
          DEFAULT: 'hsl(var(--critical))',
          subtle: 'hsl(var(--critical-subtle))',
          strong: 'hsl(var(--critical-strong))',
        },
        information: {
          DEFAULT: 'hsl(var(--information))',
          subtle: 'hsl(var(--information-subtle))',
          strong: 'hsl(var(--information-strong))',
        },

        // AI
        ai: {
          DEFAULT: 'hsl(var(--ai))',
          highlight: 'hsl(var(--ai-highlight))',
          panel: 'hsl(var(--ai-panel))',
          emphasis: 'hsl(var(--ai-emphasis))',
        },

        // Clinical chart series
        chart: {
          1: 'hsl(var(--chart-1))',
          2: 'hsl(var(--chart-2))',
          3: 'hsl(var(--chart-3))',
          4: 'hsl(var(--chart-4))',
          5: 'hsl(var(--chart-5))',
          6: 'hsl(var(--chart-6))',
          7: 'hsl(var(--chart-7))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
        pill: '9999px',
      },
      boxShadow: {
        // Soft morphism — broad, soft, low contrast
        'soft-sm': '0 1px 2px 0 hsl(215 25% 27% / 0.05)',
        soft: '0 1px 2px 0 hsl(215 25% 27% / 0.04), 0 8px 24px -6px hsl(215 25% 27% / 0.08)',
        'soft-lg': '0 2px 4px 0 hsl(215 25% 27% / 0.05), 0 16px 40px -8px hsl(215 25% 27% / 0.10)',
        // Restricted neumorphism — small / focused controls only
        neu: 'inset 0 1px 0 0 hsl(0 0% 100% / 0.7), 0 1px 3px 0 hsl(215 25% 27% / 0.10)',
        'neu-inset': 'inset 2px 2px 5px hsl(215 25% 27% / 0.10), inset -2px -2px 5px hsl(0 0% 100% / 0.7)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
export default config
