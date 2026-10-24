---
name: web-motion-studio
description: Shoot → critique → fix loop for web pages with premium motion — taste file, Playwright shots (viewports, scroll sheet, entrance and hover strips, reduced motion, frame-time and CSS checks), rubric scoring until every axis is 8/10. Use for landing pages, heroes, scroll sections, micro-interactions, or whenever Claude must see its own web work before showing it.
---

# web-motion-studio

Claude cannot judge motion from code. Shoot the page, look at the frames, score, fix, repeat. Technique comes from the installed
skills (gsap-core/timeline/scrolltrigger/react/plugins, animate, animation-performance, review-animations, apple-design, motion-react, micro-interaction,
svg-animation, threejs-animation, shader-glsl); this skill is the loop that makes them land.

## 0. Taste file
If the project has no `DESIGN.md`, create one from `~/.claude/skills/web-motion-studio/DESIGN.md` using three reference sites the user
names (ask if none). References guide taste only: never copy their words, logos or images.

## 1. Build
- Motion: springs/physical easing (Motion `spring`, GSAP with custom eases or `gsap-core`/`gsap-timeline` skills). Animate only `transform` and `opacity` (`animation-performance`). No `transition: all`.
- Entrances: staggered, purposeful, under 600 ms for UI, heavier for display type. Scroll: `gsap-scrolltrigger` or `motion` scroll, pinned sections sparingly. Honour `prefers-reduced-motion` by reducing, not removing (`gsap-core` matchMedia, `micro-interaction` MotionConfig, `animate`).
- Typography and layout: one display face, one UI face, one accent; left-aligned editorial grid unless the taste file says otherwise.
- Bans: centred all-caps on a gradient, purple-to-blue "AI magic" gradients, glow, fade-in-everything, corner labels, floating particle bursts, generic 3-column icon grids, lorem ipsum, hero stock photos.

## 2. Shoot
```
node ~/.claude/skills/web-motion-studio/scripts/shoot.mjs --url http://localhost:3000 [--hover ".cta"] [--click "#menu"] [--viewports 1440x900,390x844] [--dark]
```
Writes `shots/iter-NN/<viewport>/`: `above.png`, `full.png`, `scroll_sheet.png` (page at 7 scroll positions), `entrance_sheet.png`
(load plus the first 2.5 s at 10 fps from a video recording; `--entrance` changes it), `hover_strip.png` / `click_strip.png` (12 frames over 600 ms), `reduced.png`
(prefers-reduced-motion), plus `report.md` with automated checks: dropped frames while scrolling, layout shift (CLS), `transition: all`,
layout-property animations, text under 13 px, horizontal overflow, `will-change` count, images without alt, console errors, fonts still loading.
Appends one line per round to `shots/LOG.md`.

Scroll-driven sections: `node ~/.claude/skills/web-motion-studio/scripts/scrub.mjs --url <url> --selector "#section" --steps 16 --out scrub.png`
tiles 16 viewport frames as the section travels through the viewport — the contact sheet for scroll animations. Read it like a film strip.

## 3. Critique
Read every image (Read tool). Score with `~/.claude/skills/web-motion-studio/prompts/critique.md`: hierarchy, type, spacing, motion quality,
timing/choreography, performance, accessibility, responsiveness, taste-file match, slop check. List the three worst problems with the
file and the fix. Use `review-animations` / `improve-animations` skills for the motion axis.

## 4. Fix and re-shoot
Fix only the listed problems, re-run shoot, re-score. Stop at ≥ 8/10 on every axis, zero failed automated checks, or after 6 rounds
(then report what is still wrong). For big pages, let a fresh subagent be the critic so the builder does not grade itself.

## Tools
Playwright MCP for interactive exploration (snapshot/screenshot/click), Chrome DevTools MCP for performance traces and console/network
when `report.md` shows dropped frames, Context7 MCP for current GSAP/Motion/Three docs. ffmpeg is required for the sheets/strips.


## Always review visually

After any edit that changes what a page or component looks like or how it moves, look at the result before reporting: render it (dev server + Playwright screenshot, or the `web-motion-studio` shoot script), open the image, and check the exact region you changed at desktop and phone widths. A passing build, type check or test does not count as having seen it. If the change is motion, capture frames across the transition, not one still. Fix what you see, then look again.
