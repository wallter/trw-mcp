# PRD-CORE-298 FR05 step 1 (T14): candidate acquisition by recall surface

One single-pass run of `benchmarks.engmem.scale` on 2026-09-23 on the dev Mac (arm64, macOS). It is not a multi-run proof; the operator rule for this sprint is single-pass engmem only.

- **Command:** `TRW_USER_DIR=<scratch> python -m benchmarks.engmem.scale --sizes 1000,5000,20000 --arms trw-framework,trw-daemon-tool,trw-fts-first,trw-fts-first+rerank --out scale-1000-5000-20000.json`, run from `trw-memory/`.
- **Code:** `REVISION` (62b39274b) plus the step 1 benchmark changes in the same commit as this file (the `trw-daemon-tool` arm, `--arms`, N, Wilson CIs and the paired McNemar test). No source file changed; nothing was replaced (PRD step 1).
- **Corpus:** EngMem-Synth seed 7, 16 queries per size, the same gold planted at every size. Only the distractor count grows.
- **Wall clock:** 202 s end to end; peak RSS 2.7 GB.

## Result

complete@10 per arm, with N=16 and Wilson 95% CIs. The McNemar column compares each arm with `trw-framework` over the same 16 queries: queries only the framework arm completed / queries only this arm completed, and the exact two-sided p.

| size | arm | complete@10 | 95% CI | forbidden@10 | p50 ms | p95 ms | McNemar vs framework |
|---|---|---|---|---|---|---|---|
| 1k | trw-framework | 56.2% | 33.2–76.9 | 0 | 68.6 | 81.8 | — |
| 1k | trw-daemon-tool | 100.0% | 80.6–100 | 0 | 184.3 | 3344.2 | 0 / 7, p=0.016 |
| 1k | trw-fts-first | 75.0% | 50.5–89.8 | 0 | 26.7 | 31.9 | 0 / 3, p=0.25 |
| 1k | trw-fts-first+rerank | 100.0% | 80.6–100 | 0 | 69.6 | 95.7 | 0 / 7, p=0.016 |
| 5k | trw-framework | 0.0% | 0–19.4 | 0 | 53.1 | 85.7 | — |
| 5k | trw-daemon-tool | 0.0% | 0–19.4 | 0 | 316.6 | 449.6 | 0 / 0, p=1 |
| 5k | trw-fts-first | 56.2% | 33.2–76.9 | 0 | 68.7 | 141.8 | 0 / 9, p=0.004 |
| 5k | trw-fts-first+rerank | 56.2% | 33.2–76.9 | 0 | 114.6 | 311.7 | 0 / 9, p=0.004 |
| 20k | trw-framework | 0.0% | 0–19.4 | 0 | 69.0 | 105.9 | — |
| 20k | trw-daemon-tool | 0.0% | 0–19.4 | 0 | 864.6 | 1107.2 | 0 / 0, p=1 |
| 20k | trw-fts-first | 62.5% | 38.6–81.5 | 0 | 99.4 | 124.3 | 0 / 10, p=0.002 |
| 20k | trw-fts-first+rerank | 62.5% | 38.6–81.5 | 0 | 144.3 | 542.4 | 0 / 10, p=0.002 |

forbidden@10 was zero for every arm at every size (95% CI upper bound 19.4% at N=16).

## Reading it

- **The daemon tool loses every gold row once the store outgrows its recency pool.** It completes 16/16 at 1k (the pool, together with the tier supplement, covers the corpus) and 0/16 at 5k and 20k. Its p50 grows from 184 ms to 865 ms with store size, against about 70 ms for the in-process arms. The tool also opens a backend per call and runs canary and tier work that the arms skip. That growth has not been profiled. The 3.3 s p95 at 1k is the first call's cold setup.
- **Query-driven candidates hold at every size.** trw-fts-first is better than the framework arm at 5k and 20k (p ≤ 0.004, 9–10 discordant queries, all in its favour). At 1k the rerank variant is needed to reach 16/16. At 5k and 20k rerank adds latency (p95 312–542 ms) and completes no extra query.
- **N is small.** 16 queries give wide intervals. The 5k/20k differences have disjoint CIs; the 1k fts-first vs framework difference does not (p=0.25).

## Caveats

