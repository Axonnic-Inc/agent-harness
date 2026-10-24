"""astra extensions shared by every copy of astra.py (laptop and any build VM): brief preflight, file ownership,
milestone commits, budgets from run history, USD for GPT tokens, compete stall rule, deliver gate.

Owner rule: never block the owner. Problems are warnings when a person launches astra; they stop the run only
when Claude Code launched it (CLAUDECODE=1 in the environment) and --force was not given.
"""
import fnmatch, hashlib, json, os, re, subprocess, sys, threading, time
from pathlib import Path

HOME = Path.home() / ".codex" / "harness"
PRICES = HOME / "prices.json"
# Required brief sections and the headings that count for each (substring match, case-insensitive). The final
# report format is not required here: astra appends it to every `do` prompt (UNATTENDED).
BRIEF_SECTIONS = {
    "Goal": ["goal", "objective", "what to do"],
    "Done when": ["done when", "done means", "definition of done", "acceptance"],
    "Owns": ["owns", "ownership", "files you own", "you own"],
    "Do not change": ["do not change", "do not touch", "out of scope", "owner decisions", "non-goals"],
}
BRIEF_TEMPLATE = "sections " + ", ".join(BRIEF_SECTIONS) + "; template: ops/brief_template.md"


def count_lines(p):
    if not p.exists():
        return 0
    with p.open('rb') as f:
        return sum(1 for _ in f)


def claude_launched():
    return os.environ.get("CLAUDECODE") == "1"


HEAD_RE = re.compile(r"^\s*(?:#{1,6}\s+(?P<h>.+?)\s*$|\*\*(?P<b>[^*]+?)\*\*\s*(?P<brest>.*)$|(?P<p>[A-Z][A-Za-z /-]{1,30}):\s*(?P<prest>.*)$)")


def section_of(name):
    name = name.lower()
    return next((k.lower() for k, aliases in BRIEF_SECTIONS.items() if any(a in name for a in aliases)), None)


def sections(text):
    """{section lower: body} for the BRIEF_SECTIONS, found as markdown headings (`## Done when`), bold lead-ins
    (`**Owns:** ...`) or plain `Owns: ...` lines; synonyms per BRIEF_SECTIONS. A `# Title` first line counts as
    the goal. Any other heading ends the current section."""
    out, cur = {}, None
    lines = (text or "").splitlines()
    first = next((l for l in lines if l.strip()), "")
    if re.match(r"^#\s+\S", first):
        out["goal"] = first[1:].strip()
    for line in lines:
        m = HEAD_RE.match(line)
        if m:
            name = (m.group("h") or m.group("b") or m.group("p")).strip().rstrip(":").strip()
            rest = (m.group("brest") or m.group("prest") or "").lstrip(":").strip()
            hit = section_of(name)
            if hit:
                cur = hit
                out[cur] = (out.get(cur, "") + "\n" + rest).strip() if cur == "goal" else rest
                continue
            if m.group("h"):
                cur = None
                continue
        if cur is not None:
            out[cur] += "\n" + line
    return out


def brief_problems(text):
    s = sections(text)
    missing = [x for x in BRIEF_SECTIONS if not s.get(x.lower(), "").strip()]
    return [f"brief has no '{x}' section" for x in missing]


def owns_explicit(text):
    """True when the brief has a dedicated `Owns` heading or label (not just prose about ownership)."""
    return any((m := HEAD_RE.match(l)) and (m.group("h") or m.group("b") or m.group("p")).strip().lower().startswith("owns")
               for l in (text or "").splitlines())


