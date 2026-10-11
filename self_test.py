#!/usr/bin/env python3
"""Self-test for reflect-memo.
Tiers: [A] functional / [B] exception / [C] extreme edges. Run: python self_test.py
"""
import copy
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from main import (MemoError, apply_diff, bullet_id, distill, extract_candidates,
                  load_session, load_state, main, render_page)

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def msg(text, role="user", ts=1.0):
    return {"ts": ts, "role": role, "text": text}


SESS = [
    msg("I prefer small pull requests over big ones. We decided to use sqlite for storage.", ts=1),
    msg("We'll use sqlite for storage in this project.", role="assistant", ts=2),
    msg("The bug was a timezone offset in the cron parser.", ts=3),
    msg("hi", ts=4),  # too short -> ignored
    msg("Let's have lunch sometime.", ts=5),  # no rule match -> ignored
]

# ---------------------------------------------------------------- A
print("[A] functional unit tests")
bid_pref = bullet_id("I prefer small pull requests over big ones.")
check("bullet id stable", bullet_id("  i PREFER   small pull requests over big ones. ") == bid_pref)
check("bullet id 12 hex", len(bid_pref) == 12)

cands = extract_candidates(SESS)
check("candidates extracted", len(cands) >= 3)
sections = {c["section"] for c in cands.values()}
check("sections cover preferences/decisions/facts",
      {"preferences", "decisions", "facts"} <= sections)
check("dedup by normalized text",
      sum(c["count"] for c in cands.values() if "sqlite" in c["text"]) == 2)

state0 = {"version": 1, "bullets": {}, "distills": 0}
new_state, diff = distill(state0, SESS)
check("diff lists adds", len(diff["added"]) >= 3)
check("proposed status", all(b["status"] == "proposed" for b in new_state["bullets"].values()))
check("distill counter", new_state["distills"] == 1)

# apply promotes to active
st1 = apply_diff(copy.deepcopy(new_state), diff)
check("apply promotes all", all(b["status"] == "active" for b in st1["bullets"].values()))
page = render_page(st1)
check("page renders sections", "## Preferences" in page and "## Decisions" in page)
check("page shows ids and counts", bid_pref in page and "observed x" in page)

# partial acceptance: reject one
some_id = diff["added"][0]["id"]
st2 = apply_diff(copy.deepcopy(new_state), diff, accept_ids={some_id})
active2 = [b for b in st2["bullets"].values() if b["status"] == "active"]
check("partial accept promotes subset", len(active2) == 1 and active2[0]["id"] == some_id)

# second distill: re-observation bumps counts, adds nothing new
_, diff2 = distill(st1, SESS)
check("no duplicate adds on re-distill", diff2["added"] == [])
check("bumps recorded", len(diff2["bumped"]) >= 1)
check("kept counts actives", diff2["kept"] == len(st1["bullets"]))

# min_count filters one-offs
_, diff3 = distill(state0, SESS, min_count=2)
check("min_count filters", all("sqlite" in a["text"] for a in diff3["added"]))

# round-2: rewording detection
sA = [msg("We'll use sqlite for storage in this project.", ts=1)]
stA, dA = distill(state0, sA)
stA = apply_diff(stA, dA)
sB = [msg("we'll use SQLite for the storage layer of the project.", ts=2)]
stB, dB = distill(stA, sB)
check("reworded candidate flagged", len(dB["possibly_reworded"]) == 1
      and dB["possibly_reworded"][0]["overlap"] >= 0.55)
check("reworded still proposed separately", dB["added"] != [])
unrelated = [msg("The bug was an off-by-one error in pagination.", ts=3)]
_, dU = distill(stA, unrelated)
check("unrelated add not flagged", dU["possibly_reworded"] == [])

# CLI smoke
fd, SESS_F = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
with open(SESS_F, "w", encoding="utf-8") as f:
    for m in SESS:
        f.write(json.dumps(m) + "\n")
