# Web critique pass (score from the shots, not from the code)

Read `above.png`, `full.png`, `scroll_sheet.png`, `entrance_sheet.png`, `hover_strip.png`, `reduced.png` for every viewport, and `report.md`.

| Axis | 8/10 means |
|---|---|
| Hierarchy | One clear focal point per view; eye path obvious in 1 s; nothing competes with the headline. |
| Type | One display + one UI face; sizes form a scale; measure 45–75 chars; no text under 13 px; no widows in headlines. |
| Spacing | Consistent rhythm; sections breathe; no cramped mobile padding; grid alignment visible in `full.png`. |
| Motion quality | Springs settle, no linear slides, no pops; entrance sheet shows staggered, purposeful reveals; hover strip shows feedback within 100 ms. |
| Choreography | Order tells a story (headline → support → CTA); nothing animates that doesn't need to; total entrance under ~1.2 s. |
| Performance | `report.md`: dropped frames < 5 % while scrolling, CLS < 0.05, no `transition: all`, no layout-property animation. |
| Accessibility | `reduced.png` shows all content; contrast legible; images have alt; focus states visible. |
| Responsiveness | 390 px view is designed, not squeezed: type rescaled, grid collapsed, no horizontal overflow. |
| Taste match | Matches `DESIGN.md` palette, type and motion; references' spirit, not their content. |
| Slop check | None of the bans present; it does not look like a template or an AI default. |

Then list the **three worst problems**: file/component, what the frame shows, the fix. Fix only those. Re-shoot. Re-score.
Pass when every axis ≥ 8 and `report.md` has no FAIL lines. Argue against your own work; "fine" is a 6.
