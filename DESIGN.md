# DESIGN

Terminal cockpit for an active FX trader. Density and signal over decoration.
Evolve-navy direction: keep the dark navy base, add amber as a second accent,
reserve green/red strictly for sentiment.

## Color (dark, fixed — not a toggle)
- `--navy #030712` page background
- `--panel #070b16` / `--panel2 #0d1320` raised surfaces (toolbar, board tiles, table head)
- `--border #1c2740` hairlines
- `--blue #2563eb` primary accent (sources, primary action)
- `--amber #f59e0b` secondary accent (active state, sort caret, cursor stripe, labels)
- `--up #16a34a` / `--down #dc2626` sentiment only, never decorative
- text: `--txt #e5e7eb` / `--txt2 #94a3b8` / `--muted #5b6675`

Color = signal. A green or red pixel always means bullish/bearish. Neutral data
stays in the grey ramp.

## Typography
- Mono everywhere (`ui-monospace` stack). Data UI, not prose.
- Sizes: 9–11px labels/data, 12px body/detail, 14px brand. Tabular numerals on
  all scores, counts, clock.
- ALL-CAPS for column headers, labels, pills.

## Layout
- Fixed full-height shell: header (h-11) / sticky command toolbar / sentiment
  board / scrolling table. No page-level container, no cards in the feed.
- Board tiles are the only "card", and only because each bloc is a discrete
  instrument readout.
- Dense table rows; detail expands inline (never a modal).

## Motion
- 0.15s ease-out on hovers; 0.18s fadeIn on detail expand. No layout animation,
  no bounce.

## Interaction
- Keyboard: `/` focus search, `j`/`k` (or arrows) move row cursor, `Enter`/`o`
  expand, `r` sync, `Esc` blur.
- Click a board tile to filter the feed by that bloc.
- Click any column header to sort; amber caret shows key + direction.

## Bans
No side-stripe accents (the cursor uses inset box-shadow, full-width),
no gradient text, no glassmorphism, no em dashes in copy.
