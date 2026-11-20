#!/usr/bin/env python3
# Token use per Claude Code session and per project stage, from the local session logs.
# Usage: python scripts/ai_usage.py [--sessions] [--prompts <out dir>] [--update docs/AI_USAGE.md]
import getpass
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGDIR = os.path.join(os.path.expanduser("~"), ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", REPO))

# stage, last commit of the stage (messages after the previous stage's last commit and up to this one belong to it)
STAGES = [
    ("1 distill + setup", "fc5bea1"),
    ("2a architecture candidates", "3882b20"),
    ("2b spec (ARCH, ISA, PINS, HOSTIF, VERIF)", "e096d51"),
    ("3 spec review", "1aa3259"),
    ("4 ISS, assembler, tests", "dff379b"),
    ("5 walking skeleton + GDS", "c4bd4aa"),
    ("6 RTL blocks", "cc42573"),
    ("6b integration, timing fix, RTL review", "77d6bed"),
    ("7 protocol bring-up (UART..CAN)", "e02b644"),
    ("8a formal", "2dc1fdc"),
    ("8b timing closure", "081faea"),
    ("8c gate level + IR drop", "9612ac3"),
    ("8d cleanup", "4105b24"),
    ("9 write-up", None),
]


def commit_time(h):
    return int(subprocess.check_output(["git", "-C", REPO, "log", "-1", "--format=%at", h]).decode())


def iso_epoch(ts):
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def stage_of(t, bounds):
    for name, end in bounds:
        if end is None or t <= end:
            return name
    return bounds[-1][0]


def read(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def usage_records(path):
    # one record per API message id; a message split over several log lines repeats its usage
    seen = {}
    for d in read(path):
        if d.get("type") != "assistant":
            continue
        m = d.get("message", {})
        u = m.get("usage")
        if not u or m.get("model") in (None, "<synthetic>"):
            continue
        seen[m.get("id") or d.get("uuid")] = (iso_epoch(d["timestamp"]), m["model"], d.get("effort"), u)
    return seen.values()


def typed_prompts(path):
    for d in read(path):
        if d.get("type") != "user" or d.get("isMeta"):
            continue
        c = d.get("message", {}).get("content")
        o = d.get("origin")
        if not isinstance(c, str) or (isinstance(o, dict) and o.get("kind") != "human"):
            continue
        if c.startswith(("<local-command", "<task-notification", "<bash-stdout", "This session is being continued")) \
                or "<agent-message" in c[:200]:
            continue
        c = re.sub(r"</?pasted_content[^>]*>", "", c).strip()
        cmd = re.search(r"<command-name>(/[^<]+)</command-name>", c)
        if cmd:
            args = re.search(r"<command-args>(.*?)</command-args>", c, re.S)
            if cmd.group(1) == "/clear":
                continue
            c = cmd.group(1) + ((" " + args.group(1).strip()) if args and args.group(1).strip() else "")
        yield iso_epoch(d["timestamp"]), d.get("permissionMode") or "not recorded", c


def redact(s):
    home = os.path.expanduser("~")
    s = s.replace(home, "~")
    try:
        email = subprocess.check_output(["git", "-C", REPO, "config", "user.email"]).decode().strip()
    except subprocess.CalledProcessError:
        email = ""
    if email:
        s = s.replace(email, "<email>")
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", s)
    user = getpass.getuser()
    return re.sub(r"\b%s\b" % re.escape(user), "<user>", s)


def main():
    bounds = [(n, commit_time(h) if h else None) for n, h in STAGES]
    mains = sorted(glob.glob(os.path.join(LOGDIR, "*.jsonl")))
    if not mains:
        sys.exit("no session logs under " + LOGDIR)
    tok = defaultdict(Counter)        # stage -> token counters
    models = defaultdict(Counter)     # stage -> model -> messages (main sessions)
    efforts = defaultdict(Counter)
    modes = defaultdict(Counter)
    agents = defaultdict(Counter)     # stage -> "type (model)" -> subagent runs
    sess = []
    prompts = defaultdict(list)
    for p in mains:
        sid = os.path.basename(p)[:-6]
        st = Counter()
        t0 = t1 = None
        for t, model, effort, u in usage_records(p):
            s = stage_of(t, bounds)
            k = Counter(inp=u.get("input_tokens", 0), out=u.get("output_tokens", 0),
                        cw=u.get("cache_creation_input_tokens", 0), cr=u.get("cache_read_input_tokens", 0), msg=1)
            tok[s].update(k)
            st.update(k)
            st[s] += 1
            models[s][model] += 1
            efforts[s][effort or "?"] += 1
            t0 = t if t0 is None else min(t0, t)
            t1 = t if t1 is None else max(t1, t)
        for t, mode, text in typed_prompts(p):
            s = stage_of(t, bounds)
            modes[s][mode or "?"] += 1
            prompts[s].append((t, sid, mode, text))
        for a in glob.glob(os.path.join(LOGDIR, sid, "subagents", "agent-*.jsonl")):
            meta = {}
            mp = a[:-6] + ".meta.json"
            if os.path.exists(mp):
                meta = json.load(open(mp))
            first = None
            for t, model, effort, u in usage_records(a):
                s = stage_of(t, bounds)
                first = first or (s, model)
                k = Counter(inp=u.get("input_tokens", 0), out=u.get("output_tokens", 0),
                            cw=u.get("cache_creation_input_tokens", 0), cr=u.get("cache_read_input_tokens", 0),
                            msg=1, sub=1)
                tok[s].update(k)
                st.update(k)
            if first:
                agents[first[0]]["%s (%s)" % (meta.get("agentType", "?"), first[1])] += 1
        if t0 is not None:
            main_stage = max((n for n, _ in bounds), key=lambda n: st[n])
            sess.append((t0, sid, main_stage, st))

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        table(bounds, tok, models, efforts, modes, agents, sess)
    sys.stdout.write(buf.getvalue())
    if "--update" in sys.argv:
        doc = sys.argv[sys.argv.index("--update") + 1]
        s = open(doc, encoding="utf-8").read()
        a, b = "<!-- ai_usage:begin -->", "<!-- ai_usage:end -->"
        s = s[:s.index(a) + len(a)] + "\n" + buf.getvalue() + s[s.index(b):]
        open(doc, "w", encoding="utf-8").write(s)
    if "--sessions" in sys.argv:
        sessions(sess)
    if "--prompts" in sys.argv:
        write_prompts(bounds, prompts)


def k(n):
    return "{:,}".format(n).replace(",", " ")


def table(bounds, tok, models, efforts, modes, agents, sess):
    print("| Stage | Main-session models (messages) | Effort (messages) | Permission mode (prompts) | Subagents (runs) "
          "| Input | Output | Cache write | Cache read |")
    print("|---|---|---|---|---|---|---|---|---|")
    total = Counter()
    for n, _ in bounds:
        c = tok[n]
        total.update(c)
        if not c["msg"]:
            print("| %s | no log | | | | | | | |" % n)
            continue
        f = lambda cnt: ", ".join("%s %d" % kv for kv in cnt.most_common())
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            n, f(models[n]), f(efforts[n]), f(modes[n]), f(agents[n]) or "—",
            k(c["inp"]), k(c["out"]), k(c["cw"]), k(c["cr"])))
    print("| **total** | | | | | %s | %s | %s | %s |" % (k(total["inp"]), k(total["out"]), k(total["cw"]), k(total["cr"])))
    print("\nsessions: %d main logs, subagent messages included in their session's stage by timestamp" % len(sess))


