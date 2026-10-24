#!/usr/bin/env python3
"""astra: run Codex (and optionally Claude) on one task without side quests.

  astra do "<task>"        one focused run that may edit files (effort high); --plan: plan + critic first
  astra review "<task>"    one read-only run that reports findings (effort high)
  astra compete "<problem>|@file" [-n 6] [--models a,b,claude:opus] [--check CMD]
                           [--budget MIN] [--bg]
      N isolated solvers (models x starting angles, effort ultra)
      -> machine check (--check) -> K independent verifiers per answer (other
      models, accepted only if unanimous) -> shared insights -> refine the rest
      -> repeat --rounds times -> judge ranks -> REPORT.md
      --board: solvers, verifiers and the reviewer share a message board (MCP); only
      verifiers/reviewer can mark posts verified or refuted. `astra board [run]` shows it.
  astra status [run]       progress of a run (default: latest)
  astra apply [run] [sN]   apply a solver's changes to the original repo (default: winner)
  astra clean [run]        delete a run's workspaces, keep reports, solutions and diffs
  astra deliver [--push]   deliver gate at HEAD (VM checks, inbox, milestone review); --push -> origin/main

Runs live in ~/.codex/harness/runs/<stamp>-<kind>-<slug>/. Nothing touches your
repo until `astra apply`.
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import astra_ext as ext  # brief preflight, ownership, milestone commits, budgets, USD, deliver

HOME = Path.home() / ".codex" / "harness"
RUNS = HOME / "runs"
CODEX_MODELS = ["gpt-6-astra", "gpt-6.1-sol"]
# Different starting angles so parallel solvers don't all walk the same path.
ANGLES = [
    ("first-principles", "Define the problem precisely, then build the simplest thing that could work and test it."),
    ("known-methods", "Start from what is already known (repo, docs, literature), reuse the strongest existing method, then improve it."),
    ("empirical", "Write small experiments first, measure, and let the numbers pick the method."),
    ("adversarial", "Look for why the obvious approach fails, find the hard cases first, then design around them."),
    ("reformulate", "Change the representation or objective so the problem becomes easier, then solve that."),
    ("minimal", "Find the smallest change with the largest effect and prove it with a measurement."),
]
CODEX_QUIET = ["--disable", "memories", "--disable", "chronicle",
               "-c", "mcp_servers.google_compute_engine.enabled=false",
               "-c", "mcp_servers.agent-board.enabled=false"]  # global board stays out of astra runs
LOCK = threading.RLock()


def log(run, msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOCK, open(run / "log.txt", "a") as f:
        f.write(line + "\n")


def save_state(run, state):
    with LOCK:
        tmp = run / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(run / "state.json")


def upd(run, state, s, **kw):
    with LOCK:
        s.update(kw)
        save_state(run, state)


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=check).stdout.strip()


def repo_root(d):
    try:
        return Path(git(d, "rev-parse", "--show-toplevel"))
    except subprocess.CalledProcessError:
        return None


def new_run(kind, text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:40].strip("-") or kind
    base = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{slug}"
    run, k = base, 2
    while True:  # parallel runs started in the same second get -2, -3, ...
        try:
            run.mkdir(parents=True)
            return run
        except FileExistsError:
            run, k = Path(f"{base}-{k}"), k + 1


def read_task(text):
    return Path(text[1:]).expanduser().read_text() if text.startswith("@") else text


def pmap(fn, items):
    """Run fn over items in parallel threads; an exception becomes {'error': ...}."""
    out = [None] * len(items)

    def work(i, x):
        try:
            out[i] = fn(x)
        except Exception as e:  # one failed agent must not kill the run
            out[i] = {"error": repr(e)}
    ts = [threading.Thread(target=work, args=(i, x)) for i, x in enumerate(items)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


# ---------------------------------------------------------------- agents

def claude_flags(sandbox, protect, board=False):
    """Claude Code has no OS sandbox by default: turn on Seatbelt (Bash writes only inside cwd),
    forbid unsandboxed retries, answer no prompts, and deny edits to the original repo."""
    settings = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                            "allowUnsandboxedCommands": False, "failIfUnavailable": True}}
    if protect:
        settings["permissions"] = {"deny": [f"Edit(/{p}/**)" for p in protect]}
    if sandbox == "read-only":
        mode = ["--permission-mode", "dontAsk", "--tools", "Read,Grep,Glob,Bash"]
    else:
        mode = ["--permission-mode", "acceptEdits"]
    return mode + ["--permission-prompts", "none",
                   "--allowedTools", "Read,Grep,Glob" + (",mcp__board__*" if board else ""),
                   "--settings", json.dumps(settings)]


PROTECT = []  # original repo paths that agents must never edit (set by compete)
BOARD = {}    # set by --board: {"db": path}; agents get the shared board as an MCP server
BOARD_PY = Path(__file__).resolve().with_name("board.py")


GLOBAL_BOARD = Path.home() / ".agent-board" / "board.sqlite"


def post_global(agent, body, refs):
    """Leave a note on the cross-session agent-board (if set up) so interactive Claude/Codex chats
    can see what an astra run produced. Best effort: never fails the run."""
    if not GLOBAL_BOARD.exists():
        return
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "post_message", "arguments": {
        "body": body[:1000], "kind": "artifact", "channel": "astra", "refs": [str(x) for x in refs]}}}
    try:
        subprocess.run([sys.executable, str(BOARD_PY)], input=json.dumps(call) + "\n", capture_output=True,
                       text=True, timeout=30, env={**os.environ, "BOARD_DB": str(GLOBAL_BOARD),
                                                   "BOARD_AGENT": agent, "BOARD_ROLE": "agent"})
    except (OSError, subprocess.SubprocessError):
        pass


def board_env(who):
    name, role = who
    return {"BOARD_DB": BOARD["db"], "BOARD_AGENT": name, "BOARD_ROLE": role,
            "BOARD_ALERT": os.environ.get("HARNESS_ALERT", "harness-alert") + f" 'astra board: {name} escalated'"}
NETWORK = []  # set by --network: let sandboxed Codex solvers reach the internet


FALLBACK = {"gpt-6.1-sol": "gpt-6-astra", "gpt-6-astra": "gpt-6.1-sol", "gpt-6-sol": "gpt-6-astra"}


def run_agent(model, effort, prompt, cwd, out_dir, sandbox, timeout_min, schema=None, resume=None, who=None):
    """Run one agent. If the model is at capacity (503), retry with back-off, then fall back to
    another model (new sessions only). Returns dict(session, final, rc, timed_out, usage, model)."""
    start = time.time()
    for attempt in range(4):
        left = timeout_min - (time.time() - start) / 60
        r = _run_agent_once(model, effort, prompt, cwd, out_dir, sandbox, left, schema, resume, who)
        busy = r.pop("capacity", False)
        if not busy or left < 3:
            r["model"] = model
            return r
        with open(out_dir / "retries.txt", "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {model} at capacity (attempt {attempt + 1})\n")
        time.sleep(30 * (attempt + 1))
        if attempt >= 1 and not resume and model in FALLBACK:
            model = FALLBACK[model]
    r["model"] = model
    return r


def _run_agent_once(model, effort, prompt, cwd, out_dir, sandbox, timeout_min, schema=None, resume=None,
                    who=None):
    """Run one agent to completion. Returns dict(session, final, rc, timed_out, usage, capacity)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    final_file = out_dir / "final.md"
    session, env = resume, None
    board = bool(BOARD and who)
    if model.startswith("claude:"):
        if sandbox == "read-only":
            cwd = out_dir  # sandboxed Bash can only write in cwd; keep it away from the candidate
        # stream-json writes events as they happen (plain json writes nothing until the end, so a
        # timed-out run left no session and no text); a preset session id lets a killed solver
        # still be resumed for refinement. No user MCP servers, hooks or advisor, like CODEX_QUIET.
        # Claude at max effort ran out the clock on 4/4 solves, so ultra/max map to high.
        cmd = ["claude", "-p", prompt, "--model", model.split(":", 1)[1],
               "--effort", "high" if effort in ("ultra", "max") else effort,
               "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
               "--setting-sources", "project,local", *claude_flags(sandbox, PROTECT, board)]
        if board:
            cmd += ["--mcp-config", json.dumps({"mcpServers": {"board": {
                "command": sys.executable, "args": [str(BOARD_PY)], "env": board_env(who)}}})]
        if schema:
            cmd += ["--json-schema", json.dumps(schema)]
        if resume:
            cmd += ["--resume", resume]
        else:
            session = str(uuid.uuid4())
            cmd += ["--session-id", session]
        tmp = Path(cwd) / ".astra-tmp"  # keep scratch files inside the sandboxed workspace
        tmp.mkdir(exist_ok=True)
        env = {**os.environ, "TMPDIR": str(tmp)}
    else:
        cmd = ["codex", "exec"] + (["resume", resume] if resume else [])
        cmd += ["--json", "-m", model, "-c", f'model_reasoning_effort="{effort}"',
                "--skip-git-repo-check", "-o", str(final_file), *CODEX_QUIET]
        if not resume:
            cmd += ["-s", sandbox, "-C", str(cwd)]
        if board:  # MCP servers run outside the sandbox; the board db lives in the run dir
            env_toml = ",".join(f'{k}={json.dumps(v)}' for k, v in board_env(who).items())
            cmd += ["-c", f"mcp_servers.board.command={json.dumps(sys.executable)}",
                    "-c", f"mcp_servers.board.args=[{json.dumps(str(BOARD_PY))}]",
                    "-c", f"mcp_servers.board.env={{{env_toml}}}",
                    "-c", 'mcp_servers.board.default_tools_approval_mode="approve"']
        if NETWORK and sandbox == "workspace-write":
            cmd += ["-c", "sandbox_workspace_write.network_access=true"]
        if schema:
            sf = out_dir / "schema.json"
            sf.write_text(json.dumps(schema))
            cmd += ["--output-schema", str(sf)]
        cmd.append(prompt)
    (out_dir / "cmd.json").write_text(json.dumps(cmd))
    with open(out_dir / "events.jsonl", "w") as ev, open(out_dir / "stderr.txt", "w") as er:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=ev, stderr=er, env=env)
        timed_out = False
        try:
            rc = p.wait(timeout=max(1.0, timeout_min) * 60)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                rc = p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
                rc = p.wait()
            timed_out = True
    if env and RUNS.resolve() not in Path(cwd).resolve().parents:
        # scratch dir sits in the user's repo (astra do -C <repo>): never leave it behind. Inside a
        # run's workspace keep it: solvers cite scratch tests as evidence and verifiers re-run them.
        shutil.rmtree(env["TMPDIR"], ignore_errors=True)
    final, capacity, last_text = "", False, ""
    usage = {"in": 0, "out": 0, "usd": 0.0}
    for line in (out_dir / "events.jsonl").read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "thread.started":
            session = e.get("thread_id")
        elif t == "turn.completed":
            u = e.get("usage", {})
            usage["in"] += u.get("input_tokens", 0)
            usage["out"] += u.get("output_tokens", 0) + u.get("reasoning_output_tokens", 0)
        elif t == "turn.failed":
            msg = str((e.get("error") or {}).get("message", "")).lower()
            capacity = capacity or "capacity" in msg or "503" in msg or "unavailable" in msg
        elif t == "item.completed" and e.get("item", {}).get("type") == "agent_message":
            final = e["item"].get("text", final)
        elif t == "assistant":  # claude stream-json: keep the latest text in case it is killed
            for b in (e.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    last_text = b["text"]
        elif t == "result":  # claude -p --output-format stream-json
            session = e.get("session_id") or session
            u = e.get("usage", {})
            usage["in"] += u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            usage["out"] += u.get("output_tokens", 0)
            usage["usd"] += e.get("total_cost_usd") or 0.0
            final = json.dumps(e["structured_output"]) if e.get("structured_output") else e.get("result", "")
    if final_file.exists() and final_file.read_text().strip():
        final = final_file.read_text()
    final = final or last_text
    final_file.write_text(final)
    return {"session": session, "final": final, "rc": rc, "timed_out": timed_out, "usage": usage, "capacity": capacity}


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def clock(m):
    """Neither CLI sees the run's deadline; Claude solvers kept measuring until they were killed."""
    return (f"\n\nTime: you have about {m:.0f} minutes, then this run is killed and only what is on "
            "disk counts. Write a first complete deliverable within the first quarter of that time "
            "and keep it current; finish early rather than late.")


BOARD_SOLVER = """
SHARED BOARD: every solver and verifier in this run shares a message board (MCP tools
post_message, search_posts, read_post, list_threads). You post as {me}. Use it sparingly:
- At the start and after each milestone, check list_threads / search_posts (include_unverified=true).
- Post what would save another solver time: a finding with evidence (kind=evidence: command +
  output, max 1000 chars), a refutation of someone's claim, a reusable file (kind=artifact, absolute
  path in refs), or kind=escalate only if the task or the check itself looks broken or gameable.
- Other solvers' posts are UNVERIFIED hypotheses until a verifier marks them: re-check before relying.
- Your SOLUTION.md must stand on your own evidence; do not copy another solver's answer.
"""

BOARD_VERIFIER = """
SHARED BOARD: you are {me}. After your review, use search_posts (author={sid},
include_unverified=true) and set_status on the posts by {sid} that your checks settled
(verified or refuted, with the evidence). Post a kind=refutation for any fatal error you found and
your verdict as kind=evidence. Never use kind=escalate for reviews (it pages the human).
"""

BOARD_REVIEWER = """
The shared board of this run (MCP tools list_threads, search_posts, read_post, set_status) also
holds solver and verifier posts: read it (include_unverified=true), set_status on any post the
evidence above settles, and include its VERIFIED/REFUTED posts in the brief.
"""

UNATTENDED = """Nobody is watching this run, so do not ask questions: make the sensible call and note it.
Finish with: what changed, how it was verified (command + numbers), what is not verified."""


def cmd_single(a, sandbox):
    if getattr(a, "network", False):
        NETWORK.append(True)
    task = read_task(a.task)
    cwd = Path(a.dir).resolve()
    run = new_run("review" if sandbox == "read-only" else "do", task)
    prompt = f"{task}\n\n{UNATTENDED}\n" + (
        "\nThis is a review: do not edit files. Report concrete problems with file:line, most "
        "severe first. Skip style." if sandbox == "read-only" else "")
    kind, say = ("review" if sandbox == "read-only" else "do"), (lambda m: log(run, m))
    hist_t, ev_p90 = ext.history_budget(RUNS, kind, a.model)
    timeout = a.timeout or hist_t or 120
    log(run, f"{a.model} @ {a.effort} in {cwd} ({sandbox}) -> {run}")
    info = ext.preflight_do(task, cwd, run, RUNS, a.force, say) if kind == "do" else None
    if kind == "do" and getattr(a, "plan", False):
        prompt += ext.plan_gate(run_agent, a.model, a.effort, task, cwd, run, a.force, say)
    if not a.timeout:
        say(f"timeout {timeout} min ({'p90 x 1.5 of past runs' if hist_t else 'default'}; -t overrides)")
    watcher = ext.MilestoneWatcher(cwd, say, (info or {}).get("owns", [])) if kind == "do" else None
    if watcher:
        watcher.start()
    try:
        r = run_agent(a.model, a.effort, prompt, cwd, run, sandbox, timeout)
    finally:
        if watcher:
            watcher.stop()
        if info:
            ext.after_do(info, run, ev_p90, say)
    log(run, f"exit {r['rc']}{' TIMED OUT' if r['timed_out'] else ''}; session {r['session']}; "
             f"tokens in {r['usage']['in']:,} out {r['usage']['out']:,}")
    ended = "TIMED OUT" if r["timed_out"] else f"exit {r['rc']}"
    post_global(f"astra-{r.get('model') or a.model}",
                f"astra {kind} in {cwd} {ended}. "
                f"Task: {task[:250]}\nResult: {r['final'][:500]}", [run / "final.md"])
    print("\n" + r["final"])


# ---------------------------------------------------------------- compete

SOLVER = """You are one of {n} independent solvers working on the same problem in parallel.
Other models will then try hard to break your answer, and a judge ranks all answers on
correctness and evidence, not length. Overclaiming loses: an honest partial result with clean
evidence beats a confident wrong one.

Your workspace is {wd} (an isolated copy{base_note}). Edit only inside it, and do not look at
{runs} or at other solvers' work. {unattended}

Starting angle (drop it if the evidence says so): {angle}

PROBLEM:
{problem}

Deliverable: create the new file {wd}/SOLUTION.md (inside your workspace) with exactly these sections:
## Answer        the result in a few lines (or "no solution found" + the best partial result)
## Method        what you did and why, short
## Evidence      commands you ran with their outputs/numbers; proofs written out in full
## Weaknesses    what could be wrong, what you did not check
{check_note}"""

VERIFIER = """You are a strict, independent verifier. Another model produced the work in {wd}:
read {wd}/SOLUTION.md and its changes (`git -C {wd} diff {base}`, if it is a git repo).
You have no reference answer.

PROBLEM it was meant to solve:
{problem}
{check_block}
Find why it is wrong or weaker than it claims. Re-derive key steps, recompute key numbers,
re-run cheap evidence commands (read-only), look for leakage, bugs, unjustified leaps, missed
cases and claims without evidence. Do not fix anything. Be specific and terse.
Score 0-10 for how likely the Answer is correct and complete as stated. A fatal error is one
that makes the Answer wrong or unsupported."""

VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["fatal_errors", "other_issues", "verified", "score", "summary"],
    "properties": {
        "fatal_errors": {"type": "array", "items": {"type": "string"}},
        "other_issues": {"type": "array", "items": {"type": "string"}},
        "verified": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "number"},
        "summary": {"type": "string"},
    },
}

