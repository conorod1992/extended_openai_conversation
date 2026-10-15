# Archive search performance and remaining limits

Archive search uses bounded ranking and a weakly owned feature cache. It still
scans retained turns. The performance part of audit finding 4 is **mitigated**,
not eliminated by a persistent search index.

## What the tuning changes

A profile of the original 10,000-turn repeated-text probe showed 1.6 million
stemming calls. Normalization, lexical expansion and stemming repeatedly processed
words that produce the same set of search terms.

The tuned implementation normalizes each turn once, expands and stems distinct
words, and preserves possessives, compound components, symbolic terms and CJK
run boundaries. ASCII turns skip the CJK-only scan. When the cache cannot admit a
record, it skips avoidable admission accounting and closure/lock work. It rechecks
capacity under the cache lock before inserting, so concurrent searches cannot
exceed the cache's configured budget.

No transcript format, retention policy, ranking weights or search-result limits
change. There is no silent scan cutoff and no new persistent copy of private text.

## Reproducible measurements

Measured on Windows 11, CPython 3.14.2. These are deterministic **generated**
conversations, not a sample of a user's private archive or a Home Assistant server
performance guarantee. Six workloads cover 1,000 to 50,000 turns, short requests,
varied household replies, distinct technical identifiers, occasional long logs,
and the original repeated-text regression. They contain about 79,000 to
15.8 million text characters.

Each workload runs a common term, a rare term occurring in 1% of turns, and a
missing term, with `limit=1`. The script checks complete result equality across
all implementations and cold/warm phases. Five repetitions rotate implementation
order; the table reports median elapsed CPU-path wall times for the common term.
Cold means the feature cache is cleared; warm immediately repeats the query.
Disk I/O, archive loading, provider requests and Management rendering are excluded.

| Generated workload | Turns | Original cold | Before tuning cold | Tuned cold | Tuned warm |
| --- | ---: | ---: | ---: | ---: | ---: |
| Short household exchanges | 1,000 | 6.90 ms | 9.82 ms | 6.95 ms | 0.66 ms |
| Mixed household discussions | 10,000 | 261.46 ms | 326.32 ms | 183.06 ms | 133.13 ms |
| Distinct technical identifiers | 10,000 | 579.89 ms | 720.40 ms | 471.99 ms | 435.72 ms |
| Occasional long maintenance logs | 1,000 | 125.05 ms | 145.97 ms | 59.50 ms | 1.15 ms |
| Original repeated-text regression | 10,000 | 805.59 ms | 930.22 ms | 353.41 ms | 267.48 ms |
| Short household exchanges | 50,000 | 347.44 ms | 478.86 ms | 281.82 ms | 254.25 ms |

The original version is commit `fac6deda`; the PR before tuning is `53bff80`.
All 18 workload/query combinations returned identical results across versions.
On the short 1,000-turn workload, cold time is effectively equal to the original;
the other measured cold workloads improve. The original repeated-text regression
is removed rather than offset only by a warm-cache gain. These measurements do
not imply every possible cold archive will be faster.

Run from the repository root with the integration's Python dependencies installed:

```sh
python scripts/benchmark_archive_search.py --baseline-ref fac6deda --comparison-ref 53bff80 --repeats 5 --output archive-search-results.json
```

The Git revisions must be available locally. Omit revision arguments to measure
only the checkout, or use `--scenario mixed` for a shorter run.

## Recording, privacy and deletion during search

A separate probe runs the actual Management search and archive mutation APIs
against 10,000 mixed turns. It uses an in-memory storage adapter: mutation latency
includes the archive's normal snapshot/partition serialization, but excludes disk
latency. Medians below use three cold repetitions.

| Operation during search | Original mutation latency | Tuned mutation latency |
| --- | ---: | ---: |
| record | 260.80 ms | 49.39 ms |
| private | 230.22 ms | 49.59 ms |
| delete | 221.86 ms | 34.67 ms |

The original search held the archive lock while its worker searched. The tuned
search did not hold it during worker processing in any probe. Recording completed
without invalidating the search. Making the selected transcript private or deleting
it caused the tuned search to reject the revoked result and request a retry in
all six relevant probes; old text was not returned.

```sh
python scripts/benchmark_archive_search.py --baseline-ref fac6deda --concurrency-only --repeats 3 --output archive-search-concurrency.json
```

Regression tests separately cover mutation/revocation, bounded cache ownership,
stable ranking ties, compound/symbolic/CJK terms, and avoiding repeated stemming
or admission accounting. Timing values are observations, not flaky CI thresholds.

## Limits that remain

- Cold searches remain linear in retained text. Warm searches still visit every
  eligible turn, including turns whose features do not fit the cache.
- The global feature cache admits at most 4,096 turns and an estimated 8 MiB of
  features. It is not a bound on all archive, snapshot or process memory. In the
  technical workload it held only 769 of 10,000 turns; the long-log workload fit
  completely. Cache entries do not keep deleted turn objects alive.
- Ranking retains at most `offset + limit` candidates; offsets above 10,000 are
  rejected. Capturing a stable snapshot still copies references to retained turns.
- Off-loop work still consumes CPU and can contend for the GIL. Cancellation ends
  the caller's wait but an already-running worker can finish its scan; this is not
  a workload quota or a fixed latency guarantee.

A persistent incremental index should be a separate, measured design change if
real deployment latency or resource use requires it. That work needs explicit
index versioning/rebuild rules and parity for privacy changes, deletion, retention,
restore and restart. A silent workload cap would hide matches or misrepresent
rankings, so this PR does not introduce one.


## Snapshot publication and concurrent changes

Model searches and Management search/list operations use
`ConversationArchive.async_query_snapshot()`. Archive captures scoped retained
records under its lock, releases the lock while the existing ranker runs in a
worker, and rechecks returned sessions and turn identities before publication.
Privacy changes, deletion, replaced/pruned turns, or unavailable Archive state
prevent stale results from escaping. Ordinary appends and unrelated sibling
changes remain allowed; there is no global revision rejection or lock held across
ranking. Cancellation stops waiting for the read without waiting for its worker.

The model tool keeps its existing controlled unavailable response after a revoked
search. Management keeps its existing retry error. The two rankers, tie ordering,
local date handling, page ceilings, output shapes and ranking memory bounds are
unchanged. The shared boundary owns publication safety, not ranking policy.
