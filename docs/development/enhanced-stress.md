# Enhanced nightly acceptance

`.github/workflows/enhanced-stress.yml` runs at 03:15 UTC every night against `develop` and can be launched with **Run workflow**. It is intentionally outside PR collection: Python tests live in `tests_stress/` while `pyproject.toml` collects `tests/`, and the browser file uses a separate Playwright configuration.

The current campaigns are:

| Campaign | New nightly work |
| --- | --- |
| `runtime` | Seeded public Assist conversations across two agents and six users, concurrent batches, unload/setup cycles, registry ownership and provider ChatLog isolation. |
| `backup` | Fault injection at all seven durable restore category writes, rollback/reload checks, and a reviewed inventory of persisted agent fields. |
| `request-rules` | Full matcher inventory, text variants, preview agreement, seeded create/toggle/delete/move/reload sequence and backup equivalence. |
| `guest-security` | Cross-product of supported Guest policies for Functions, Knowledge and shared Memory, with security and Function type inventories. |
| `quiet-hours` | Multiple repeated periods across real registry-backed satellites with distinct starting volumes and restoration checks. |
| `functions` | Seeded Function Group loading, disabling and session-isolation model across 24 tools, 12 groups and 16 conversations. |
| `memory-knowledge` | Hundreds of private Memory records and Knowledge sources with reload comparison, plus bulk Temporary Memory expiry, repeated owner-isolated reads and restart. |
| `chaos` | Seeded valid Memory, Knowledge and Request Rule mutations, backup checkpoints and restores, reloads and public request probes after every step. |
| `browser` | One mounted Chromium management panel through 80 seeded route changes, plus two tabs against the same genuine HA backend proving stale Request Rule saves are rejected. |

`normal` is the nightly workload. `heavy` multiplies Python operation counts by four and browser transitions by four. The workflow generates a seed if none is supplied, prints it in the first job and every trace, and includes it in the Step Summary. To reproduce a failure, choose the failing campaign and intensity in **Run workflow**, then paste the reported seed. Python traces are in `stress-artifacts/*.json`; browser traces and screenshots are retained on failure.

For a local Linux environment with the repository's test requirements installed:

```sh
STRESS_SEED=123 STRESS_INTENSITY=normal pytest tests_stress/test_request_rules_matrix.py -v -s --asyncio-mode=auto --timeout=600
STRESS_SEED=123 npx playwright test --config=playwright.stress.config.mjs
```

Home Assistant's Python test harness requires Linux. The Windows Python installation cannot run the Real HA tests because Home Assistant imports `fcntl`.

When adding a persisted agent setting, update `BACKED_UP_AGENT_FIELDS` in `tests_stress/test_backup_inventory.py` after reviewing the export and restore behavior. When adding a Request Rule matcher, action or routing scope, update the classified inventory and generated cases in `tests_stress/test_request_rules_matrix.py`. Keep a new stress test under `tests_stress/` or name a browser test `*.stress.mjs` to preserve the separation from normal CI.

The suite is complementary to the existing bounded Real HA, browser, release, upgrade and mutation tests. It does not rerun those suites. A failing operation trace identifies the seed and last completed operation; use the same seed to reproduce, then inspect the first violated invariant and Playwright trace or HA log.