def owns(text):
    """Repo-relative file globs from the brief's Owns section: backticked items, or bullet / comma-separated items.
    Prose ownership sections are read up to their first blank line, without `(except ...)` asides. Absolute paths
    (clone locations) are skipped; a directory `dir/` becomes `dir/*`."""
    body = sections(text).get("owns", "")
    if not owns_explicit(text):
        body = re.split(r"\n\s*\n", body.strip())[0] if body.strip() else ""
    body = re.sub(r"\((?:except|but not|other than)[^)]*\)", "", body, flags=re.I)
    globs = re.findall(r"`([^`]+)`", body)
    if not globs:
        for line in body.splitlines():
            line = re.sub(r"^\s*[-*]\s*", "", line).strip()
            globs += [g.strip() for g in line.split(",") if g.strip() and " " not in g.strip()]
    out = []
    for g in globs:
        g = g.strip()
        if (not g or g.startswith(("/", "~", "$")) or ".." in g.split("/") or "=" in g or " " in g
                or not re.search(r"[/*.]", g)):
            continue
        g = g[2:] if g.startswith("./") else g
        out.append(g + "*" if g.endswith("/") else g)
    return out


def overlap(a, b):
    """Could glob a and glob b touch the same file? Conservative: a literal match either way, or one's fixed
    prefix contains the other's."""
    if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
        return True
    pa, pb = re.split(r"[*?\[]", a)[0], re.split(r"[*?\[]", b)[0]
    return bool(pa and pb) and (pa.startswith(pb) or pb.startswith(pa))


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def running_workers(runs, me=None):
    """[(run dir, ext dict)] for `do` runs still running (their ext.json pid is alive)."""
    out = []
    for f in runs.glob("*/ext.json"):
        if me and f.parent == me:
            continue
        try:
            e = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if e.get("kind") == "do" and alive(e.get("pid")):
            out.append((f.parent, e))
    return out


def gate(problems, force, say):
    """Warn about problems; stop only for Claude-launched runs without --force."""
    for p in problems:
        say(f"WARNING: {p}")
    if problems and claude_launched() and not force:
        sys.exit(f"astra: refusing a Claude-launched run with {len(problems)} problem(s) above "
                 f"(fix the brief per {BRIEF_TEMPLATE}, or pass --force)")


