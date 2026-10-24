#!/usr/bin/env python3
"""board: a shared message board for Claude Code and Codex agents (MCP over stdio, no deps).

Tool names and limits follow Codex's built-in agent message board (post_message, search_posts,
read_post, list_threads): pull-first, writes return ids, reads are previews, posts are short.
Two additions from the multi-agent literature: every post has a kind and a status, and only a
verifier, reviewer or human can mark a post verified or refuted (never their own), so
unchecked claims cannot pass as facts.

  env BOARD_DB      SQLite file (default ~/.agent-board/board.sqlite)
      BOARD_AGENT   author name shown on posts (e.g. claude, codex, s1); "{project}" becomes
                    the session's directory name (claude@{project} -> claude@myapp)
      BOARD_ROLE    solver | verifier | reviewer | human | agent (default agent)
      BOARD_ALERT   optional shell command run when an `escalate` post is made (max 1 per 10 min)

  board.py              run the MCP server (what Claude Code / Codex launch)
  board.py show [N]     print the latest N threads (read-only)
  board.py prune DAYS   delete threads whose last activity is older than DAYS days
"""
import json, os, sqlite3, subprocess, sys, time
from pathlib import Path

DB = Path(os.environ.get("BOARD_DB") or Path.home() / ".agent-board" / "board.sqlite").expanduser()
AGENT = os.environ.get("BOARD_AGENT", "agent").replace("{project}", Path.cwd().name)
ROLE = os.environ.get("BOARD_ROLE", "agent")
JUDGES = ("verifier", "reviewer", "human")
KINDS = ["claim", "evidence", "refutation", "artifact", "question", "escalate", "note"]
ALERT_COOLDOWN = 600  # seconds: one human alert per 10 minutes, however many escalations
MAX_BODY, PREVIEW = 1000, 200


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, channel TEXT NOT NULL,
            thread_id INTEGER, author TEXT NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unverified', body TEXT NOT NULL, refs TEXT NOT NULL,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS status_log (post_id INTEGER NOT NULL, by TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS seen (agent TEXT PRIMARY KEY, last_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL NOT NULL);""")
    return c


def label(r):
    return f"#{r['id']} [{r['status'].upper()}] {r['kind']} by {r['author']} in {r['channel']}"


def preview(r):
    b = r["body"].replace("\n", " ")
    return {"id": r["id"], "head": label(r), "thread_id": r["thread_id"],
            "text": b if len(b) <= PREVIEW else b[:PREVIEW] + "…"}


def news(c, mark=False):
    """Posts by others since this agent last listed or searched (there is no push: this is the nudge)."""
    row = c.execute("SELECT last_id FROM seen WHERE agent=?", (AGENT,)).fetchone()
    last = row[0] if row else 0
    n = c.execute("SELECT COUNT(*) FROM posts WHERE id>? AND author!=?", (last, AGENT)).fetchone()[0]
    if mark:
        top = c.execute("SELECT COALESCE(MAX(id), 0) FROM posts").fetchone()[0]
        c.execute("INSERT INTO seen VALUES (?, ?) ON CONFLICT(agent) DO UPDATE SET last_id=excluded.last_id",
                  (AGENT, top))
    return n


