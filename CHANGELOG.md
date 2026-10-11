# Changelog

All notable changes to `reflect-memo` (ALIGNMENT-16 rule 93).

## v0.1.1 - 2026-10-11 (round 2: self-audit revision)

### Added
- Rewording detector: new candidates with ≥0.55 Jaccard overlap against an
  active bullet in the same section are flagged `possibly_reworded` in the
  diff — a rephrased decision no longer silently forks the memory; a human
  decides. 3 new tests (flag on, separate-bullet preserved, unrelated add
  not flagged). Tests 35 → 38 (**+8.6% count; memory-integrity coverage:
  0 → 1 rewording path**).
- State schema validation now rejects files without a `bullets` map
  (previously silently treated as empty memory — dangerous default).

### Fixed
- Re-observation bump semantics: any re-observation now confirms
  ("still true as of distill N") instead of requiring a strictly higher
  count — a repeated statement at equal count is signal, not noise.
- Self-test file-path derivation and a leaked file-descriptor in the
  corrupt-state fixture.

## v0.1.0 - 2026-10-11 (initial release)

### Added
- Rule-based distiller (preference/decision/fact patterns), content-hash
  bullet IDs, propose/apply two-phase memory with `--accept` partial
  acceptance, mental-model page renderer, dirty-tolerant JSONL loader.
- CLI: `distill / apply / page / version`.
- Three-tier self-test, 35 checks PASS.