def git_out(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def preflight_do(task, cwd, run, runs, force, say):
    """Brief sections + ownership overlap with running workers. Writes run/ext.json. Returns ext dict.
    An overlap can stop a Claude-launched run only when both briefs declare an explicit `Owns` section;
    ownership read from prose is a warning."""
    problems = brief_problems(task)
    mine, explicit = owns(task), owns_explicit(task)
    root = git_out(cwd, "rev-parse", "--show-toplevel")
    for other, e in running_workers(runs, me=run):
        if e.get("root") != root:
            continue
        clash = sorted({f"{a} ~ {b}" for a in mine for b in e.get("owns", []) if overlap(a, b)})
        msg = f"Owns overlaps running worker {other.name}: {', '.join(clash[:6])}"
        if clash and explicit and e.get("explicit"):
            problems.append(msg)
        elif clash:
            say(f"WARNING: {msg} (ownership read from prose; not enforced)")
        elif not mine or not e.get("owns"):
            say(f"WARNING: another worker is running in the same repo ({other.name}) and ownership is not declared on both")
    gate(problems, force, say)
    ext = {"kind": "do", "pid": os.getpid(), "owns": mine, "explicit": explicit, "cwd": str(cwd), "root": root,
           "base": git_out(cwd, "rev-parse", "HEAD"), "started": time.time()}
    (run / "ext.json").write_text(json.dumps(ext))
    return ext


def changed_files(cwd, base):
    if not base:
        return []
    files = set(git_out(cwd, "diff", "--name-only", base).splitlines())
    files |= set(git_out(cwd, "ls-files", "--others", "--exclude-standard").splitlines())
    return sorted(f for f in files if f)


def after_do(ext, run, events_p90, say):
    """Report files changed outside Owns and how the run's size compares with past runs."""
    files = changed_files(ext["cwd"], ext.get("base"))
    if ext.get("owns"):
        rel = Path(ext["cwd"]).resolve()
        root = Path(ext["root"]) if ext.get("root") else rel
        prefix = str(rel.relative_to(root)) if rel != root and str(rel).startswith(str(root)) else ""
        out = [f for f in files if not any(fnmatch.fnmatch(os.path.join(prefix, f) if prefix else f, g) for g in ext["owns"])
               and f != "MILESTONE"]
        if out:
            say(f"WARNING: {len(out)} changed file(s) outside Owns: {', '.join(out[:12])}{' ...' if len(out) > 12 else ''}")
    ev = run / "events.jsonl"
    n = count_lines(ev)
    if events_p90 and n > 2 * events_p90:
        say(f"NOTE: this run used {n} events, {n / events_p90:.1f}x the p90 of past runs of this kind ({events_p90})")
    ext.update(pid=None, finished=time.time(), events=n, outside_owns=len(files))
    (run / "ext.json").write_text(json.dumps(ext))


def pathspec(glob):
    """Owns glob -> git pathspec: `dir/*` covers the whole directory; other globs match as globs."""
    if glob.endswith("/*") and not any(c in glob[:-2] for c in "*?["):
        return glob[:-1]
    return f":(glob){glob}" if any(c in glob for c in "*?[") else glob


class MilestoneWatcher(threading.Thread):
    """The sandboxed agent cannot write .git (Codex workspace-write), so it writes MILESTONE instead; astra,
    which runs outside the sandbox, commits as `milestone: <text>`. The commit holds exactly: tracked files that
    differ from HEAD, plus new files inside Owns (never inputs/). Anything else another session has staged stays
    staged and out of the commit; new files outside Owns are listed. MILESTONE is removed only after a successful
    commit, so a failure (index lock, hook) is retried on the next poll."""

    def __init__(self, cwd, say, owns=(), every=15):
        super().__init__(daemon=True)
        self.cwd, self.say, self.every, self.stop_ev = Path(cwd), say, every, threading.Event()
        self.root = git_out(cwd, "rev-parse", "--show-toplevel")
        self.owns = list(owns or [])
        self.lock = threading.Lock()

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True)

    def commit(self):
        with self.lock:
            try:
                self._commit()
            except Exception as e:  # a milestone problem must never take the astra run down
                self.say(f"milestone commit error: {e!r} (MILESTONE kept for the next try)")

    def _commit(self):
        f = Path(self.root) / "MILESTONE" if self.root else None
        if not f or not f.exists():
            return
        text = f.read_text(errors="replace").strip()
        if not text:
            return
        msg = text if text.lower().startswith("milestone:") else f"milestone: {text}"
        excl = [":!inputs"] if (Path(self.root) / "inputs").exists() else []
        # Files in HEAD that changed (a rename is its deletion here plus its new path below, via --no-renames).
        r = self.git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=MDT", "HEAD", "--", ".", *excl)
        if r.returncode != 0:
            self.say(f"milestone: git diff failed (rc {r.returncode}); MILESTONE kept: {r.stderr[-200:]}")
            return
        tracked = [x for x in r.stdout.split("\0") if x]
        new = []
        if self.owns:  # new files inside Owns: untracked, or already staged as added (e.g. by our failed try)
            specs = [pathspec(g) for g in self.owns]
            r = self.git("ls-files", "-z", "--others", "--exclude-standard", "--", *specs, *excl)
            a = self.git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=A", "--", *specs, *excl)
            new = sorted({x for x in r.stdout.split("\0") + a.stdout.split("\0") if x})
        files = sorted(set(tracked + new) - {"MILESTONE"})
        untracked = self.git("ls-files", "-z", "--others", "--exclude-standard").stdout.split("\0")
        staged_new = self.git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=A").stdout.split("\0")
        left = sorted(set(x for x in untracked + staged_new if x) - set(new) - {"MILESTONE"})
        if left:  # includes the new side of a rename out of Owns, whose deletion side is committed
            self.say(f"milestone: {len(left)} new or staged-new file(s) outside Owns not committed: {', '.join(left[:8])}"
                     f"{' ...' if len(left) > 8 else ''}")
        if not files:
            f.unlink(missing_ok=True)
            self.say(f"MILESTONE '{text[:60]}': nothing new to commit")
            return
        if new:
            r = self.git("add", "--", *new)
            if r.returncode != 0:
                self.say(f"milestone: git add failed (rc {r.returncode}); MILESTONE kept: {r.stderr[-200:]}")
                return
        r = self.git("commit", "-q", "-m", msg, "--only", "--", *files)
        if r.returncode != 0:
            self.say(f"milestone commit failed (rc {r.returncode}); MILESTONE kept: {(r.stderr or r.stdout)[-200:]}")
            return
        f.unlink(missing_ok=True)
        self.say(f"milestone committed by astra: {git_out(self.root, 'rev-parse', '--short', 'HEAD')} {msg[:80]} "
                 f"({len(files)} files)")

    def run(self):
        while not self.stop_ev.wait(self.every):
            self.commit()

    def stop(self):
        self.stop_ev.set()
        if self.is_alive():
            self.join(timeout=120)
        self.commit()  # a MILESTONE written in the last seconds still lands