STATE = SESS_F + ".state.json"
rc = main(["distill", "--session", SESS_F, "--state", STATE])
check("CLI distill exit 0", rc == 0 and os.path.exists(STATE + ".new"))
DIFF_F = os.path.splitext(STATE)[0] + ".diff.json"  # main.py writes <state-base>.diff.json
check("diff file written", os.path.exists(DIFF_F))
rc = main(["apply", "--diff", DIFF_F, "--state", STATE + ".new", "--page", SESS_F + ".page.md"])
check("CLI apply exit 0", rc == 0)
check("page written", os.path.exists(SESS_F + ".page.md"))
rc = main(["page", "--state", STATE + ".new"])
check("CLI page exit 0", rc == 0)

# ---------------------------------------------------------------- B
print("[B] exception / error paths")
try:
    distill(state0, SESS, min_count=0)
    check("min_count=0 rejected", False)
except ValueError:
    check("min_count=0 rejected", True)
fd2, BADSTATE = tempfile.mkstemp(suffix=".json")
os.write(fd2, b"{corrupt state")
os.close(fd2)
try:
    load_state(BADSTATE)
    check("corrupt state raises", False)
except MemoError:
    check("corrupt state raises", True)
fd3, SCHEMA = tempfile.mkstemp(suffix=".json"); os.close(fd3)
with open(SCHEMA, "w") as f:
    json.dump({"hello": []}, f)
try:
    load_state(SCHEMA)
    check("wrong schema state raises", False)
except MemoError:
    check("wrong schema state raises", True)
# dirty session: malformed lines skipped
dirty = json.dumps(SESS[0]) + "\nNOT JSON\n{\"role\": \"user\"}\n[1]\n"
msgs, warns = load_session(None, text=dirty)
check("dirty session tolerated", len(msgs) == 1 and len(warns) == 3)
# apply with bogus diff ids is a no-op, not a crash
st3 = apply_diff(copy.deepcopy(new_state), {"added": [{"id": "deadbeefdead", "text": "x", "section": "facts"}]})
check("bogus diff id no-op", all(b["status"] == "proposed" for b in st3["bullets"].values()))
rc = main(["distill", "--session", "no/such.jsonl", "--state", STATE])
check("CLI missing session exit 2", rc == 2)

# ---------------------------------------------------------------- C
print("[C] extreme edges")
empty_state, empty_diff = distill(state0, [])
check("empty session: no adds", empty_diff["added"] == [] and empty_state["bullets"] == {})
msgs_empty, warns_empty = load_session(None, text="")
check("empty session text loads", msgs_empty == [])
# 1-message session
s1, d1 = distill(state0, [msg("I prefer vim keybindings in every editor.", ts=9)])
check("single message distilled", len(d1["added"]) == 1)
# huge session: 5000 messages
import random
random.seed(7)
huge = []
for i in range(5000):
    t = random.choice([
        "I prefer deterministic builds over incremental chaos.",
        "We decided to use postgres for the audit log.",
        "The bug was an off-by-one in the pagination code.",
        "The weather is nice today, no signal here.",
    ])
    huge.append(msg(t, ts=i / 10))
t0 = time.perf_counter()
hs, hd = distill(state0, huge)
dt = time.perf_counter() - t0
check(f"5000-message distill < 3s (measured {dt*1000:.0f} ms)", dt < 3.0)
check("huge distill bounded bullets", len(hs["bullets"]) <= 4)
# 1MB single message
big = msg("We'll use kafka for event delivery. " * 20000)
sb, db_ = distill(state0, [big])
check("1MB message clipped to bullet cap",
      all(len(b["text"]) <= 200 for b in sb["bullets"].values()))
# corrupted binary session file
fd4, BIN = tempfile.mkstemp(suffix=".jsonl")
os.write(fd4, b"\xff\xfe\x00\x01 binary garbage")
os.close(fd4)
try:
    load_session(BIN)
    ok = False
except (UnicodeDecodeError, OSError):
    ok = True
check("binary session raises (documented)", ok)
os.unlink(BIN)
# reworded statement gets a NEW id (visible as separate bullet, not silent merge)
dA = bullet_id("We'll use sqlite for storage.")
dB = bullet_id("we'll use SQLite for the storage layer.")
check("rewording -> separate bullet ids", dA != dB)

for p in (SESS_F, STATE, STATE + ".new", DIFF_F, SESS_F + ".page.md", BADSTATE, SCHEMA):
    try:
        os.unlink(p)
    except OSError:
        pass

print(f"\nRESULT: PASS {PASS}/{PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