def post_message(body, kind="note", channel="general", thread_id=None, refs=None):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not body.strip():
        raise ValueError("empty body")
    if len(body) > MAX_BODY:
        raise ValueError(f"body is {len(body)} chars; max {MAX_BODY}. Put details in a file and pass its path in refs.")
    c = db()
    c.execute("BEGIN IMMEDIATE")
    dup = c.execute("SELECT id FROM posts WHERE author=? AND channel=? AND body=?",
                    (AGENT, channel, body.strip())).fetchone()
    if dup and thread_id is None:
        c.execute("ROLLBACK")
        raise ValueError(f"you already posted this as #{dup['id']}")
    if thread_id is not None:
        root = c.execute("SELECT channel, thread_id FROM posts WHERE id=?", (thread_id,)).fetchone()
        if not root:
            c.execute("ROLLBACK")
            raise ValueError(f"no post #{thread_id}")
        if root["thread_id"] is not None:
            thread_id = root["thread_id"]  # replies attach to the thread's first post
        channel = root["channel"]
    cur = c.execute("INSERT INTO posts (channel, thread_id, author, role, kind, body, refs, created) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (channel, thread_id, AGENT, ROLE, kind, body.strip(), json.dumps(refs or []), time.time()))
    c.execute("COMMIT")
    pid = cur.lastrowid
    if kind == "escalate" and os.environ.get("BOARD_ALERT"):
        c.execute("BEGIN IMMEDIATE")
        last = c.execute("SELECT value FROM meta WHERE key='last_alert'").fetchone()
        fire = not last or time.time() - last[0] > ALERT_COOLDOWN
        if fire:
            c.execute("INSERT OR REPLACE INTO meta VALUES ('last_alert', ?)", (time.time(),))
        c.execute("COMMIT")
        if fire:
            subprocess.Popen(os.environ["BOARD_ALERT"], shell=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    out = {"id": pid, "thread_id": thread_id or pid, "channel": channel}
    n = news(db())
    if n:
        out["new_posts_by_others"] = f"{n} since your last list_threads/search_posts"
    return out


def search_posts(query=None, channel=None, author=None, kind=None, status=None, after_id=None,
                 include_unverified=None, limit=20):
    if include_unverified is None:
        include_unverified = ROLE != "solver"  # solvers see checked posts unless they opt in
    sql, args = "SELECT * FROM posts WHERE 1=1", []
    for col, val in (("channel", channel), ("author", author), ("kind", kind), ("status", status)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if query:
        sql += " AND body LIKE ? ESCAPE '\\'"
        args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if after_id:
        sql += " AND id>?"
        args.append(after_id)
    if not include_unverified and not status:
        sql += " AND (status!='unverified' OR author=?)"
        args.append(AGENT)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    c = db()
    rows = c.execute(sql, args).fetchall()
    out = {"results": [preview(r) for r in rows], "new_since_last_check": news(c, mark=True)}
    if not include_unverified and not status:
        hidden = db().execute("SELECT COUNT(*) FROM posts WHERE status='unverified' AND author!=?",
                              (AGENT,)).fetchone()[0]
        if hidden:
            out["hidden_unverified"] = f"{hidden} unverified posts by others hidden; pass include_unverified=true to see them as hypotheses"
    return out


def read_post(id):
    c = db()
    r = c.execute("SELECT * FROM posts WHERE id=?", (id,)).fetchone()
    if not r:
        raise ValueError(f"no post #{id}")
    log = [dict(x) for x in c.execute("SELECT by, status, reason FROM status_log WHERE post_id=? ORDER BY at", (id,))]
    root = r["thread_id"] or r["id"]
    replies = c.execute("SELECT COUNT(*) FROM posts WHERE thread_id=?", (root,)).fetchone()[0]
    return {"head": label(r), "thread_id": root, "body": r["body"], "refs": json.loads(r["refs"]),
            "status_history": log, "replies_in_thread": replies}


def list_threads(channel=None, limit=20):
    c = db()
    sql, args = "SELECT * FROM posts WHERE thread_id IS NULL", []
    if channel:
        sql += " AND channel=?"
        args.append(channel)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    out = []
    for r in c.execute(sql, args).fetchall():
        t = preview(r)
        last = c.execute("SELECT * FROM posts WHERE thread_id=? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        t["replies"] = c.execute("SELECT COUNT(*) FROM posts WHERE thread_id=?", (r["id"],)).fetchone()[0]
        if last:
            t["latest_reply"] = preview(last)
        out.append(t)
    return {"threads": out, "new_since_last_check": news(c, mark=True)}


def set_status(id, status, reason):
    if ROLE not in JUDGES:
        raise PermissionError(f"role '{ROLE}' cannot set status; only {', '.join(JUDGES)} can")
    if status not in ("verified", "refuted", "unverified"):
        raise ValueError("status must be verified, refuted or unverified")
    if not reason.strip():
        raise ValueError("give the evidence or reason")
    c = db()
    c.execute("BEGIN IMMEDIATE")
    r = c.execute("SELECT author FROM posts WHERE id=?", (id,)).fetchone()
    if not r:
        c.execute("ROLLBACK")
        raise ValueError(f"no post #{id}")
    if r["author"] == AGENT:
        c.execute("ROLLBACK")
        raise PermissionError("you cannot judge your own post")
    c.execute("UPDATE posts SET status=? WHERE id=?", (status, id))
    c.execute("INSERT INTO status_log VALUES (?,?,?,?,?)", (id, AGENT, status, reason[:MAX_BODY], time.time()))
    c.execute("COMMIT")
    return {"id": id, "status": status}


S, I, B, A = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "array", "items": {"type": "string"}}
TOOLS = [
    ("post_message", post_message,
     "Post to the shared board read by every agent in this collaboration (Claude and Codex). Start a thread "
     "in a channel, or reply with thread_id. Body max 1000 chars: put long content in a file and pass its "
     "path in refs. Use kind to say what it is: claim (needs checking), evidence (command + output), "
     "refutation, artifact (a file others can reuse), question, escalate (ONLY when a human must act: "
     "the task, grader or environment is broken or being gamed; never for status updates or reviews), note. New posts are UNVERIFIED until a verifier or reviewer checks them. Returns ids, not text.",
     {"body": S, "kind": {"type": "string", "enum": KINDS}, "channel": S, "thread_id": I, "refs": A}, ["body"]),
    ("search_posts", search_posts,
     "Search posts newest first (case-insensitive substring; omit query for recent activity). Filter by "
     "channel, author, kind, status, or after_id. Results are 200-char previews with status labels; use "
     "read_post for full text. Treat UNVERIFIED posts as hypotheses, not facts.",
     {"query": S, "channel": S, "author": S, "kind": S, "status": S, "after_id": I,
      "include_unverified": B, "limit": I}, []),
    ("read_post", read_post, "Read one post in full by id, with its refs and status history.",
     {"id": I}, ["id"]),
    ("list_threads", list_threads,
     "List threads (first post + reply count + latest reply preview), newest first, optionally in one channel.",
     {"channel": S, "limit": I}, []),
    ("set_status", set_status,
     "Mark another agent's post verified or refuted, with the evidence. Only verifier, reviewer or human "
     "roles may do this, never on their own posts.",
     {"id": I, "status": {"type": "string", "enum": ["verified", "refuted", "unverified"]}, "reason": S},
     ["id", "status", "reason"]),
]
FN = {name: fn for name, fn, *_ in TOOLS}


def serve():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue  # notifications (initialized, cancelled)
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-board", "version": "1.0"},
                   "instructions": f"Shared board between Claude Code and Codex agents. You post as '{AGENT}' "
                                   f"(role '{ROLE}'). Use it to hand work to or from the other agent: post a "
                                   "thread with what was done, file paths in refs and what is next; check "
                                   "list_threads when the user mentions the other agent or a handover."}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": d,
                              "inputSchema": {"type": "object", "properties": p, "required": r}}
                             for n, _, d, p, r in TOOLS]}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                if name not in FN:
                    raise ValueError(f"unknown tool {name}")
                res = {"content": [{"type": "text", "text": json.dumps(FN[name](**args), ensure_ascii=False)}]}
            except Exception as e:  # tool errors go back to the model, not the protocol
                res = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": mid,
                              "error": {"code": -32601, "message": f"method not found: {method}"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}, ensure_ascii=False), flush=True)


def show(n=20):
    for t in reversed(list_threads(limit=n)["threads"]):
        print(f"{t['head']}  ({t['replies']} replies)\n  {t['text']}")
        for r in db().execute("SELECT * FROM posts WHERE thread_id=? ORDER BY id", (t["id"],)):
            print(f"    ↳ {label(r)}: {preview(r)['text']}")


def prune(days):
    c = db()
    cut = time.time() - float(days) * 86400
    old = [r[0] for r in c.execute(
        "SELECT p.id FROM posts p WHERE p.thread_id IS NULL AND "
        "COALESCE((SELECT MAX(created) FROM posts q WHERE q.thread_id=p.id), p.created) < ?", (cut,))]
    c.execute("BEGIN IMMEDIATE")
    for t in old:
        c.execute("DELETE FROM status_log WHERE post_id IN (SELECT id FROM posts WHERE id=? OR thread_id=?)", (t, t))
        c.execute("DELETE FROM posts WHERE id=? OR thread_id=?", (t, t))
    c.execute("COMMIT")
    print(f"deleted {len(old)} threads older than {days} days from {DB}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["show"]:
        show(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif sys.argv[1:2] == ["prune"] and len(sys.argv) == 3:
        prune(sys.argv[2])
    else:
        serve()
