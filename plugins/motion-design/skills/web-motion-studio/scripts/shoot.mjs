#!/usr/bin/env node
// shoot.mjs — make Claude look at its own web page. Screenshots, scroll sheet, entrance/hover/click strips, reduced-motion view,
// frame-time + layout-shift + CSS checks. Output: shots/iter-NN/<viewport>/… and report.md. Requires ffmpeg for the sheets/strips.
//   node ~/.claude/skills/web-motion-studio/scripts/shoot.mjs --url http://localhost:3000 [--hover ".cta"] [--click "#menu"]
//        [--viewports 1440x900,390x844] [--out shots/iter-01] [--dark] [--entrance 2] [--steps 6] [--settle 700] [--headed] [--no-video]
import { chromium } from 'playwright';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const argv = process.argv.slice(2);
const flag = (n, d) => { const i = argv.indexOf(`--${n}`); if (i < 0) return d; const v = argv[i + 1]; return v === undefined || v.startsWith('--') ? true : v; };
const url = flag('url', null);
if (!url) { console.error('usage: node shoot.mjs --url http://localhost:3000 [--hover sel] [--click sel] [--viewports 1440x900,390x844] [--out dir] [--dark] [--entrance 2] [--steps 6] [--headed] [--no-video]'); process.exit(2); }
const viewports = String(flag('viewports', '1440x900,390x844')).split(',').map(s => { const [w, h] = s.split('x').map(Number); return { w, h, name: `${w}x${h}` }; });
const hover = flag('hover', null), click = flag('click', null);
const dark = flag('dark', false) === true, headed = flag('headed', false) === true, noVideo = flag('no-video', false) === true;
const entrance = Number(flag('entrance', 2)), steps = Number(flag('steps', 6)), settle = Number(flag('settle', 700));
const hasFfmpeg = spawnSync('ffmpeg', ['-version']).status === 0;
if (!hasFfmpeg) console.error('warning: ffmpeg not found — sheets and strips will be skipped (brew install ffmpeg)');
const ff = (args) => hasFfmpeg && spawnSync('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', ...args], { stdio: 'inherit' }).status === 0;
const nextIter = (root = 'shots') => { fs.mkdirSync(root, { recursive: true }); const n = fs.readdirSync(root).filter(d => /^iter-\d+$/.test(d)).length + 1; return path.join(root, `iter-${String(n).padStart(2, '0')}`); };
const outDir = String(flag('out', null) || nextIter());
fs.mkdirSync(outDir, { recursive: true });

