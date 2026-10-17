# Configuration persistence boundaries

Normalization validates an effective configuration and supplies values for display.
It is not evidence that an administrator edited every field in that configuration.
Missing fields can select legacy Guest Mode semantics, inherit a model recommendation,
or leave a feature disabled. Unknown future fields and raw Function YAML also belong
to the saved source, not to a normalized editor snapshot.

For partial updates, validate the merged candidate, then persist
`project_agent_config_updates(current, updates, candidate)` or
`project_agent_config_fields(current, owned_fields, candidate)`. These helpers copy
untouched raw values and preserve key absence. A model change deliberately owns the
reasoning reset. New AI Task model/effort choices also own removal of sampling values
that are no longer supported. Validate the resulting AI Task request before writing.
Do not persist `merge_agent_config()` or a display snapshot directly for a patch.
`update_live_subentry()` marks runtime notification behavior; it does not determine
which configuration fields an operation owns.

## Writer audit

| Operation / owner | Persistence contract | Audit result |
| --- | --- | --- |
| Management `settings/update` | Submitted Settings fields only | Fixed: uses the shared scoped unchanged-Function merge; no implicit Guest v2 adoption or unrelated defaults. |
| Management `configuration/save` and `configuration/update` | Submitted fields/title and explicit model-reset dependency | Already project partial updates. Existing strict rejection and repair-aware editor behavior remain distinct. |
| Guest `save_policy` | Submitted Guest fields plus explicit v2 policy version | Shared helper fixed; v2 adoption is intentional here, while unrelated source fields remain raw. |
| Knowledge `set_enabled` | `knowledge_enabled` only | Shared helper fixed; the response still uses a separate normalized snapshot. |
| Function repair `configuration_save` | Submitted non-Function fields/title, with Functions/Groups retained verbatim | Fixed: projects the filtered update instead of persisting the normalized safe configuration. |
| Function / Group edits and repair save/delete | Owned Functions/Groups and validated reference updates | Strict/tolerant writers copy/project raw source; removed legacy-policy guards that could undo explicitly owned Guest reference renames. Quarantined siblings and unrelated omissions remain intact. |
| Function enable/disable services | Functions or Groups only | Already use `project_agent_config_updates`; public service regressions retain legacy policy and opaque fields. |
| Conversation native reconfigure flow | No write | Already panel-only; opening the flow never normalizes saved data. |
| AI Task basic/advanced reconfigure | Submitted form fields and explicit model/effort dependencies | Fixed: form defaults become suggestions, omitted model/reasoning/sampling fields remain omitted, and retries retain authored edits rather than display defaults. No conversation defaults are added. |
| Provider credentials, reauth, and `change_config` service | Submitted provider fields; existing URL/credential handling | Raw provider data is merged; child configurations are validated, not rewritten. |
| New conversation / AI Task | Intentional creation defaults | Preserved. |
| Portable import, duplicate, and backup/transfer restore | Intentional replacement under the existing transfer schema | Preserved: strict portable fields, explicit transfer defaults, legacy policy/model omission protections, and recovery-only opaque-field preservation. Restore rollback is also a replacement. |
| Structural and exact-stock migrations in `__init__.py` | Explicit migration of declared fields | Preserved: copy raw source and change only the migration's fields; AI Task migrations do not add conversation defaults. |
| Validation, previews, export projections, catalogue refresh | No configuration write | Normalized candidates remain read/validation objects; catalogue-derived cache generation is separate from saved revision. |

## Regression boundaries

The archive-retention regression was first run against the unfixed code through the
production WebSocket handler, real HA registries and the production prompt renderer.
It demonstrated an unallowed Assist-exposed lock entering the guest prompt after an
unrelated update. It now checks raw configuration equality and unchanged Guest entity
and shared-memory permissions.

The public-operation matrix covers sparse legacy/v2 sources, unknown future fields,
exact YAML preservation, supported degraded updates, and failure without writes on
strict legacy surfaces. AI Task tests submit actual generated form schemas, exercise
retry and model-transition behavior, and assert no conversation fields appear.
Genuine-HA acceptance tests additionally exercise an authenticated WebSocket, the
registered Function service, and both subentry reconfigure flows. Existing transfer,
creation, migration and explicit-reset tests retain their different operation contracts.