- **`trw-framework` is the pre-CORE-292 MCP path.** `TrwFrameworkArm` reads only the recency pool (`list_entries`). Since PRD-CORE-292, trw-mcp's `trw_recall` takes candidates from `recall_policy.acquire_candidates`, which adds the full-text leg. This arm therefore measures what the framework shipped before CORE-292, not today. The MCP path as shipped is measured by `trw-mcp/benchmarks/engmem_mcp.py` (baselines `core292-pre`/`core292-post`). Step 2's per-query comparison needs an arm that uses `acquire_candidates`, or it has to be read against engmem_mcp.
- **The graph drain close timed out at 5k and 20k** ("timed out waiting for background graph updates"). The scale script records this as a scale result (`_run.close_failed`). Every query was scored before the close, so the table is complete.
- **One Hugging Face Hub warning appeared** ("unauthenticated requests") while the models loaded at 1k. Both models were already in the local cache.

## Step 2: the daemon tool takes `acquire_candidates`

One single-pass rerun on 2026-09-23 of `--arms trw-framework,trw-daemon-tool`, same corpus and machine, written to `scale-step2-1000-5000-20000.json`. Code: the step 2 commit, which also moves `TrwFrameworkArm` to today's MCP path (`acquire_candidates`, `resolve_query`, `hybrid_policy` with `importance_alpha=1.0`). The step 1 framework rows above are therefore the pre-CORE-292 path, and these are today's.

| size | arm | complete@10 | 95% CI | forbidden@10 | p50 ms | p95 ms | McNemar vs framework |
|---|---|---|---|---|---|---|---|
| 1k | trw-framework | 100.0% | 80.6–100 | 0 | 165.9 | 3255.7 | — |
| 1k | trw-daemon-tool | 100.0% | 80.6–100 | 0 | 148.9 | 163.5 | 0 / 0, p=1 |
| 5k | trw-framework | 87.5% | 64.0–96.5 | 0 | 261.8 | 471.1 | — |
| 5k | trw-daemon-tool | 62.5% | 38.6–81.5 | 0 | 435.1 | 635.5 | 4 / 0, p=0.125 |
| 20k | trw-framework | 87.5% | 64.0–96.5 | 0 | 258.4 | 297.6 | — |
| 20k | trw-daemon-tool | 87.5% | 64.0–96.5 | 0 | 1049.0 | 1708.2 | 0 / 0, p=1 |

- **The T14 defect is fixed.** The daemon tool goes from 0/16 to 10/16 at 5k and from 0/16 to 14/16 at 20k. It still completes 16/16 at 1k, and forbidden@10 stays zero.
- **The per-query match criterion is not met at 5k.** The framework arm completes 4 queries the daemon tool does not (and the reverse is 0), p=0.125. Both arms now take the same candidates, so the difference is ranking. The tool ranks with `build_scored_candidates` and `rank_by_utility`, not `hybrid_policy` (rerank, fusion mode, adaptive floor). Converging the ranking is the remaining work for "the surfaces agree".
- **The daemon tool is the slowest surface and grows with the store** (p50 1049 ms at 20k against 258 ms). This has not been profiled; per-call backend open, canary and tier work are the candidates.
- The first framework call at 1k pays the reranker load (p95 3.3 s). The graph drain close timed out at 5k and 20k again; all queries were scored first.


## Ranking convergence: one ranking for `trw_recall` and the daemon tool

One single-pass rerun on 2026-09-23 of `--arms trw-framework,trw-daemon-tool,trw-query-pool`, same corpus and machine, written to `scale-ranking-1000-5000-20000.json` (246 s, peak RSS 2.8 GB). Code: the ranking-convergence commit.
- **Ranking:** trw-mcp's `trw_recall` and the daemon's `memory_recall` both rank with `recall_policy.ranking_arguments()`, and a query keeps the pipeline's order.
- **Tag filter:** the full-text leg honours `tags`.
- **New arm:** `trw-query-pool` is the framework arm with query-driven candidates and nothing else changed (the step 3 comparison).