const PROBE = () => {
  window.__probe = { longTasks: [], cls: 0 };
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__probe.longTasks.push([Math.round(e.startTime), Math.round(e.duration)]); }).observe({ type: 'longtask', buffered: true }); } catch {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__probe.cls += e.value; }).observe({ type: 'layout-shift', buffered: true }); } catch {}
};
const TIMING = async (steps) => {
  window.scrollTo(0, 0); await new Promise(r => setTimeout(r, 250));
  const H = Math.max(0, document.documentElement.scrollHeight - innerHeight);
  const gaps = []; let last = performance.now(), on = true;
  const tick = (t) => { gaps.push(t - last); last = t; if (on) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  const t0 = performance.now();
  for (let i = 1; i <= steps; i++) { window.scrollTo({ top: H * i / steps, behavior: 'smooth' }); await new Promise(r => setTimeout(r, 500)); }
  on = false; await new Promise(r => setTimeout(r, 60));
  const g = gaps.slice(1); const dropped = g.filter(x => x > (1000 / 60) * 1.6).length;
  return { frames: g.length, droppedPct: g.length ? Math.round(dropped / g.length * 1000) / 10 : 0, worstMs: Math.round(Math.max(0, ...g)), scrollDistance: H, durationMs: Math.round(performance.now() - t0) };
};
const CHECKS = () => {
  const desc = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  const uniq = (a) => Array.from(new Set(a));
  const all = Array.from(document.querySelectorAll('body *'));
  const layoutProps = /\b(width|height|top|left|right|bottom|margin|padding|inset|font-size)\b/;
  const transitionAll = [], layoutAnim = [], smallText = [], willChange = [];
  for (const el of all) {
    const s = getComputedStyle(el);
    const hasDur = (s.transitionDuration || '').split(',').some(d => parseFloat(d) > 0);
    if (hasDur && /\ball\b/.test(s.transitionProperty)) transitionAll.push(desc(el));
    else if (hasDur && layoutProps.test(s.transitionProperty)) layoutAnim.push(desc(el) + ' [' + s.transitionProperty + ']');
    if (s.willChange && s.willChange !== 'auto') willChange.push(desc(el));
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim())) { const px = parseFloat(s.fontSize); if (px < 13) smallText.push(desc(el) + ' ' + px + 'px'); }
  }
  const layoutKeyframes = [];
  for (const sh of Array.from(document.styleSheets)) { let rules; try { rules = sh.cssRules; } catch { continue; }
    for (const r of Array.from(rules)) if (r.type === CSSRule.KEYFRAMES_RULE) { const txt = Array.from(r.cssRules).map(k => k.cssText).join(' '); if (/\b(width|height|top|left|right|bottom|margin|padding|font-size)\s*:/.test(txt)) layoutKeyframes.push(r.name); } }
  return {
    title: document.title, h1Count: document.querySelectorAll('h1').length, scrollHeight: document.documentElement.scrollHeight,
    transitionAllCount: transitionAll.length, transitionAll: uniq(transitionAll).slice(0, 8),
    layoutAnimCount: layoutAnim.length, layoutAnim: uniq(layoutAnim).slice(0, 8), layoutKeyframes: uniq(layoutKeyframes),
    smallTextCount: smallText.length, smallText: uniq(smallText).slice(0, 8), willChangeCount: willChange.length,
    horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    imagesWithoutAlt: Array.from(document.images).filter(i => !i.hasAttribute('alt') && i.getBoundingClientRect().width > 0).length,
    fontsLoading: Array.from(document.fonts).filter(f => f.status === 'loading').length, fontsCount: document.fonts.size,
  };
};
// Count content elements (text or images) that are actually visible in the first viewport: in view, sized, opaque, not clipped away.
const VISIBLE = () => { let visible = 0, total = 0; for (const el of Array.from(document.querySelectorAll('body *'))) {
  const hasText = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()); if (!hasText && el.tagName !== 'IMG') continue;
  total++; const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0 || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) continue;
  let op = 1, node = el; while (node && node !== document.documentElement) { const cs = getComputedStyle(node); if (cs.visibility === 'hidden' || cs.display === 'none') { op = 0; break; } op *= parseFloat(cs.opacity); if (cs.clipPath && cs.clipPath !== 'none' && /inset\(\s*(50|[6-9]\d|100)%/.test(cs.clipPath)) { op = 0; break; } node = node.parentElement; }
  if (op >= 0.05) visible++; } return { visible, total }; };

async function strip(page, vp, sel, action, file) {
  const loc = page.locator(sel).filter({ visible: true }).first(); if (!(await loc.count())) return { error: `no visible element for: ${sel}` };
  try { await loc.scrollIntoViewIfNeeded({ timeout: 5000 }); } catch (e) { return { error: `could not scroll to ${sel}: ${e.message.split('\n')[0]}` }; }
  await page.waitForTimeout(300);
  const box = await loc.boundingBox(); if (!box) return { error: `no box for ${sel}` };
  const pad = 32, x = Math.max(0, box.x - pad), y = Math.max(0, box.y - pad);
  const clip = { x, y, width: Math.max(8, Math.min(vp.w - x, box.width + 2 * pad)), height: Math.max(8, Math.min(vp.h - y, box.height + 2 * pad)) };
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wms-strip-')); const stamps = []; const t0 = Date.now();
  if (action === 'hover') await loc.hover(); else await loc.click({ noWaitAfter: true });
  for (let i = 0; i < 12; i++) { await page.screenshot({ path: path.join(tmp, `f${String(i).padStart(2, '0')}.png`), clip }); stamps.push(Date.now() - t0); const wait = (i + 1) * 50 - (Date.now() - t0); if (wait > 0) await page.waitForTimeout(wait); }
  ff(['-framerate', '1', '-i', path.join(tmp, 'f%02d.png'), '-vf', 'scale=240:-1,tile=12x1:padding=2:color=#202020', '-frames:v', '1', '-update', '1', file]);
  fs.rmSync(tmp, { recursive: true, force: true });
  return { stampsMs: stamps };
}

