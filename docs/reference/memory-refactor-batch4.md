# Memory refactor — Batch 4: morphology and retrieval decision

## Decision: retain production stemmer unchanged

The CI benchmark compares four lexical representations against the **same**
Memory BM25/ranking/metadata pipeline and frozen synthetic fixture. No production
dependency or ranking change is justified by these measurements.

Measured on GitHub Actions run [42678163173](https://github.com/conorod1992/extended_openai_conversation/actions/runs/42678163173)
(Home Assistant stable job; [corrected four-way artifact](https://github.com/conorod1992/extended_openai_conversation/actions/runs/42678163173/artifacts/13702041710)), 38 records, 36 queries and 33 relevant labels:

| Variant | Recall@1 | Recall@5 | Hard negatives @3 | Warm p95 (ms) | Cold p95 (ms) | CPU (ms) | Event-loop gap (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Exact terms | 0.9394 | 0.9697 | 17 | 29.9496 | 119.9443 | 518.3849 | 120.4533 |
| Current custom | 0.9697 | **1.0** | **15** | **30.6527** | **32.7313** | **416.8116** | **8.9580** |
| Snowball/Porter2 | 0.9697 | **1.0** | **15** | 32.8670 | 175.3583 | 635.1589 | 16.0207 |
| Exact + Snowball prototype | 0.9697 | 1.0 | 17 | 85.5026 | 206.0648 | 1240.4598 | 10.2654 |

- Current and Snowball rankings are *identical* for all 36 fixture queries.
  Neither improves on the other's relevance labels, despite Snowball's added
  complexity and observed CPU cost.
- Exact-only misses `ret-034` (`fall` versus `falling`). The *initial* hybrid
  benchmark also missed it due to a **benchmark bug**: it omitted Snowball
  features for unchanged base forms. Those initial hybrid results were
  invalidated. The **corrected hybrid** now retrieves this match, but compared
  with the current stemmer it promotes two additional hard negatives into the
  top three, changes seven of 36 rankings, and has roughly 2.8 times the warm
  p95 latency on this run. It is only an uncalibrated prototype, not a
  deployable hybrid relevance policy.
- Exact-only, current and Snowball each include 17 hard-negative hits in the
  top ten; corrected hybrid includes **18**. A lexical candidate may be
  incidental even when the correct target is ranked first. **Retrieval
  similarity is never mutation authorization.**
- This is a **single-process, fixed-order** run, not a statistical performance
  study. Per-variant first-query cold times and peak RSS reflect shared Python
  initialization and prior variant caches. CPU, RSS and event-loop numbers are
  diagnostic, not cleanly isolated measurements. Worker-thread responsiveness
  is preserved by production ranking; no meaningful performance win for a new
  stemmer was established.

## What the prototype measures

`scripts/benchmark_memory_stemmers.py` evaluates:

1. Exact tokens without stemming (keeping current compound/CJK expansion).
2. The current conservative English stemmer.
3. Snowball English via pinned, benchmark-only `snowballstemmer==3.1.1`.
4. Exact tokens plus independently prefixed Snowball features.

The prototype invokes the real asynchronous Memory search and snapshot ranker,
with bounded feature caches cleared between modes. Snowball objects are created
per thread to avoid sharing mutable instances across the worker pool.
`snowballstemmer==3.1.1` has a pure-Python wheel and is installed only inside
the non-gating comparison step; **it is not in the integration manifest**.
No vector embedding provider is invoked.

The fixture includes exact identifiers, C++, C#, C, versioned models,
case-distinct paths, short tokens, negation, swapped relations, CJK, Cyrillic,
mixed English morphology and scope isolation. The current Memory tokenizer
casefolds paths, so these rankings **do not prove case-sensitive path
discrimination**. Neither do these synthetic labels certify the best
real-user retrieval policy. These are limitations, not passed acceptance tests.

## Archive, Knowledge and Management

Memory and Conversation Archive share the custom stemming function, but
Archive has its own special five-character `-ing` rule, relevance scoring and
excerpt offsets. Replacing the shared function would silently change Archive
ranking and excerpt matching; therefore no common production tokenizer switch
was made. Knowledge uses a different source/chunk index, and Management uses
substring browsing. Neither was changed as part of the morphology comparison.
Separate follow-up fixes on the same PR add lower-weight word-form evidence
to Knowledge's existing exact index; they leave the shared stemmer and
Management substring browsing unchanged.

## What would justify reconsideration

A separate, independently judged corpus with multiple languages, compound
corrections, exact identifiers, historical records and production-like sizes;
a recalibrated weighted-exact relevance scheme; direct Archive differential
rank/excerpt tests; cold and warm isolated per-process benchmarks in randomized
order; memory/allocation and event-loop checks; and hybrid embedding
fallback validation. Any gain must outweigh the maintenance/dependency cost
without decreasing identifier precision or scope integrity.

The existing thresholds (`MIN_LEXICAL_RELEVANCE_SCORE` and hybrid weights)
are unchanged. No historical Memory rewrites, storage-format changes, or
Knowledge indexing migrations were made.