# ---------------------------------------------------------------- plan pass (astra do --plan)

PLAN_PROMPT = """Do not edit anything yet. Read what you need, then write the plan you will follow for the brief below,
in at most 16 lines: the steps, every file you will change (all inside Owns), and how you will prove each
"Done when" item (command + expected number). Reply with the plan only.

BRIEF:
{task}"""

CRITIC_PROMPT = """A builder wrote this plan for the brief below. Check it against the brief only:
- every file it will change is inside the brief's Owns, and nothing in Do not change / out of scope is touched;
- every "Done when" item has a step that proves it with a command or a number;
- it does not add scripts, docs or features the brief does not need.
Set pass=false only for a concrete violation of these; list each one in problems. Do not solve the task.

BRIEF:
{task}

PLAN:
{plan}"""

CRITIC_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pass", "problems"],
                 "properties": {"pass": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}}}}


def critic_model(builder):
    """A critic from the other model family (Claude roles use Opus)."""
    return "gpt-6.1-sol" if builder.startswith("claude:") else "claude:opus"  # owner 2026-10-24: Opus for every Claude role


def plan_gate(run_agent, model, effort, task, cwd, run, force, say):
    """Plan first (read-only), a cross-family critic checks it against the brief, then the build follows the plan.
    A failed critique is a warning for the owner and stops Claude-launched runs (like the brief check).
    Returns the text to append to the build prompt."""
    p = run_agent(model, effort, PLAN_PROMPT.format(task=task), cwd, run / "plan", "read-only", 30)
    plan = (p.get("final") or "").strip()[:4000]
    if not plan:
        say("WARNING: plan pass produced no plan; building without one")
        return ""
    (run / "plan.md").write_text(plan + "\n")
    cm = critic_model(model)
    c = run_agent(cm, "high", CRITIC_PROMPT.format(task=task, plan=plan), cwd, run / "critic", "read-only", 15, CRITIC_SCHEMA)
    try:
        verdict = json.loads(re.search(r"\{.*\}", c.get("final") or "", re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        verdict = {"pass": True, "problems": ["critic output unreadable; not enforced"]}
    say(f"plan critic ({cm}): {'PASS' if verdict.get('pass') else 'FAIL'}"
        + (f": {'; '.join(verdict.get('problems', []))[:600]}" if verdict.get("problems") else ""))
    if not verdict.get("pass"):
        gate([f"plan critic: {x}" for x in verdict.get("problems", [])] or ["plan critic failed"], force, say)
    return f"\n\nYour plan (checked against the brief; follow it, and note any change of plan in your final report):\n{plan}\n"


# ---------------------------------------------------------------- budgets from history

def _clock(s):
    h, m, x = (int(v) for v in s.split(":"))
    return h * 3600 + m * 60 + x


def history(runs, kind, model=None, last=60):
    """[(minutes, events)] for finished runs of this kind (and model), newest last."""
    out = []
    for d in sorted(runs.glob(f"*-{kind}-*"))[-400:]:
        lt = d / "log.txt"
        if not lt.exists():
            continue
        lines = lt.read_text(errors="replace").splitlines()
        first = next((l for l in lines if " -> " in l), None)
        end = next((l for l in lines if re.match(r"^\d\d:\d\d:\d\d exit ", l)), None)
        if not first or not end or (model and not first[9:].startswith(model + " ")):
            continue
        secs = (_clock(end[:8]) - _clock(first[:8])) % 86400
        ev = d / "events.jsonl"
        out.append((secs / 60, count_lines(ev)))
    return out[-last:]


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else None


def history_budget(runs, kind, model, floor=120, cap=480, min_samples=5):
    """(timeout minutes, events p90): timeout = p90 duration x 1.5 within [floor, cap]; (None, None) without enough
    history or on any error. The floor is the old fixed default, so history only ever extends a timeout: most runs
    are short, so a pure p90 would kill the rare multi-hour build (review of PR #2: p90 x 1.5 = 75 min against
    past do runs of 88 and 114 min)."""
    try:
        h = history(runs, kind, model) or []
        if len(h) < min_samples:
            h = history(runs, kind)
        if len(h) < min_samples:
            return None, None
        return max(floor, min(cap, round(p90([m for m, _ in h]) * 1.5))), p90([e for _, e in h])
    except Exception:  # a budget must never stop a run
        return None, None


# ---------------------------------------------------------------- compete: stop refining a solver stuck on one failure

def note_check(s):
    c = s.get("check") or {}
    if not c or c.get("rc") == 0:
        s["check_fail_hashes"] = []
        return
    h = hashlib.sha1(f"{c.get('rc')}|{c.get('tail', '')[-2000:]}".encode()).hexdigest()[:12]
    s.setdefault("check_fail_hashes", []).append(h)


def stalled(s):
    """Same check failure (rc + output tail) twice in a row: refining again rarely helps."""
    hs = s.get("check_fail_hashes", [])
    return len(hs) >= 2 and hs[-1] == hs[-2]


# ---------------------------------------------------------------- USD for non-Claude tokens

def prices():
    try:
        return json.loads(PRICES.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def usd(model, t, table=None):
    """USD from a prices.json entry {"in": $/Mtok uncached input, "cached": $/Mtok, "out": $/Mtok incl. reasoning}.
    None when the model has no entry."""
    p = (table if table is not None else prices()).get(model)
    if not p:
        return None
    unc = max(0, t["in"] - t["cached"])
    return (unc * p.get("in", 0) + t["cached"] * p.get("cached", p.get("in", 0)) + (t["out"] + t.get("reasoning", 0)) * p.get("out", 0)) / 1e6


# ---------------------------------------------------------------- deliver

def deliver(cwd, push, force, say):
    """Run the repo's deliver gate (tools/review_daemon/deliver_check.py); push HEAD to origin/main only when it
    passes, or when the owner forces it (never for Claude-launched runs)."""
    root = git_out(cwd, "rev-parse", "--show-toplevel")
    gate_py = Path(root) / "tools" / "review_daemon" / "deliver_check.py" if root else None
    if not gate_py or not gate_py.exists():
        sys.exit("astra deliver: no tools/review_daemon/deliver_check.py in this repo")
    sha = git_out(root, "rev-parse", "HEAD")  # pin the commit: a milestone landing during the gate must not ride along
    rc = subprocess.run([sys.executable, str(gate_py), "--repo", root, "--ref", sha]).returncode
    if not push:
        return rc
    if rc != 0 and not (force and not claude_launched()):
        sys.exit("astra deliver: gate failed; not pushing" + ("" if claude_launched() else " (owner: --force pushes anyway)"))
    if rc != 0:
        say("WARNING: pushing with a failed gate (--force)")
    say(f"pushing {sha[:7]} (the commit the gate checked) to origin/main")
    r = subprocess.run(["git", "-C", root, "push", "origin", f"{sha}:refs/heads/main"])
    return r.returncode