const browser = await chromium.launch({ headless: !headed });
const results = [];
for (const vp of viewports) {
  const dir = path.join(outDir, vp.name); fs.mkdirSync(dir, { recursive: true });
  const mobile = vp.w < 500; const dsf = mobile ? 2 : 1;
  const videoDir = noVideo ? null : fs.mkdtempSync(path.join(os.tmpdir(), 'wms-video-'));
  const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, deviceScaleFactor: dsf, colorScheme: dark ? 'dark' : 'light', isMobile: mobile, hasTouch: mobile, ...(videoDir ? { recordVideo: { dir: videoDir, size: { width: vp.w, height: vp.h } } } : {}) });
  await ctx.addInitScript(PROBE);
  const page = await ctx.newPage(); const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message)); page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 200)); });
  const nav0 = Date.now();
  try { await page.goto(url, { waitUntil: 'load', timeout: 60000 }); } catch (e) { console.error(`[${vp.name}] navigation failed: ${e.message}`); results.push({ viewport: vp.name, error: e.message }); await ctx.close(); continue; }
  const loadMs = Date.now() - nav0;
  await page.waitForTimeout(entrance * 1000 + settle);
  const normalVisible = await page.evaluate(VISIBLE);
  await page.screenshot({ path: path.join(dir, 'above.png') });
  // Scroll through first so in-view reveals have fired, then capture the full page from the top.
  const H = await page.evaluate(() => Math.max(0, document.documentElement.scrollHeight - innerHeight));
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wms-scroll-'));
  for (let i = 0; i <= steps; i++) { await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'smooth' }), H * i / steps); await page.waitForTimeout(settle); await page.screenshot({ path: path.join(tmp, `s${String(i).padStart(2, '0')}.png`) }); }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' })); await page.waitForTimeout(settle);
  await page.screenshot({ path: path.join(dir, 'full.png'), fullPage: true, timeout: 90000 });
  ff(['-framerate', '1', '-i', path.join(tmp, 's%02d.png'), '-vf', `scale=${mobile ? 200 : 320}:-1,tile=${steps + 1}x1:padding=4:color=#202020`, '-frames:v', '1', '-update', '1', path.join(dir, 'scroll_sheet.png')]);
  fs.rmSync(tmp, { recursive: true, force: true });
  const timing = await page.evaluate(TIMING, steps);
  const safeStrip = (...a) => strip(...a).catch((e) => ({ error: e.message.split('\n')[0] }));
  const hoverRes = hover ? await safeStrip(page, vp, hover, 'hover', path.join(dir, 'hover_strip.png')) : null;
  const clickRes = click ? await safeStrip(page, vp, click, 'click', path.join(dir, 'click_strip.png')) : null;
  await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(300);
  const checks = await page.evaluate(CHECKS);
  const probe = await page.evaluate(() => window.__probe || { longTasks: [], cls: 0 });
  const video = noVideo ? null : page.video();
  await ctx.close();
  if (video) { try { const vpth = await video.path(); const span = entrance + 0.5; const rows = Math.max(1, Math.ceil(span * 10 / 10)); ff(['-i', vpth, '-vf', `fps=10,trim=duration=${span},scale=${mobile ? 160 : 240}:-1,tile=10x${rows}:padding=2:color=#202020`, '-frames:v', '1', '-update', '1', path.join(dir, 'entrance_sheet.png')]); /* trim inside the graph: -t as an output option never fires because tile emits one frame */ } catch (e) { console.error('video:', e.message); } fs.rmSync(videoDir, { recursive: true, force: true }); }
  const rctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, deviceScaleFactor: dsf, reducedMotion: 'reduce', colorScheme: dark ? 'dark' : 'light', isMobile: mobile, hasTouch: mobile });
  const rpage = await rctx.newPage(); let reduced = null;
  try { await rpage.goto(url, { waitUntil: 'load', timeout: 60000 }); await rpage.waitForTimeout(entrance * 1000 + settle); await rpage.screenshot({ path: path.join(dir, 'reduced.png') }); const rv = await rpage.evaluate(VISIBLE); reduced = { normal: normalVisible.visible, reduced: rv.visible, total: rv.total }; } catch (e) { reduced = { error: e.message }; }
  await rctx.close();
  results.push({ viewport: vp.name, loadMs, timing, checks, probe: { longTasks: probe.longTasks.length, longestTaskMs: Math.max(0, ...probe.longTasks.map(t => t[1])), cls: Math.round(probe.cls * 1000) / 1000 }, errors: errors.slice(0, 10), reduced, hover: hoverRes, click: clickRes });
}
await browser.close();

