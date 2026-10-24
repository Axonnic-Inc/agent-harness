---
name: motion-react
description: Motion (motion.dev, formerly Framer Motion) for React and vanilla JS — correct imports, variants, AnimatePresence exits, layout/layoutId, springs with good values, scroll-linked useScroll/useTransform, gestures, MotionConfig reducedMotion. Use when animating React UI with Motion or deciding Motion vs GSAP vs CSS.
---

# Motion (React + vanilla)

Package is `motion` (not `framer-motion`). Check current APIs with Context7 (`/motiondivision/motion`) when unsure.

```ts
import { motion, AnimatePresence, MotionConfig, useScroll, useTransform, useSpring, useMotionValue, useInView, LayoutGroup } from "motion/react"
import { animate, scroll, inView, stagger, spring } from "motion"          // vanilla / outside React
```

## Choose the tool
- CSS transition / `@starting-style` for simple hover, press, show/hide. Motion for exits, layout changes, gestures, springs, orchestration. GSAP for timelines, scroll pinning, SVG morph/draw, text splitting.
- Only animate `transform` and `opacity` for anything that moves every frame. Motion's `x`/`y`/`scale`/`rotate` shorthands are hardware-accelerated; `width`/`height`/`top` are not (use `layout` instead).

## Values that feel right
- UI feedback: `{ type: "spring", stiffness: 400, damping: 30 }` or `duration: 0.2, ease: "easeOut"`.
- Cards, panels, modals: `{ type: "spring", stiffness: 300, damping: 26 }` or `{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }`.
- Display type, page transitions: `0.6–0.8 s`, `ease: [0.22, 1, 0.36, 1]`, `staggerChildren: 0.08–0.15`.
- Playful (badges, mascots): `{ type: "spring", stiffness: 300, damping: 12 }` (visible overshoot, use sparingly). Spring params, not `bounce`, unless you want a duration-based spring.

## Patterns
```tsx
// Variants orchestrate parents and children
const list = { hidden: {}, show: { transition: { staggerChildren: 0.1, delayChildren: 0.1 } } }
const item = { hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 26 } } }
<motion.ul variants={list} initial="hidden" animate="show">{items.map(i => <motion.li key={i.id} variants={item} />)}</motion.ul>

// Exits need AnimatePresence and a stable key; mode="popLayout" keeps siblings from jumping
<AnimatePresence mode="popLayout" initial={false}>{open && <motion.div key="panel" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} />}</AnimatePresence>

// Layout animations: size/position changes become transforms; shared element across components with layoutId
<motion.div layout /> · <motion.div layoutId="tab-indicator" transition={{ type: "spring", stiffness: 500, damping: 40 }} />

// Scroll-linked (not scroll-triggered): drive values from progress
const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] })
const y = useTransform(scrollYProgress, [0, 1], [40, -40])            // parallax
const smooth = useSpring(scrollYProgress, { stiffness: 100, damping: 30 })

// Scroll-triggered once
<motion.section initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.3 }} />

// Gestures
<motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} transition={{ type: "spring", stiffness: 400, damping: 30 }} />
<motion.div drag="x" dragConstraints={{ left: -200, right: 0 }} dragElastic={0.15} />

// Reduced motion: reduce, don't remove
<MotionConfig reducedMotion="user">…</MotionConfig>      // disables transform/layout motion, keeps opacity/color
```

## Gotchas
- Next.js App Router: files using `motion/react` need `"use client"`. Exit animations only run for elements that were rendered inside `AnimatePresence` with a key.
- Don't animate on every render: keep `animate` targets stable or use `useMotionValue` + `animate()` for high-frequency updates (pointer followers).
- `layout` on an element with `border-radius` or `box-shadow` can distort during the animation; set `layout` on a wrapper or use `layout="position"`.
- Vanilla `animate(el, { x: 100 }, { type: "spring", stiffness: 300, damping: 26 })` returns controls (`.pause()`, `.time`), handy for seekable work.


## Always review visually

After any edit that changes what a page or component looks like or how it moves, look at the result before reporting: render it (dev server + Playwright screenshot, or the `web-motion-studio` shoot script), open the image, and check the exact region you changed at desktop and phone widths. A passing build, type check or test does not count as having seen it. If the change is motion, capture frames across the transition, not one still. Fix what you see, then look again.
