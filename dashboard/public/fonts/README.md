# Fonts

## Satoshi (brand / hierarchy typeface)

The typography system is already wired for Satoshi — this folder just needs the
binary font files, which are not committed here.

### Required file(s)

Place the **variable** Satoshi file(s) directly in this folder:

- `Satoshi-Variable.woff2`  (preferred)
- `Satoshi-Variable.ttf`    (optional fallback)

The `@font-face` in `app/globals.css` references exactly these paths
(`/fonts/Satoshi-Variable.woff2`, `/fonts/Satoshi-Variable.ttf`) with a
`font-weight: 500 700` range. No code change is needed once the file is here.

### Where to get it

Satoshi is distributed for free by Fontshare: https://www.fontshare.com/fonts/satoshi
Download the family and copy the variable file(s) into this directory.

### How it's wired

- `@font-face { font-family: 'Satoshi'; ... }` — `dashboard/app/globals.css`
- `--font-display: 'Satoshi', var(--font-inter), 'Inter', system-ui, sans-serif;` — token in `:root`
- `font-display` Tailwind utility → used by headings (`h1–h6`) and `CardTitle`
- Until the file is present, the stack falls back to Inter automatically (no build error).

Inter (the clinical/UI typeface) is self-hosted via `next/font` and needs no files here.
