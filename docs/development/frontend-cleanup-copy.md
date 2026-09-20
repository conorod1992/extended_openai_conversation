# Direct copy ownership

Starting develop: `38e7ff2d2a7ddfc1202f0250483ab386d65d3298`.

The removed `polishRenderedCopy` transformations now belong to these renderers:

| Transformation | Owner and final behavior |
| --- | --- |
| Usage maintenance heading | `usage-chart.js`: Manage usage history, admin only |
| Retention subsection notice | `usage-chart.js`: omit redundant notice; retain navigation/action buttons |
| Aggregate totals explanation | The old exact-match string no longer exists. Preserve the current daily/monthly/selected-period/lifetime explanation. |
| Cached-input explanation | `usage-chart.js`: provider-recognised cached input, included in totals, potentially lower rate; retain strong emphasis |
| Continuity help wording/placement | `management-panel.js`: admin-only help precedes the first section, with or without active conversations |
| Archive enabled/disabled notices | `management-panel.js`: do not generate these redundant notices |
| Knowledge heading/count | `management-knowledge-feature.js`: Sources and singular/plural count, including collection reconciliation |
| Setup-health footnote | `overview-page-impl.js`: final Diagnostics explanation, retaining the accessible decorative icon |
| Broadcast intro | `overview-page-impl.js`: final message and busy-satellite explanation |
| Broadcast state | `overview-page-impl.js`: omit redundant enabled copy, render concise off copy; preserve controls and permissions |

Previously the shell-only scheduling could miss route renders and subsequent
collection updates. Owners now emit the intended final presentation immediately.
The cached-input exact-text helper could not rewrite its nested strong element;
the owner now produces the intended text without losing that emphasis.

Agent Config loading is unchanged in this stage. Direct detached-DOM browser
assertions cover admin/non-admin, enabled/disabled, active conversations,
escaping, zero/one/multiple sources, icon retention and emphasis. Existing route,
collection mutation, reconnect and genuine-HA assertions follow the final copy.

## Reproducible source measurements

Measure Git blob bytes (independent of checkout CRLF conversion), line counts via
`bytes.splitlines()`, nonblank lines via `line.strip()`, and file count. Include
all tracked `.js`/`.mjs` under `custom_components/`, excluding path components
`dist`, `build`, `node_modules`, `vendor`, `generated`, `tests`, `fixtures`.
The initial scope is 72 files, all in the integration's frontend directory:
833,459 bytes, 13,177 lines, 12,151 nonblank lines. Audit production edits outside
this scope separately; no replacement source is moved outside it.
Production diff counts use `git diff --numstat BASE HEAD` with the same paths.
Tests and generated production bundles are reported separately.
