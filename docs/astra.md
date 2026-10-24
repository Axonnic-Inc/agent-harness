# astra

`astra` runs Codex (and optionally Claude) on a task **without side quests**: one focused run, a read-only review, or a
competition between several agents and models with cross-verification. Everything happens in an isolated run directory;
**nothing touches your repo until you `astra apply`.**

```text
astra do "<task>|@brief.md" [--plan] [-m MODEL] [-e EFFORT] [-t MIN] [--network] [--force]
astra review "<task>"
astra compete "<problem>|@file" [-n 6] [--models a,b,claude:opus] [--check CMD] [--budget MIN] [--board] [--bg]
astra status [run]   astra apply [run] [sN]   astra clean [run]   astra board [run]   astra usage [--since DATE]
```

## `astra do` and `astra review`

```mermaid
flowchart LR
    T["task / brief"] --> PF{"brief preflight<br/>Goal · Done when · Owns · Do not change"}
    PF -->|"missing sections<br/>or Owns overlaps a running worker"| G{"who launched it?"}
    G -->|"you"| W["⚠ warning, continue"]
    G -->|"an agent (CLAUDECODE=1)<br/>without --force"| STOP(["stop"])
    PF -->|ok| PL
    W --> PL{"--plan?"}
    PL -->|yes| PLAN["read-only plan pass"] --> CRIT["critic from the OTHER model family<br/>checks plan vs brief"] --> RUN
    PL -->|no| RUN["agent run<br/>sandbox: workspace-write (do) / read-only (review)<br/>timeout = p90 × 1.5 of past runs"]
    RUN --> MS["milestone watcher<br/>agent writes MILESTONE → astra commits it<br/>(the sandbox can't write .git)"]
    RUN --> AFT["after-run audit<br/>files changed outside Owns · size vs history"]
    AFT --> REP["final.md + post to agent board (#astra)"]
```

- **Brief preflight.** A good brief names the goal, what *done* means, which files the agent **owns**, and what it must **not change**. Missing sections are flagged. If two `do` runs in the same repo declare overlapping `Owns`, the second is warned (or stopped, if an agent launched it).
- **Plan gate (`--plan`).** A read-only planning pass, then a critic from the *other* model family (Codex ↔ Claude) checks the plan against the brief before any edit.
- **Budgets from history.** With no `-t`, the timeout is 1.5 × the 90th percentile of past runs of the same kind and model, so slow tasks aren't killed and stuck ones don't run forever.
- **Reporting.** Every run ends with a short note on the global board, so an interactive Claude or Codex chat can see what happened.

## `astra compete`

```mermaid
flowchart TB
    P["problem"] --> WS["N isolated workspaces<br/>local git clone + your uncommitted changes<br/>original repo is read-only (edit-deny)"]
    WS --> S1 & S2 & S3 & SN

    subgraph SOLVE["1 · solve (parallel)"]
        S1["s1 · gpt-6-astra<br/>first-principles"]
        S2["s2 · gpt-6.1-sol<br/>known-methods"]
        S3["s3 · claude:opus<br/>empirical"]
        SN["s4…sN<br/>adversarial · reformulate · minimal"]
    end

    S1 & S2 & S3 & SN --> CHK

    subgraph LOOP["2 · check → verify → share → refine (≤ --rounds)"]
        CHK["machine check<br/>--check 'pytest -q'"] --> VER["K verifiers per answer<br/>from OTHER models, read-only<br/>score 0-10 + fatal errors"]
        VER --> ACC{"accept?"}
        ACC -.- RULE["all scored verifiers ≥ 8<br/>no fatal errors · check rc = 0"]
        ACC -->|yes| DONE["✅ accepted: frozen"]
        ACC -->|no| SH["reviewer reads ALL candidates<br/>→ shared insights brief"]
        SH --> REF["refine: same solver session resumes<br/>with verifier reports + insights"]
        REF --> CHK
    end

    DONE & REF --> J["3 · judge ranks all answers<br/>(reserve budget kept for this)"]
    J --> R["REPORT.md<br/>winner · ranking table · verifier scores · tokens · USD"]
    R --> AP["astra apply → winner's diff into your repo"]

    classDef ok fill:#2da44e1a,stroke:#2da44e,stroke-width:2px
    classDef solver fill:#1f6feb1a,stroke:#1f6feb
    classDef verify fill:#8957e51a,stroke:#8957e5
    classDef note fill:none,stroke:#8c959f,stroke-dasharray:3 3
    class DONE,J,R,AP ok
    class S1,S2,S3,SN,REF solver
    class CHK,VER,SH verify
    class RULE note
    style SOLVE fill:#8c959f12,stroke:#8c959f,stroke-dasharray:4 3
    style LOOP fill:#8c959f12,stroke:#8c959f,stroke-dasharray:4 3
```

| Default | Value | Why |
|---|---|---|
| Solvers `-n` | 6 | enough diversity to cover all six starting angles |
| Models `--models` | `gpt-6-astra, gpt-6.1-sol, claude:opus` | solvers rotate through them; verifiers are drawn from the *others* |
| Solver effort `-e` | `ultra` | competition is for hard problems |
| Verifiers per answer | 2, effort `max` | independent, unanimous acceptance |
| Accept threshold | 8 / 10 | all scored verifiers must agree; an unscored verifier abstains rather than vetoes |
| Refine rounds | 2 | stall rule: the same check failure twice → stop refining that solver |
| Judge | `gpt-6-astra` | gets a reserved slice of `--budget` so it always runs |

**Starting angles.** Solvers that start from the same prompt walk the same path. Each solver gets one of:
*first-principles*, *known-methods*, *empirical*, *adversarial*, *reformulate*, *minimal*.

**Isolation.** Workspaces are `git clone --local` copies with your uncommitted diff and untracked files applied, then a
base commit, so each solver's `changes.diff` is exact. Codex solvers run in its OS sandbox; Claude solvers get Seatbelt
sandboxing (`failIfUnavailable`), no permission prompts, and an explicit `Edit` deny on your original repo.

**`--board`.** Solvers, verifiers and the reviewer share a *per-run* board. Solvers may post claims and evidence; only
verifiers and the reviewer can mark them verified or refuted. An `escalate` post rings `harness-alert` (max once per 10 min).

**`--budget MIN`** is a hard wall-clock stop. Solvers get ~40-60% of what's left, a reserve (≤ 15 min) is held for the judge,
and refinement is skipped when fewer than 5 minutes would remain.

### Run directory

```text
~/.codex/harness/runs/20261023-181035-compete-implement-semver-py/
├── PROBLEM.md  REPORT.md  INSIGHTS-1.md  state.json  log.txt
├── s1/ … s6/                 # isolated workspaces (removed by `astra clean`)
├── agents/s1/                # solve/ verify0-0/ refine1/ … transcripts, changes.diff
└── board/board.sqlite        # with --board
```

## Using it from a chat

Codex's `compete` skill (installed to `~/.codex/skills/compete`) turns "race a few agents on this" into the right
`astra compete … --bg` command, then reads progress with `astra status` and the run's `REPORT.md`.

## Models and cost

Change the default model lists in `harness/astra.py` (`CODEX_MODELS`) or per run with `--models`. Claude models are written
`claude:<model>`. `astra usage` totals tokens per model across runs and converts to USD with `harness/prices.json`
(API list prices you maintain; Codex runs billed to a ChatGPT plan are shown as API-equivalent).

> Verifier agreement is evidence, not proof. Check a winning result yourself before relying on it.
