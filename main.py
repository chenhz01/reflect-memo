#!/usr/bin/env python3
"""reflect-memo: a reflection-LAYER-ONLY memory plugin.

Periodically distills session transcripts into a "mental model" page and
outputs an auditable diff. No recall, no embeddings, no vector store — the
entire job is: sessions in -> high-signal bullets -> human-reviewable diff ->
(only after explicit approval) an updated page.

Input: session JSONL, one JSON object per line:
  {"ts": 1781000000.0, "role": "user"|"assistant"|"system", "text": "..."}

Extraction rules (deterministic, rule-based):
  - preference : "I prefer/always/never/like/hate ..."
  - decision   : "decided/let's use/we'll use/switch to/chose/go with ..."
  - fact       : "actually/the bug is/the reason is/turns out ..."
Bullets are deduplicated by normalized text; each has a stable 12-hex id
(sha1 of normalized text) so diffs are auditable across runs.

CLI:
  python main.py distill --session s.jsonl --state state.json [--min-count 1]
  python main.py apply   --diff diff.json --state state.json --page page.md
  python main.py page    --state state.json
  python main.py version

Distill writes `state.json.new` + `diff.json` + `page.md.new`; nothing is
committed until you run `apply` (the human gate). Pure stdlib.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

FORMAT_VERSION = 1
SECTIONS = ("preferences", "decisions", "facts")

RULES = [
    (re.compile(r"\b(i|i'm|we|i'd)\s+(prefer|always|never|hate|like|love|want)\b", re.I), "preferences"),
    (re.compile(r"\b(always|never)\s+(use|put|write|run|test)\b", re.I), "preferences"),
    (re.compile(r"\b(decided|let's|we'll|chose|choose|go with|switch(ed)? to|use)\s+\S+", re.I), "decisions"),
    (re.compile(r"\b(the bug|the issue|the reason|turns out|actually|root cause)\b", re.I), "facts"),
]

MAX_BULLET_LEN = 200
MIN_SENTENCE_LEN = 15


class MemoError(ValueError):
    pass


# ---------------------------------------------------------------- ids
def bullet_id(text: str) -> str:
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _classify(text: str) -> str | None:
    for rx, section in RULES:
        if rx.search(text):
            return section
    return None


def _clip(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip())
    if len(t) > MAX_BULLET_LEN:
        t = t[:MAX_BULLET_LEN - 1] + "…"
    return t


# ---------------------------------------------------------------- session parsing
def load_session(path: str | None = None, text: str | None = None) -> tuple[list[dict], list[str]]:
    """Dirty-tolerant JSONL parse; returns (messages, warnings)."""
    raw = text
    if raw is None:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    msgs, warnings = [], []
    for i, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError as e:
            warnings.append(f"line {i}: skipped malformed JSON ({e})")
            continue
        if not isinstance(m, dict) or not isinstance(m.get("text"), str) or "role" not in m:
            warnings.append(f"line {i}: skipped, need object with 'role' and 'text'")
            continue
        m.setdefault("ts", time.time())
        msgs.append(m)
    return msgs, warnings


# ---------------------------------------------------------------- distill
def extract_candidates(msgs: list[dict]) -> dict[str, dict]:
    """sentence -> {section, count, ts_first, ts_last}"""
    cands: dict[str, dict] = {}
    for m in msgs:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", m["text"]):
            sent = sent.strip()
            if len(sent) < MIN_SENTENCE_LEN:
                continue
            section = _classify(sent)
            if section is None:
                continue
            clipped = _clip(sent)
            bid = bullet_id(clipped)
            c = cands.setdefault(bid, {"id": bid, "text": clipped, "section": section,
                                       "count": 0, "ts_first": m["ts"], "ts_last": m["ts"]})
            c["count"] += 1
            c["ts_last"] = max(c["ts_last"], m["ts"])
    return cands


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": FORMAT_VERSION, "bullets": {}, "distills": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise MemoError(f"state file unreadable: {e}") from None
    if not isinstance(state, dict) or "bullets" not in state or \
            not isinstance(state["bullets"], dict):
        raise MemoError("state file has wrong schema (need object with 'bullets' map)")
    return state


def _jaccard(a: str, b: str) -> float:
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def distill(state: dict, msgs: list[dict], min_count: int = 1) -> tuple[dict, dict]:
    """Propose an updated bullet store. Returns (new_state, diff).

    Rules:
      - candidates with count >= min_count are proposed (status stays
        'proposed' until `apply` promotes them);
      - active bullets not re-observed are KEPT (memory does not silently
        forget within one distill; staleness is visible in the page instead);
      - candidates that overlap >=0.55 Jaccard with an active bullet in the
        same section are flagged `possibly_reworded` (round-2: a reworded
        statement must not silently split the memory — a human decides);
      - the diff records adds / count-bumps so a human can audit.
    """
    if min_count < 1:
        raise ValueError("min_count must be >= 1")
    cands = extract_candidates(msgs)
    bullets = {k: dict(v) for k, v in state["bullets"].items()}
    added, bumped, possibly_reworded = [], [], []
    for bid, c in cands.items():
        if c["count"] < min_count:
            continue
        if bid in bullets:
            b = bullets[bid]
            # re-observed at all -> confirm ("still true as of distill N")
            if c["ts_last"] >= b.get("ts_last", 0):
                b["last_count"] = max(b.get("last_count", 0), c["count"])
                b["ts_last"] = c["ts_last"]
                bumped.append({"id": bid, "count": c["count"]})
        else:
            bullets[bid] = {"id": bid, "text": c["text"], "section": c["section"],
                            "status": "proposed", "first_seen_ts": c["ts_first"],
                            "ts_last": c["ts_last"], "last_count": c["count"]}
            added.append({"id": bid, "text": c["text"], "section": c["section"],
                          "count": c["count"]})
            for ab in bullets.values():  # round-2: rewording detector
                if ab["status"] == "active" and ab["section"] == c["section"] \
                        and ab["id"] != bid and _jaccard(ab["text"], c["text"]) >= 0.55:
                    possibly_reworded.append({"new_id": bid, "active_id": ab["id"],
                                              "overlap": round(_jaccard(ab["text"], c["text"]), 2)})
                    break
    new_state = {"version": FORMAT_VERSION, "bullets": bullets,
                 "distills": state.get("distills", 0) + 1}
    diff = {"added": added, "bumped": bumped,
            "possibly_reworded": possibly_reworded,
            "kept": len([b for b in bullets.values() if b["status"] == "active"]),
            "distill_no": new_state["distills"]}
    return new_state, diff


# ---------------------------------------------------------------- page
def render_page(state: dict) -> str:
    """Mental model page: only ACTIVE bullets, grouped by section."""
    lines = ["# Mental Model Page", "",
             f"_distills applied: {state.get('distills', 0)} · "
             f"active bullets: {sum(1 for b in state['bullets'].values() if b['status'] == 'active')}_",
             ""]
    for section in SECTIONS:
        rows = sorted((b for b in state["bullets"].values()
                       if b["status"] == "active" and b["section"] == section),
                      key=lambda b: -b.get("last_count", 0))
        lines.append(f"## {section.title()}")
        if not rows:
            lines.append("_(none)_")
        for b in rows:
            lines.append(f"- [{b['id']}] {b['text']} (observed x{b.get('last_count', 1)})")
        lines.append("")
    return "\n".join(lines)


def apply_diff(state: dict, diff: dict, accept_ids: set[str] | None = None) -> dict:
    """Human gate: promote proposed bullets listed in the diff to active.

    accept_ids=None accepts everything in the diff; otherwise only the given
    ids are promoted (partial acceptance is the auditable path).
    """
    bullets = state["bullets"]
    promoted = 0
    for a in diff.get("added", []):
        bid = a["id"]
        if bid in bullets and bullets[bid]["status"] == "proposed" and \
           (accept_ids is None or bid in accept_ids):
            bullets[bid]["status"] = "active"
            promoted += 1
    state["distills"] = diff.get("distill_no", state.get("distills", 0) + 1)
    state["applied_diffs"] = state.get("applied_diffs", []) + [{
        "ts": time.time(), "promoted": promoted, "accepted_all": accept_ids is None}]
    return state


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reflect-memo")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("distill")
    sp.add_argument("--session", required=True)
    sp.add_argument("--state", required=True)
    sp.add_argument("--min-count", type=int, default=1)
    sp = sub.add_parser("apply")
    sp.add_argument("--diff", required=True)
    sp.add_argument("--state", required=True)
    sp.add_argument("--page", required=True)
    sp.add_argument("--accept", default=None, help="comma-separated bullet ids to accept (default: all)")
    sp = sub.add_parser("page")
    sp.add_argument("--state", required=True)
    sub.add_parser("version")

    a = p.parse_args(argv)
    try:
        if a.cmd == "version":
            print("reflect-memo 0.1.1")
            return 0
        if a.cmd == "distill":
            state = load_state(a.state)
            msgs, warnings = load_session(a.session)
            for w in warnings:
                print(f"WARN: {w}", file=sys.stderr)
            new_state, diff = distill(state, msgs, min_count=a.min_count)
            base = os.path.splitext(a.state)[0]
            with open(a.state + ".new", "w", encoding="utf-8") as f:
                json.dump(new_state, f, indent=1)
            with open(base + ".diff.json", "w", encoding="utf-8") as f:
                json.dump(diff, f, indent=1)
            print(f"proposed {len(diff['added'])} new, {len(diff['bumped'])} bumped, "
                  f"{diff['kept']} kept active. Review {base}.diff.json, then:\n"
                  f"  python main.py apply --diff {base}.diff.json --state {a.state}.new --page {base}.page.md")
            return 0
        if a.cmd == "apply":
            with open(a.diff, "r", encoding="utf-8") as f:
                diff = json.load(f)
            state = load_state(a.state)
            accept = set(a.accept.split(",")) if a.accept else None
            state = apply_diff(state, diff, accept_ids=accept)
            with open(a.state, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=1)
            with open(a.page, "w", encoding="utf-8") as f:
                f.write(render_page(state))
            print(f"applied: page written to {a.page}")
            return 0
        if a.cmd == "page":
            print(render_page(load_state(a.state)))
            return 0
    except (MemoError, ValueError, OSError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
