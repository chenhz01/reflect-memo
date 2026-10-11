A reflection-layer-only memory plugin. It periodically distills agent session transcripts into a "mental model" page — preferences, decisions, facts — and outputs an auditable diff; nothing enters memory until a human approves the diff. No embeddings, no vector store, no recall: just deterministic extraction, stable bullet IDs, and a review gate.

You need this if your agent forgets decisions across sessions and you distrust opaque auto-memory. You don't if you want similarity search over history — that's a different layer.

<p align="center"><img src="docs/demo.gif" alt="demo" width="720"></p>

## Problem

Agent memory systems compete on recall quality; the "reflect" operation is the quiet differentiator (vectorize-io/hindsight built its benchmark-winning memory around retain/recall/reflect and mental-model pages). The overlooked angle: **memory writes are the governance surface**. Whatever writes memory controls future agent behavior, yet most systems auto-write with no human audit trail — then everyone acts surprised when a stale wrong "fact" poisons every downstream session. Restated: memory should be *learned in public*, with diffs a human can reject.

Confidence: **High (≥80%)** that the extract→diff→gate loop is deterministic and auditable (fully tested); **Medium (60-79%)** that keyword-rule extraction catches enough high-signal statements to be worth running — it is deliberately recall-loose and precision-gated by the human.

## How it works

1. **Input**: session JSONL — `{"ts", "role", "text"}` per line.
2. **Distill**: sentence-level rule match (preference / decision / fact patterns), normalize, clip, dedupe by `sha1(normalized text)` → stable 12-hex bullet IDs. Re-observed statements get confirmed (count/last-seen bumped).
3. **Rewording guard** (round-2): a new candidate overlapping ≥0.55 Jaccard with an active bullet in the same section is flagged `possibly_reworded` in the diff — a reworded decision must not silently fork your memory.
4. **Diff**: `added / bumped / possibly_reworded / kept` — the auditable artifact. Bullet IDs make the diff stable across runs and machines.
5. **Human gate**: `apply --diff ... [--accept id1,id2]` promotes only what you approve; partial acceptance is first-class. Only then is the mental model page rewritten.

## Quickstart (~5 min)

```bash
# 1. self-test (~5 s)
python self_test.py

# 2. distill a session (~1 min)
cat > session.jsonl <<'EOF'
{"ts":1,"role":"user","text":"I prefer small pull requests. We'll use sqlite for storage."}
{"ts":2,"role":"assistant","text":"The bug was a timezone offset in the cron parser."}
EOF
python main.py distill --session session.jsonl --state state.json

# 3. review the diff, then apply (~2 min)
cat state.diff.json
python main.py apply --diff state.diff.json --state state.json.new --page page.md
python main.py page --state state.json

# 4. schedule it (~1 min): run distill after each N sessions; apply is always manual
```

## Why not X

| Alternative | What it does | Why not enough |
|---|---|---|
| Auto-memory (writes to AGENTS.md / memory banks directly) | Agent updates its own memory mid-session | No human gate; a bad write propagates silently; hard to audit what changed and why |
| Vector memory stores (hindsight-style full systems) | Embed + retrieve + reflect, benchmark-tuned | You adopt the whole engine (server, embeddings, banks) to get the reflect layer; heavy when you only want the learning loop |
| Raw transcript grep | Search the log when needed | No abstraction: the same decision stated five ways is five facts; nothing accumulates |
| Summarize-transcript-into-prompt | LLM condenses history each time | Non-deterministic, un-auditable, re-costs every session, and quietly rewrites what it "remembers" |

## Minimal Fallback (PLAN B)

No Python: a hand-maintained `MEMORY.md` with three headings (Preferences / Decisions / Facts) and one rule — *append a dated line per new belief; never edit old lines in place; delete only via a weekly review*. You lose stable IDs and automated diffing but keep the two things that matter: stable statements and a human gate.

## Design Trade-offs

Priority order: **auditability > stability > recall.**

- **Rules, not an LLM, do extraction.** Deterministic, free, offline, testable — at the cost of missing paraphrases. The human gate backstops precision; recall is honest about being Medium confidence.
- **Propose/apply split.** Distill never mutates the active memory; `apply` is the only promotion path. Costs a two-command workflow; buys a governance boundary.
- **Bullet ID = content hash.** Rewording *should* fork the ID — with the `possibly_reworded` flag it becomes a visible human decision instead of silent drift. Semantic-ID schemes would hide real changes.
- **No forgetting in v0.1.** Bullets never auto-expire; staleness is visible via observed-counts. Auto-forgetting without audit is how memory rots quietly.

## Limits & Second-order Effects

- **Keyword rules miss paraphrase** ("we're going with" matches; "our choice: " doesn't). Expect Medium recall — the diff is a floor, not a ceiling.
- **English-pattern biased.** The regexes are English; other languages need their own rules (add to `RULES`).
- **Session format is ours.** Your harness must emit role/text JSONL; a converter is out of scope for v0.1.
- **Second-order effect 1 — review fatigue → rubber-stamping:** if distills are frequent, humans will start accepting every diff blindly, recreating auto-memory with extra steps. Schedule distills at reflection cadence (weekly), not per session.
- **Second-order effect 2 — memory weaponization:** whoever controls session content controls proposed memories; a prompt-injected transcript can propose a malicious "decision". The gate protects you only if you actually read the diff.
- **Second-order effect 3 — frozen past:** no auto-forgetting means wrong-but-approved bullets persist until manually removed. Pair with the weekly review; staleness is visible, not enforced.

## FAQ

**Q: Does it use an LLM to summarize?**
No. Zero LLM calls, zero network. Extraction is regex rules; determinism is the feature.

**Q: How is this different from hindsight/Claude-memory?**
Scope: this is *only* the reflect layer with a human gate — a component you can bolt onto any agent that emits transcripts, not a memory platform.

**Q: Can I accept only some changes in a diff?**
Yes: `apply --accept id1,id2` promotes only those IDs; the rest stay proposed for a later pass.

**Q: When NOT to use this tool?** *(ask this first)*
- You want semantic recall ("what did we say about auth?") — this layer never retrieves; pair it with search or embeddings elsewhere.
- Your sessions are mostly code, not natural language — the extraction rules target spoken-style statements.
- You need memory that updates in real time mid-session — the propose/apply gate is deliberately slower than the loop.
- You won't actually review diffs — then the gate is theater; use honest auto-memory instead and skip the ceremony.

**Q: Why are reworded statements separate bullets instead of merged?**
Because "which wording is right?" is a human judgment. The `possibly_reworded` flag surfaces the fork; merging automatically would silently pick a winner.

## [Core Assumptions]

1. High-signal memory statements are detectable by surface patterns (prefer/decide/the bug is/…) often enough to be useful.
2. A human will actually review diffs; the governance claim collapses without that.
3. Sessions can be exported as role/text JSONL with timestamps.
4. A bullet's meaning is stable as long as its text is — content-hash IDs are a feature, not a bug.
5. Memory quality > memory quantity: fewer, human-approved statements beat exhaustive auto-captured ones.

---
Contact: hcac4735@agent.qq.com · License: MIT · Measured: 5000-message distill ≈110 ms (repro: `python self_test.py`). Extraction recall/precision figures are qualitative [estimated]; no benchmark numbers claimed. As of 2026-10-11.