def sessions(sess):
    from datetime import datetime, timezone
    print("\n| Session | Start (UTC) | Main stage | API messages | Output tokens |")
    print("|---|---|---|---|---|")
    for t0, sid, ms, st in sorted(sess):
        print("| %s | %s | %s | %d | %s |" % (sid[:8], datetime.fromtimestamp(t0, timezone.utc).strftime("%m-%d %H:%M"),
                                             ms, st["msg"], k(st["out"])))


def write_prompts(bounds, prompts):
    from datetime import datetime, timezone
    out = sys.argv[sys.argv.index("--prompts") + 1]
    os.makedirs(out, exist_ok=True)
    for i, (n, _) in enumerate(bounds):
        if not prompts[n]:
            continue
        fn = os.path.join(out, "%02d_%s.md" % (i + 1, re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_")))
        with open(fn, "w", encoding="utf-8") as f:
            f.write("# Prompts: stage %s\n\nTyped prompts in order, verbatim except redaction (home path, user name, "
                    "e-mail). Generated by scripts/ai_usage.py --prompts.\n" % n)
            for t, sid, mode, text in sorted(prompts[n]):
                stamp = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                f.write("\n## %s · session %s · mode %s\n\n````text\n%s\n````\n" % (stamp, sid[:8], mode, redact(text)))
        print("wrote", fn)


if __name__ == "__main__":
    main()
