## REVIEW PHASE OVERLAY (v18.1_TRW)

This overlay augments the shared core with review-specific content.

---

### PSR (Prompt Self-Review)

| Phase | Inputs | Outputs |
|-------|--------|---------|
| REVIEW | Run events, outcomes | What helped/hurt → `trw_reflect` + propose framework edits |

ORC MUST call `trw_reflect(run_path)` before `trw_phase_check("review")`.
`trw_phase_check` verifies reflection events exist in `events.jsonl` — gate warns without them.

---

### QOL CHANGES (Opportunistic Cleanup)

Shards MAY fix minor issues (<10 lines, already-open files, obviously correct, no behavior change, ≤5% effort). Separate commits. Log: `qol_fixes: [{file, change, lines_changed}]`. When in doubt → P2 TODO.

---

### Review Checklist

1. All findings from VALIDATE reviewed and addressed
2. Confidence levels assessed for all shards
3. Simplifications applied where possible
4. Reflection completed (`trw_reflect`)
5. Final report drafted (`reports/final.md`)
