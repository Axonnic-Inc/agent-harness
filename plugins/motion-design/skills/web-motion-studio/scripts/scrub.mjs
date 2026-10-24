#!/usr/bin/env node
// scrub.mjs — contact sheet of a scroll-driven section: scrolls the page so the section travels through the viewport
// in N equal steps (from "top enters at the bottom" to "bottom leaves at the top") and tiles the viewport frames.
//   node ~/.claude/skills/web-motion-studio/scripts/scrub.mjs --url http://localhost:3000/ --selector "#pilots" --out scrub.png
//        [--steps 16] [--viewport 1440x900] [--cols 8] [--settle 350] [--reduced]
import { chromium } from 'playwright';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const argv = process.argv.slice(2);
const flag = (n, d) => { const i = argv.indexOf(`--${n}`); if (i < 0) return d; const v = argv[i + 1]; return v === undefined || v.startsWith('--') ? true : v; };
const url = flag('url', null), selector = flag('selector', null), out = flag('out', 'scrub.png');
if (!url || !selector) { console.error('usage: scrub.mjs --url <url> --selector <css> [--steps 16] [--viewport 1440x900] [--cols 8] [--out scrub.png] [--reduced]'); process.exit(2); }
const steps = Number(flag('steps', 16)), cols = Number(flag('cols', 8)), settle = Number(flag('settle', 350));
const [vw, vh] = String(flag('viewport', '1440x900')).split('x').map(Number);
const reduced = flag('reduced', false) === true;
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, deviceScaleFactor: 1, ...(reduced ? { reducedMotion: 'reduce' } : {}) });
const page = await ctx.newPage();
await page.goto(url, { waitUntil: 'load', timeout: 60000 }); await page.waitForTimeout(800);
const box = await page.evaluate((sel) => { const el = document.querySelector(sel); if (!el) return null; const r = el.getBoundingClientRect(); return { top: r.top + scrollY, height: r.height, docH: document.documentElement.scrollHeight }; }, selector);
if (!box) { console.error(`selector not found: ${selector}`); process.exit(2); }
const start = Math.max(0, box.top - vh), end = Math.min(box.docH - vh, box.top + box.height);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wms-scrub-')); const ys = [];
for (let i = 0; i < steps; i++) { const y = start + (end - start) * (i / (steps - 1)); ys.push(Math.round(y)); await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'instant' }), y); await page.waitForTimeout(settle); await page.screenshot({ path: path.join(tmp, `f${String(i).padStart(2, '0')}.png`) }); }
await browser.close();
const rows = Math.ceil(steps / cols); const scale = Math.round(2400 / cols);
const r = spawnSync('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', '-framerate', '1', '-i', path.join(tmp, 'f%02d.png'), '-vf', `scale=${scale}:-1,tile=${cols}x${rows}:padding=3:margin=3:color=#202020`, '-frames:v', '1', '-update', '1', out]);
fs.rmSync(tmp, { recursive: true, force: true });
if (r.status !== 0) { console.error('ffmpeg failed', r.stderr?.toString()); process.exit(1); }
console.log(`${out}: ${steps} frames of ${selector} scrolling from y=${start | 0} to y=${end | 0} at ${vw}x${vh}${reduced ? ' (reduced motion)' : ''}, ${cols} per row. Read it: does the motion have a cause, does it read at a glance, does anything pop or slide?`);