| size | arm | complete@10 | 95% CI | forbidden@10 | p50 ms | p95 ms | p50 / framework | McNemar vs framework |
|---|---|---|---|---|---|---|---|---|
| 1k | trw-framework | 100.0% | 80.6–100.0 | 0 | 216.6 | 3514.9 | 1.00 | — |
| 1k | trw-daemon-tool | 100.0% | 80.6–100.0 | 0 | 225.9 | 242.5 | 1.04 | 0 / 0, p=1 |
| 1k | trw-query-pool | 100.0% | 80.6–100.0 | 0 | 81.6 | 105.6 | 0.38 | 0 / 0, p=1 |
| 5k | trw-framework | 87.5% | 64.0–96.5 | 0 | 303.6 | 576.9 | 1.00 | — |
| 5k | trw-daemon-tool | 87.5% | 64.0–96.5 | 0 | 516.3 | 613.8 | 1.70 | 0 / 0, p=1 |
| 5k | trw-query-pool | 87.5% | 64.0–96.5 | 0 | 198.5 | 272.5 | 0.65 | 0 / 0, p=1 |
| 20k | trw-framework | 87.5% | 64.0–96.5 | 0 | 306.9 | 661.6 | 1.00 | — |
| 20k | trw-daemon-tool | 87.5% | 64.0–96.5 | 0 | 950.5 | 1291.9 | 3.10 | 0 / 0, p=1 |
| 20k | trw-query-pool | 81.2% | 57.0–93.4 | 0 | 194.3 | 534.5 | 0.63 | 1 / 0, p=1 |

- **The acceptance criterion is met.** complete@10 for the daemon tool and the framework agree on every query at every size (0 discordant queries either way, N=16 per size). forbidden@10 is zero for every arm.
- **Ranking did not cause the latency; opening the store does.** The p50 ratio is 1.04, 1.70 and 3.10 at 1k, 5k and 20k. One daemon-tool recall on the 20k store was profiled (cProfile, three warm calls, 1,172 ms each):
  - **Store opens: about 650 ms.** `open_and_configure` runs `PRAGMA quick_check` on every open, which costs about 320 ms at this size and grows with the file. The tool opens the store twice per call: its own backend, and again for org-memory discovery (`_org_memory_results` → `discover_namespace_backends`).
  - **Tier warm search:** about 120 ms.
  - **Ranking:** about 220 ms, the same work the framework arm does.
- **No candidate-pool gap remains, so step 3 is not warranted on quality.** The query-driven pool completes the same queries as the framework at 1k and 5k, and one fewer at 20k (1 / 0, p=1). It is faster: p50 is 0.38 to 0.65 of the framework's, because it ranks a smaller pool. By the PRD's step 3 condition this is closed with this measurement as its record. A latency-motivated switch would be a separate decision.
- The first framework call at 1k pays the reranker load (p95 3.5 s). The graph drain close timed out again at 5k and 20k; all queries were scored first.

## One ranking depth, source admission only, quick_check once per store

Codex round 2 on 4c8ad129c raised two output-parity issues, and this change fixes both:
- **Ranking depth:** the daemon tool now ranks to `trw_recall`'s depth (`limit * RECALL_PREFETCH_MULTIPLIER`) and caps last.
- **Source policy:** it admits rows by source without re-sorting by source weight.

The slice also carries the latency fix. `PRAGMA quick_check` runs once per process per store file, and org-memory discovery reuses the open backend.

Single pass at 20k on 2026-09-23, machine load about 10 throughout (the same range as the NFR01 runs):

| surface | measured by | complete@10 | p50 ms | p95 ms | before |
|---|---|---|---|---|---|
| trw_recall (real MCP path) | `trw-mcp/benchmarks/engmem_mcp.py --entry recall` → `mcp-recall-quickcheck-20000.json` | 87.5% | 333.4 | 632.9 | NFR01 candidate 450 p50 / 670 p95; pre-CORE-292 312 / 420 |
| trw-framework arm | `scale --arms trw-framework,trw-daemon-tool` → `scale-quickcheck-20000.json` | 87.5% | 220.7 | 3577.7 | 306.9 / 661.6 |
| trw-daemon-tool | same run | 87.5% | 312.8 | 529.9 | 950.5 / 1291.9 |

- **Daemon tool p50 went from 950 ms to 313 ms,** now 1.42× the framework arm (it was 3.10×). The remaining gap is work the arm does not do: a backend opened per call, canaries and the tier warm search.
- **Per-query parity holds:** the daemon tool and the framework arm complete the same queries (0 / 0 discordant).
- **`trw_recall` p50 is 333 ms at 20k,** against 450 ms for the NFR01 candidate and 312 ms before CORE-292. Each figure is one pass, so this narrows the recall slowdown we logged but does not prove it is closed.
- The framework arm's p95 is its first call loading the reranker.