// ---- report
const lines = [`# shoot report — ${url}`, '', `out: ${outDir}`, ''];
let fails = 0, warns = 0;
const F = (ok, msg) => { if (ok) return `- PASS ${msg}`; fails++; return `- FAIL ${msg}`; };
const Wn = (ok, msg) => { if (ok) return `- PASS ${msg}`; warns++; return `- WARN ${msg}`; };
for (const r of results) {
  lines.push(`## ${r.viewport}`);
  if (r.error) { lines.push(`- FAIL navigation: ${r.error}`); fails++; continue; }
  const c = r.checks, t = r.timing, p = r.probe;
  lines.push(`images: above.png · full.png · scroll_sheet.png · entrance_sheet.png${r.hover ? ' · hover_strip.png' : ''}${r.click ? ' · click_strip.png' : ''} · reduced.png`);
  lines.push(`load ${r.loadMs} ms · page height ${c.scrollHeight}px · title "${c.title}" · h1 ×${c.h1Count} · fonts ${c.fontsCount}`);
  lines.push(F(t.droppedPct <= 5, `dropped frames while scrolling: ${t.droppedPct}% (worst ${t.worstMs} ms over ${t.frames} frames; headless Chromium, relative measure)`));
  lines.push(F(p.cls <= 0.05, `layout shift (CLS): ${p.cls}${p.longTasks ? ` · long tasks ×${p.longTasks} (longest ${p.longestTaskMs} ms)` : ''}`));
  lines.push(F(c.transitionAllCount === 0, `transition: all → ${c.transitionAllCount}${c.transitionAll.length ? ' (' + c.transitionAll.join(', ') + ')' : ''}`));
  lines.push(F(c.layoutAnimCount === 0 && c.layoutKeyframes.length === 0, `layout-property animation (width/height/top/left/margin/padding): transitions ×${c.layoutAnimCount}${c.layoutAnim.length ? ' (' + c.layoutAnim.join(', ') + ')' : ''}, keyframes ${c.layoutKeyframes.length ? c.layoutKeyframes.join(', ') : 'none'}`));
  lines.push(F(!c.horizontalOverflow, `horizontal overflow: ${c.horizontalOverflow ? 'YES (page wider than viewport)' : 'none'}`));
  lines.push(F(!r.reduced || r.reduced.error || r.reduced.reduced >= r.reduced.normal, `reduced-motion: ${r.reduced && !r.reduced.error ? `${r.reduced.reduced} content elements visible vs ${r.reduced.normal} with motion (content must not depend on an animation running)` : 'not measured'}`));
  lines.push(F(r.errors.length === 0, `console/page errors: ${r.errors.length}${r.errors.length ? '\n  ' + r.errors.join('\n  ') : ''}`));
  lines.push(Wn(c.smallTextCount === 0, `text under 13 px: ${c.smallTextCount}${c.smallText.length ? ' (' + c.smallText.join(', ') + ')' : ''}`));
  lines.push(Wn(c.willChangeCount <= 20, `will-change on ${c.willChangeCount} elements`));
  lines.push(Wn(c.imagesWithoutAlt === 0, `images without alt: ${c.imagesWithoutAlt}`));
  lines.push(Wn(c.fontsLoading === 0, `fonts still loading at shoot time: ${c.fontsLoading}`));
  if (r.hover) lines.push(r.hover.error ? `- WARN hover: ${r.hover.error}` : `- hover strip: 12 frames at ${r.hover.stampsMs.map(m => m + 'ms').join(' ')}`);
  if (r.click) lines.push(r.click.error ? `- WARN click: ${r.click.error}` : `- click strip: 12 frames at ${r.click.stampsMs.map(m => m + 'ms').join(' ')}`);
  lines.push('');
}
lines.push(`**${fails} FAIL · ${warns} WARN** — now Read every image and score with prompts/critique.md.`);
fs.writeFileSync(path.join(outDir, 'report.md'), lines.join('\n') + '\n');
fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify({ url, outDir, results }, null, 2));
fs.appendFileSync(path.join(path.dirname(outDir), 'LOG.md'), `- ${path.basename(outDir)} · ${url} · ${fails} FAIL · ${warns} WARN\n`);
console.log(lines.join('\n'));