INSIGHTS = """{n} solvers attacked this problem independently and verifiers reviewed each answer.

PROBLEM:
{problem}

{digest}

Write a short brief (max 40 lines) that every solver will read before revising:
- VERIFIED facts: only what a passing check showed or what a verifier listed under `verified`
  (say which candidate and which verifier),
- REFUTED: claims a verifier showed wrong, with the reason (as prominent as the wins),
- [UNVERIFIED] ideas from one candidate that others could try, labelled as such,
- approaches that failed and why.
Never upgrade a solver's own claim to a fact. Do not pick a winner and do not solve the problem yourself."""

REFINE = """Independent verifiers (other models) reviewed your SOLUTION.md:

{reports}
{check_block}{insights}
Fix every fatal error you agree with; where you disagree, say why with evidence. If your
approach is broken, say so plainly or switch approach. Update SOLUTION.md in place (same
sections). Stay on the original problem."""

JUDGE = """You are the final judge for {n} competing answers to this problem:

{problem}

Each candidate directory under {run} (s1, s2, ...) holds SOLUTION.md and the solver's changes.
Per candidate below: the solution, the check result and the independent verifier reports.
{digest}

Rank by correctness first. Among answers equally likely to be correct, prefer the simpler
answer with the smaller change; extra evidence counts only where it raises confidence in
correctness, and extra scripts or prose count against an answer. A candidate with an unrefuted
fatal error ranks below every candidate without one. If parts of two candidates combine into something clearly better, say exactly how in
`combine`; otherwise leave it empty."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["ranking", "winner", "why", "combine"],
    "properties": {
        "ranking": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "score", "reason"],
            "properties": {"id": {"type": "string"}, "score": {"type": "number"},
                           "reason": {"type": "string"}}}},
        "winner": {"type": "string"},
        "why": {"type": "string"},
        "combine": {"type": "string"},
    },
}

EXCLUDE = [":(exclude)SOLUTION.md", ":(glob,exclude)**/__pycache__/**", ":(glob,exclude)**/*.pyc",
           ":(glob,exclude).astra-tmp/**"]


def setup_workspaces(run, base_dir, n):
    root = repo_root(base_dir)
    ws = []
    if not root:
        for i in range(1, n + 1):
            (run / f"s{i}").mkdir()
            ws.append(run / f"s{i}")
        return None, [None] * n, ws
    # Independent clones (own .git, no remote) + the current uncommitted state. The original
    # repo is only read.
    head = git(root, "rev-parse", "HEAD")
    dirty = subprocess.run(["git", "-C", str(root), "diff", "--binary", "HEAD"],
                           capture_output=True, check=True).stdout
    untracked = [f for f in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f]
    for i in range(1, n + 1):
        wd = run / f"s{i}"
        subprocess.run(["git", "clone", "--quiet", "--local", str(root), str(wd)], check=True)
        git(wd, "checkout", "--quiet", "--detach", head)
        git(wd, "remote", "remove", "origin")
        if dirty:
            subprocess.run(["git", "-C", str(wd), "apply", "--binary", "-"], input=dirty, check=True)
        for f in untracked:
            src = root / f
            if src.is_file() and src.stat().st_size < 200e6:
                (wd / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, wd / f)
        git(wd, "add", "-A")
        git(wd, "-c", "user.name=astra", "-c", "user.email=astra@local",
            "commit", "-qm", "astra base", "--allow-empty", "--no-verify")
        ws.append(wd)
    return root, [git(wd, "rev-parse", "HEAD") for wd in ws], ws


def run_check(cmd, wd, out, timeout_min):
    if not cmd:
        return None
    try:
        p = subprocess.run(cmd, shell=True, cwd=wd, capture_output=True, text=True,
                           timeout=max(1.0, timeout_min) * 60)
        res = {"rc": p.returncode, "tail": (p.stdout + p.stderr)[-3000:]}
    except subprocess.TimeoutExpired:
        res = {"rc": "timeout", "tail": ""}
    (out / "check.json").write_text(json.dumps(res, indent=2))
    return res


def check_block(cmd, res):
    if not cmd or not res:
        return ""
    return f"\nCheck `{cmd}` exited {res['rc']}; last output:\n```\n{res['tail']}\n```\n"


def solver_diff(s):
    wd = Path(s["dir"])
    git(wd, "add", "-A")
    return subprocess.run(["git", "-C", str(wd), "diff", "--cached", "--binary", s["base"], "--", ".", *EXCLUDE],
                          capture_output=True, check=True).stdout


def save_changes(s):
    """Copy SOLUTION.md and the diff next to the agent logs so they survive `astra clean`."""
    wd, keep = Path(s["dir"]), Path(s["meta"])
    try:
        if (wd / "SOLUTION.md").exists():
            shutil.copy2(wd / "SOLUTION.md", keep / "SOLUTION.md")
        if s.get("base") and wd.exists():
            (keep / "changes.diff").write_bytes(solver_diff(s))
    except (OSError, subprocess.CalledProcessError) as e:
        (keep / "changes.diff.error").write_text(repr(e))


def solution_text(s, limit=12000):
    f = Path(s["dir"]) / "SOLUTION.md"
    if not f.exists():
        return "(no SOLUTION.md)"
    t = f.read_text()
    return t if len(t) <= limit else t[:limit] + f"\n...[truncated, full file {f}]"


def cmd_compete(a):
    if a.bg:
        RUNS.mkdir(parents=True, exist_ok=True)
        logf = RUNS / f"bg-{time.strftime('%Y%m%d-%H%M%S')}.log"
        argv = [x for x in sys.argv[1:] if x != "--bg"]
        subprocess.Popen([sys.executable, os.path.realpath(__file__), *argv], cwd=os.getcwd(),
                         stdin=subprocess.DEVNULL, stdout=open(logf, "w"), stderr=subprocess.STDOUT,
                         start_new_session=True)
        print(f"started in the background (log {logf}). Progress: astra status")
        return

    problem = read_task(a.problem)
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    pool = list(dict.fromkeys(models + CODEX_MODELS))
    run = new_run("compete", problem)
    root, bases, ws = setup_workspaces(run, Path(a.dir).resolve(), a.n)
    if root:
        PROTECT.append(str(root))
    if a.network:
        NETWORK.append(True)
    (run / "PROBLEM.md").write_text(problem)
    if a.board:
        (run / "board").mkdir()
        BOARD["db"] = str(run / "board" / "board.sqlite")
    t0 = time.time()
    deadline = t0 + a.budget * 60 if a.budget else None
    reserve = min(15.0, a.budget * 0.15) if a.budget else 0.0  # minutes kept back for the judge

    def minutes(share=1.0):
        if deadline is None:
            return a.timeout
        return max(1.0, min(a.timeout, ((deadline - time.time()) / 60 - reserve) * share))

    def time_left():
        return deadline is None or (deadline - time.time()) / 60 - reserve > 5

    solvers = []
    for i, wd in enumerate(ws):
        tag, angle = ANGLES[i % len(ANGLES)]
        solvers.append({"id": f"s{i+1}", "dir": str(wd), "base": bases[i], "meta": str(run / "agents" / f"s{i+1}"),
                        "model": models[i % len(models)], "angle": tag, "angle_text": angle,
                        "stage": "pending", "verify": [], "accepted": False,
                        "usage": {"in": 0, "out": 0, "usd": 0.0}})
        Path(solvers[-1]["meta"]).mkdir(parents=True, exist_ok=True)
    state = {"run": str(run), "repo": str(root) if root else None, "problem": problem[:500],
             "effort": a.effort, "check": a.check, "rounds": a.rounds, "verifiers": a.verifiers,
             "budget": a.budget, "solvers": solvers, "started": t0, "stage": "solve", "notes": []}
    save_state(run, state)
    calls = a.n + a.n * a.verifiers * (a.rounds + 1) + a.n * a.rounds + (a.rounds if a.share else 0) + 1
    log(run, f"compete: {a.n} solvers ({', '.join(s['model'] for s in solvers)}) @ {a.effort}; "
             f"{a.verifiers} verifiers each; up to {a.rounds} refine round(s); <= {calls} agent calls; "
             f"budget {f'{a.budget:.0f} min' if a.budget else 'none'} -> {run}")

    def add_usage(s, r):
        with LOCK:
            for k in s["usage"]:
                s["usage"][k] += r["usage"][k]

    # 1. solve
    def solve(s):
        upd(run, state, s, stage="solving")
        prompt = SOLVER.format(
            n=a.n, wd=s["dir"], runs=run, unattended=UNATTENDED, angle=s["angle_text"], problem=problem,
            base_note=f" of {root}; the original is read-only reference" if root else "",
            check_note=f"\nYour work will be scored with `{a.check}`, run in {s['dir']}. Run it yourself." if a.check else "")
        if a.board:
            prompt = prompt.replace(f"do not look at\n{run} or at other solvers' work.",
                                    f"do not look at\n{run} except files other agents point to on the shared board.")
            prompt += BOARD_SOLVER.format(me=s["id"])
        m = minutes(0.4 if a.rounds and a.budget else 0.6)  # at 0.6 the budget ran out before sharing in 3/4 runs
        r = run_agent(s["model"], a.effort, prompt + clock(m), s["dir"], Path(s["meta"]) / "solve",
                      "workspace-write", m, who=(s["id"], "solver"))
        add_usage(s, r)
        if r.get("model") and r["model"] != s["model"]:
            log(run, f"{s['id']}: {s['model']} at capacity, fell back to {r['model']}")
        upd(run, state, s, session=r["session"], timed_out=r["timed_out"], stage="solved",
            model=r.get("model") or s["model"])
        log(run, f"{s['id']} ({s['model']}, {s['angle']}) solved in {(time.time() - t0) / 60:.0f} min"
                 f"{' TIMED OUT' if r['timed_out'] else ''}")
    pmap(solve, solvers)

    # 2. check -> verify -> (share) -> refine, repeated
    for rnd in range(a.rounds + 1):
        live = [s for s in solvers if not s["accepted"]]
        if not live:
            break
        state["stage"] = f"verify{rnd}"
        save_state(run, state)
        for s in live:
            upd(run, state, s, check=run_check(a.check, s["dir"], Path(s["meta"]), minutes()),
                stage=f"verify{rnd}")
        jobs = []
        for idx, s in enumerate(live):
            others = [m for m in pool if m != s["model"]] or [s["model"]]
            for j in range(a.verifiers):
                jobs.append((s, j, others[(idx + j) % len(others)]))

        def verify(job):
            s, j, vm = job
            vp = VERIFIER.format(wd=s["dir"], base=s["base"] or "HEAD", problem=problem,
                                 check_block=check_block(a.check, s.get("check")))
            m = minutes()
            me = f"v{rnd}-{s['id']}-{j}"
            if a.board:
                vp += BOARD_VERIFIER.format(me=me, sid=s["id"])
            r = run_agent(vm, a.verify_effort, vp + clock(m), s["dir"], Path(s["meta"]) / f"verify{rnd}-{j}",
                          "read-only", m, schema=VERIFY_SCHEMA, who=(me, "verifier"))
            add_usage(s, r)
            v = parse_json(r["final"]) or {"fatal_errors": [], "other_issues": [], "verified": [],
                                           "score": None, "summary": "unparsed: " + r["final"][-1500:]}
            v["model"] = vm
            return v
        results = pmap(verify, jobs)
        for s in live:
            vs = [v for (js, _, _), v in zip(jobs, results) if js is s]
            # A verifier that returned no score (killed, unparsable) abstains instead of vetoing:
            # unscored Claude verifiers blocked every acceptance in two runs.
            scored = [v for v in vs if isinstance(v.get("score"), (int, float))]
            ok = bool(scored) and all(v["score"] >= a.accept for v in scored) \
                and not any(v.get("fatal_errors") for v in vs)
            if a.check:
                ok = ok and (s.get("check") or {}).get("rc") == 0
            upd(run, state, s, verify=vs, accepted=ok)
            log(run, f"{s['id']} ({s['model']}) round {rnd}: verifier scores "
                     f"{[v.get('score') for v in vs]}, fatal {[len(v.get('fatal_errors') or []) for v in vs]}"
                     f"{', check rc ' + str(s['check']['rc']) if s.get('check') else ''}"
                     f"{' -> ACCEPTED' if ok else ''}")
        for s in solvers:
            ext.note_check(s)
            if ext.stalled(s) and not s.get("stall_noted"):
                s["stall_noted"] = True
                state["notes"].append(f"{s['id']}: same check failure twice; not refined again")
        live = [s for s in solvers if not s["accepted"] and s.get("session") and not ext.stalled(s)]
        if rnd == a.rounds or not live:
            break
        if not time_left():
            state["notes"].append(f"budget reached after round {rnd}; skipped further refinement")
            log(run, state["notes"][-1])
            break
        insights = ""
        if a.share:
            state["stage"] = f"share{rnd + 1}"
            save_state(run, state)
            digest = "".join(
                f"\n### {s['id']} ({s['model']}, {s['angle']}){' ACCEPTED' if s['accepted'] else ''}\n"
                f"{solution_text(s, 6000)}\nVerifiers: {json.dumps(s['verify'])}\n" for s in solvers)
            ip = INSIGHTS.format(n=a.n, problem=problem, digest=digest) + (BOARD_REVIEWER if a.board else "")
            r = run_agent(a.judge, a.verify_effort, ip, run, run / "agents" / f"insights{rnd + 1}",
                          "read-only", minutes(), who=("reviewer", "reviewer"))
            insights = f"\nBrief from a reviewer who read every candidate:\n{r['final']}\n"
            (run / f"INSIGHTS-{rnd + 1}.md").write_text(r["final"])
            with LOCK:
                state.setdefault("usage_extra", {"in": 0, "out": 0, "usd": 0.0})
                for k in r["usage"]:
                    state["usage_extra"][k] += r["usage"][k]
        state["stage"] = f"refine{rnd + 1}"
        save_state(run, state)

        def refine(s):
            upd(run, state, s, stage=f"refine{rnd + 1}")
            rp = REFINE.format(reports=json.dumps(s["verify"], indent=2),
                               check_block=check_block(a.check, s.get("check")), insights=insights)
            if a.board:
                rp += ("\nBefore revising, check the shared board for VERIFIED/REFUTED posts and new evidence "
                       "(search_posts include_unverified=true); post what you learn.")
            m = minutes(0.6)
            r = run_agent(s["model"], a.effort, rp + clock(m), s["dir"], Path(s["meta"]) / f"refine{rnd + 1}",
                          "workspace-write", m, resume=s["session"], who=(s["id"], "solver"))
            add_usage(s, r)
        pmap(refine, live)

    # 3. judge
    state["stage"] = "judge"
    save_state(run, state)
    for s in solvers:
        save_changes(s)
    digest = ""
    for s in solvers:
        diff = Path(s["meta"]) / "changes.diff"
        digest += (f"\n### {s['id']} ({s['model']}, angle {s['angle']})"
                   f"{' TIMED OUT' if s.get('timed_out') else ''}{' ACCEPTED by all verifiers' if s['accepted'] else ''}\n"
                   f"{solution_text(s)}\n"
                   f"{check_block(a.check, s.get('check'))}"
                   f"Diff: {diff.stat().st_size if diff.exists() else 0} bytes at {diff}\n"
                   f"Verifiers: {json.dumps(s['verify'], indent=1)}\n")
    j = run_agent(a.judge, a.verify_effort, JUDGE.format(n=a.n, problem=problem, run=run, digest=digest),
                  run, run / "agents" / "judge", "read-only", max(reserve, 10.0) if deadline else a.timeout,
                  schema=JUDGE_SCHEMA)
    verdict = parse_json(j["final"]) or {"ranking": [], "winner": "", "why": j["final"], "combine": ""}
    total = {"in": 0, "out": 0, "usd": 0.0}
    for u in [s["usage"] for s in solvers] + [state.get("usage_extra", {}), j["usage"]]:
        for k in total:
            total[k] += u.get(k, 0)
    for s in solvers:
        s["stage"] = "accepted" if s["accepted"] else "done"
    state.update(stage="done", verdict=verdict, finished=time.time(), usage=total)
    save_state(run, state)

    by_id = {s["id"]: s for s in solvers}
    usd = f", Claude ${total['usd']:.2f}" if total["usd"] else ""
    lines = [f"# astra compete: {run.name}", "", f"Problem: {problem[:300]}", "",
             f"Winner: **{verdict.get('winner')}**. {verdict.get('why')}", ""]
    if verdict.get("combine"):
        lines += [f"Combine: {verdict['combine']}", ""]
    lines += ["| rank | id | model | angle | judge | verifiers | accepted | check | out tokens |",
              "|---|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(verdict.get("ranking", []), 1):
        s = by_id.get(r["id"], {})
        lines.append(f"| {k} | {r['id']} | {s.get('model')} | {s.get('angle')} | {r['score']} | "
                     f"{[v.get('score') for v in s.get('verify', [])]} | {'yes' if s.get('accepted') else 'no'} | "
                     f"{(s.get('check') or {}).get('rc', '-')} | {s.get('usage', {}).get('out', 0):,} |")
    lines += ["", *[f"- **{r['id']}**: {r['reason']}" for r in verdict.get("ranking", [])], ""]
    lines += [f"- {n}" for n in state["notes"]]
    if a.board:
        lines += [f"- board: {board_stats(run)} (`astra board {run.name}`)"]
    lines += [f"Solutions: {run}/sN/SOLUTION.md (copies + diffs in agents/sN/).",
              f"Apply: `astra apply {run.name}` (winner) or `astra apply {run.name} sN`. "
              f"Free disk: `astra clean {run.name}`.",
              f"Took {(state['finished'] - t0) / 60:.0f} min; tokens in {total['in']:,}, out {total['out']:,}{usd}.",
              "Verifier agreement is evidence, not proof: check a winning proof or result yourself before relying on it."]
    (run / "REPORT.md").write_text("\n".join(lines) + "\n")
    post_global("astra-compete", f"astra compete finished in {(state['finished'] - t0) / 60:.0f} min: winner "
                f"{verdict.get('winner')}. {str(verdict.get('why', ''))[:400]}\nProblem: {problem[:250]}",
                [run / "REPORT.md"])
    print("\n" + "\n".join(lines))


# ---------------------------------------------------------------- status / apply / clean

def find_run(name):
    runs = sorted(p for p in RUNS.glob("*") if p.is_dir())
    if not runs:
        sys.exit("no runs yet")
    if not name:
        return runs[-1]
    hits = [r for r in runs if name in r.name]
    return hits[-1] if hits else sys.exit(f"no run matching {name}")


def board_stats(run):
    import sqlite3
    db = run / "board" / "board.sqlite"
    if not db.exists():
        return "no posts"
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT kind, status, COUNT(*) FROM posts GROUP BY kind, status").fetchall()
        authors = c.execute("SELECT COUNT(DISTINCT author) FROM posts").fetchone()[0]
    return f"{sum(r[2] for r in rows)} posts by {authors} agents: " + ", ".join(f"{k}/{st} {n}" for k, st, n in rows)


def cmd_board(a):
    run = find_run(a.run)
    db = run / "board" / "board.sqlite"
    if not db.exists():
        sys.exit(f"{run.name} has no board (run compete with --board)")
    print(f"{run.name}: {board_stats(run)}", flush=True)
    subprocess.run([sys.executable, str(BOARD_PY), "show", "100"], env={**os.environ, "BOARD_DB": str(db)})


def cmd_status(a):
    run = find_run(a.run)
    st = run / "state.json"
    if not st.exists():
        print(run.name)
        if (run / "log.txt").exists():
            print((run / "log.txt").read_text()[-2000:])
        return
    s = json.loads(st.read_text())
    mins = ((s.get("finished") or time.time()) - s["started"]) / 60
    left = f", {s['budget'] - mins:.0f} min of budget left" if s.get("budget") and not s.get("finished") else ""
    print(f"{run.name}\nstage {s['stage']}, {mins:.0f} min{left}")
    for x in s["solvers"]:
        print(f"  {x['id']} {x['model']:<16} {x['angle']:<16} {x['stage']:<9} "
              f"verifiers={[v.get('score') for v in x.get('verify', [])]} "
              f"{'ACCEPTED ' if x.get('accepted') else ''}check={(x.get('check') or {}).get('rc', '-')} "
              f"out={x.get('usage', {}).get('out', 0):,}")
    if (run / "board" / "board.sqlite").exists():
        print(f"  board: {board_stats(run)} (astra board {run.name})")
    if s.get("verdict"):
        print(f"\nwinner {s['verdict'].get('winner')}: {s['verdict'].get('why', '')[:300]}")
    if (run / "REPORT.md").exists():
        print(f"{run / 'REPORT.md'}")


def cmd_apply(a):
    run = find_run(a.run)
    s = json.loads((run / "state.json").read_text())
    sid = a.solver or (s.get("verdict") or {}).get("winner")
    if not s.get("repo") or not sid:
        sys.exit("nothing to apply (not a git run, or no winner yet)")
    x = next((x for x in s["solvers"] if x["id"] == sid), None)
    if not x:
        sys.exit(f"no solver {sid}")
    if Path(x["dir"]).exists():
        diff = solver_diff(x)
    else:
        diff = (run / "agents" / sid / "changes.diff").read_bytes()
    if not diff:
        sys.exit(f"{sid} made no changes")
    if subprocess.run(["git", "-C", s["repo"], "apply", "-"], input=diff).returncode == 0:
        print(f"applied {sid} to {s['repo']} (working tree only)")
    elif subprocess.run(["git", "-C", s["repo"], "apply", "--3way", "-"], input=diff).returncode == 0:
        print(f"applied {sid} to {s['repo']} with a 3-way merge (changes are staged)")
    else:
        print(f"apply failed; the patch is in {run / 'agents' / sid / 'changes.diff'}")


def cmd_clean(a):
    run = find_run(a.run)
    s = json.loads((run / "state.json").read_text()) if (run / "state.json").exists() else {}
    for x in s.get("solvers", []):
        wd = Path(x["dir"])
        if wd.exists():
            save_changes(x)
            shutil.rmtree(wd, ignore_errors=True)
    print(f"cleaned {run.name}; reports, solutions and diffs kept in {run}")


def cmd_usage(a):
    """Sum tokens (and Claude USD) over every agent call recorded under runs/, per model."""
    totals = {}
    for ev in RUNS.rglob("events.jsonl"):
        if a.since and ev.relative_to(RUNS).parts[0][:8] < a.since.replace("-", ""):
            continue
        try:
            cmd = json.loads((ev.parent / "cmd.json").read_text())
        except (OSError, json.JSONDecodeError):
            cmd = []
        model = "unknown"
        for flag in ("-m", "--model"):
            if flag in cmd:
                model = cmd[cmd.index(flag) + 1]
        if cmd[:1] == ["claude"]:
            model = "claude:" + model
        t = totals.setdefault(model, {"calls": 0, "in": 0, "cached": 0, "out": 0, "reasoning": 0, "usd": 0.0})
        t["calls"] += 1
        for line in ev.read_text(errors="ignore").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "turn.completed":
                u = e.get("usage", {})
                t["in"] += u.get("input_tokens", 0)
                t["cached"] += u.get("cached_input_tokens", 0)
                t["out"] += u.get("output_tokens", 0)
                t["reasoning"] += u.get("reasoning_output_tokens", 0)
            elif e.get("type") == "result":
                u = e.get("usage", {})
                t["in"] += u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                t["cached"] += u.get("cache_read_input_tokens", 0)
                t["out"] += u.get("output_tokens", 0)
                t["usd"] += e.get("total_cost_usd") or 0.0
    print(f"{'model':<18} {'calls':>6} {'input':>14} {'of which cached':>16} {'output':>12} {'reasoning':>12} {'USD':>11}")
    table, unpriced = ext.prices(), []
    for m, t in sorted(totals.items(), key=lambda kv: -kv[1]["in"]):
        u = t["usd"] if m.startswith("claude:") else ext.usd(m, t, table)
        unpriced += [m] if u is None else []
        print(f"{m:<18} {t['calls']:>6} {t['in']:>14,} {t['cached']:>16,} {t['out']:>12,} {t['reasoning']:>12,} "
              f"{(f'{u:.2f}' if u is not None else '-'):>11}")
    if unpriced:
        print(f"No USD for {', '.join(unpriced)}: add $/Mtok as {{\"model\": {{\"in\": .., \"cached\": .., \"out\": ..}}}} "
              f"to {ext.PRICES}. Claude USD is the CLI's own estimate.")
    if getattr(a, "reviews", False):
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        stats = Path(root) / "tools" / "review_daemon" / "inbox_stats.py"
        print()
        subprocess.run([sys.executable, str(stats)]) if stats.exists() else print("no tools/review_daemon/inbox_stats.py here")
    print("Input includes cached input. Calls that were killed before finishing may show partial or no usage.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("do", "review"):
        p = sub.add_parser(name)
        p.add_argument("task", help="task text, or @file")
        p.add_argument("-m", "--model", default="gpt-6-astra")
        p.add_argument("-e", "--effort", default="high")
        p.add_argument("-C", "--dir", default=".")
        p.add_argument("-t", "--timeout", type=float, default=None,
                       help="minutes (default: p90 x 1.5 of past runs of this kind, else 120)")
        p.add_argument("--force", action="store_true",
                       help="run even if the brief check finds problems (only Claude-launched runs are stopped)")
        p.add_argument("--plan", action="store_true",
                       help="do: write a plan first; a cross-family critic checks it against the brief")
        p.add_argument("--network", action="store_true",
                       help="let the sandboxed run use the network (ssh to a build VM, downloads)")
    p = sub.add_parser("compete")
    p.add_argument("problem", help="problem text, or @file")
    p.add_argument("-n", type=int, default=6, help="number of solvers")
    p.add_argument("--models", default="gpt-6-astra,gpt-6.1-sol,claude:opus",
                   help="comma list cycled over solvers; claude:<model> (e.g. claude:opus) runs Claude Code")
    p.add_argument("-e", "--effort", default="ultra", help="solver effort")
    p.add_argument("--verifiers", type=int, default=2, help="independent verifiers per answer per round")
    p.add_argument("--accept", type=float, default=8, help="min verifier score to accept (all must agree)")
    p.add_argument("--verify-effort", default="max", help="effort for verifiers, insights and judge")
    p.add_argument("--judge", default="gpt-6-astra")
    p.add_argument("--check", help="shell command run in each workspace (tests, eval script, `lake build`)")
    p.add_argument("--rounds", type=int, default=2, help="verify->refine rounds after the first answers")
    p.add_argument("--no-share", dest="share", action="store_false",
                   help="keep solvers fully independent (no shared insights between rounds)")
    p.add_argument("--budget", type=float, help="total wall-clock minutes; a hard stop")
    p.add_argument("--network", action="store_true",
                   help="let Codex solvers use the network (pip install, papers); off by default")
    p.add_argument("-C", "--dir", default=".")
    p.add_argument("-t", "--timeout", type=float, default=90, help="max minutes per agent call")
    p.add_argument("--bg", action="store_true", help="run detached; follow with `astra status`")
    p.add_argument("--board", action="store_true",
                   help="shared message board (MCP) for solvers, verifiers and the reviewer; "
                        "verifiers mark posts verified/refuted")
    for name in ("status", "clean", "board"):
        p = sub.add_parser(name)
        p.add_argument("run", nargs="?")
    p = sub.add_parser("apply")
    p.add_argument("run", nargs="?")
    p.add_argument("solver", nargs="?")
    p = sub.add_parser("usage", help="token totals per model over all runs")
    p.add_argument("--since", help="YYYY-MM-DD: only runs started on or after this date")
    p.add_argument("--reviews", action="store_true", help="also print the acted-on rate per reviewer from REVIEW_INBOX.md")
    p = sub.add_parser("deliver", help="check HEAD against the repo's deliver gate; --push sends it to origin/main")
    p.add_argument("-C", "--dir", default=".")
    p.add_argument("--push", action="store_true")
    p.add_argument("--force", action="store_true", help="owner only: push even if the gate fails")
    a = ap.parse_args()
    {"do": lambda: cmd_single(a, "workspace-write"), "review": lambda: cmd_single(a, "read-only"),
     "compete": lambda: cmd_compete(a), "status": lambda: cmd_status(a), "apply": lambda: cmd_apply(a),
     "clean": lambda: cmd_clean(a), "usage": lambda: cmd_usage(a),
     "deliver": lambda: sys.exit(ext.deliver(Path(a.dir).resolve(), a.push, a.force, print)), "board": lambda: cmd_board(a)}[a.cmd]()


if __name__ == "__main__":
    main()
