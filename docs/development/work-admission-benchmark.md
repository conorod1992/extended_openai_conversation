# Worker admission measurements

Run `python ci/benchmark_work_admission.py --output /path/to/report.json` from a checkout with the integration's HA/test dependencies and psutil installed. The constrained-resource workflow runs it on one CPU with the same 1.5 GiB address-space ceiling used by its mixed-workload tests and uploads the JSON report. It imposes no production admission limit or latency threshold. Worker failures and timeouts fail the command rather than producing a successful measurement.

Each concurrency level (1, 2 and 4 by default) gets a fresh Python process. Every case completes the same 64 jobs, each using 16 synthetic Knowledge sources with 32,768 characters per source. Jobs call the production context-free backup JSON decoder, canonical Knowledge loading/index construction and bounded JSON exporter. The benchmark semaphore controls only the experiment. Schema version 2 reports also include repeat numbers, worker-source hashes, installed HA/psutil versions and the PR head alongside the actual checkout revision. A dirty worktree flag alone does not identify whether measured source files changed.

Use `--jobs`, `--sources`, `--characters` and `--concurrency` to vary the workload. Use `--repeats 3` for three independent processes per level; execution order rotates across repeats to reduce first/last-case bias. CI requests three repeats; manual runs default to one. Preserve identical dimensions when comparing concurrency levels. Repeat runs on representative HA hardware before drawing a policy conclusion; compare constrained CI with unconstrained runs rather than merging their results.

These are worker-phase measurements, not full backup/restore or HTTP/provider acceptance tests. They exclude disk I/O, HA-aware configuration validation, YAML conversion and provider SDK response materialization. The input text is shared across jobs and caches may warm within a case. Heavy outputs are released after each job, so this measures transient work rather than the resident cost of retaining many agents' indexes. RSS is sampled every 10 ms and may miss brief peaks; heartbeat percentiles are descriptive samples, not confidence bounds. The report retains sample counts.

An admission limit is justified only if repeated results show a useful reduction in memory or scheduling delay at acceptable throughput and queueing cost. Before implementing one, test cancellation, shutdown, nested work, per-agent fairness and maximum admitted payloads. A queue must also be bounded: simply moving unbounded input retention behind a semaphore does not resolve resource exhaustion. Provider response limits need separate measurements before SDK materialization; this harness does not assess that boundary.

## Initial constrained Linux results and decision

[PR CI run 40953068380](https://github.com/conorod1992/extended_openai_conversation/actions/runs/40953068380) passed all checks, including the existing mixed-workload constrained acceptance and this probe. Artifact `work-admission-benchmark-1` recorded one CPU, a 1.5 GiB address-space limit and Python 3.14.9. Each case completed all 64 jobs with 527,640 input bytes per job and 320 indexed chunks. The checkout was merge commit `fc671fd792a79c595c5c4c5f53d80045a9c6b3f4`, whose second parent is PR head `eb1560d2b4691836d8fa97aa78135aa233981ebf`. The initial report marked its worktree dirty and did not contain source hashes or installed HA version; treat it as exploratory evidence. Version 2 adds that provenance for subsequent runs.

| Workers | Jobs/second | Peak sampled RSS above baseline | Heartbeat p95 |
| --- | ---: | ---: | ---: |
| 1 | 15.39 | 14.0 MiB | 7.51 ms |
| 2 | 14.52 | 27.5 MiB | 27.36 ms |
| 4 | 14.77 | 38.0 MiB | 80.33 ms |

There is no measured throughput advantage from higher concurrency in this run. More concurrent work increased transient memory and scheduling delay. This supports investigating a narrow CPU-worker scheduling policy on constrained hosts. It does not demonstrate exhaustion, establish an optimal limit for multi-core hosts, or measure user-visible queueing and cancellation behavior. It also covers a small fraction of the maximum accepted payload sizes and reuses input text across jobs.

**Recommendation: do not introduce a global admission gate on this evidence.** Keep existing per-operation bounds and per-manager serialization. First confirm that a narrowly scoped CPU-work limit improves real mixed HA workloads after accounting for waiting time, fairness, cancellation and retained queued inputs. Avoid changing HA's shared executor capacity, which would affect other integrations. The measured scheduling effect is an enhancement opportunity; this probe alone does not establish a P1/P2 production defect.
