# Model data catalogue

Extended OpenAI ships a descriptive JSON catalogue of the model metadata it already uses. Configuration, Request Rules, request parameter support, API Auto selection, and explicit prompt-cache support read the same active metadata. Existing helper interfaces and the bundled behaviour remain unchanged.

## Updating and resetting

In **Assistant → Model & responses → Model parameters**, administrators can select **Update model data** or **Use bundled model data**. The buttons affect every agent in this Home Assistant installation. They do not change saved agent settings, send an OpenAI request, or require an API key. The available controls refresh after a successful update.

An hourly background timer checks whether 24 hours have elapsed since the last attempt. The first automatic check runs within an hour of integration setup; startup never waits for a network request. Attempt time is persisted, including after failures, to avoid repeated checks across restarts. Manual updates bypass the interval. Reset removes the downloaded override and ETag and postpones the next automatic check for 24 hours; it does not permanently disable updates.

The fixed HTTPS source is this repository's `develop` branch:

`custom_components/extended_openai_conversation_responses/model_catalog.json`

The update source becomes available when this change is merged. Updates are independent of an installed integration release: maintainers can publish a validated catalogue change to `develop` without users reinstalling the integration. A failed download (including a missing file) keeps the current metadata.

Downloaded data is stored in HA's `.storage/extended_openai_conversation_responses.model_catalog`. Files under `custom_components` are never overwritten. The bundled catalogue remains the fallback. Reloading an entry retains the shared catalogue; restarting HA validates and restores the stored override. An older downloaded version cannot override a newer bundled catalogue after an integration upgrade.

## Data contract

The root has exactly `schema_version`, `catalog_version`, `defaults`, and `models`:

- `schema_version` is currently the integer **1**. Unsupported schemas are rejected.
- `catalog_version` is a positive, monotonically increasing integer. Increment it for any data change. Older versions and changed content with the same version are rejected.
- `defaults` describes the integration's existing unknown-model compatibility metadata.
- Each model has `id`, `display_name`, `kind` (`alias` or `snapshot`), and the same metadata fields as `defaults`. Optional `deprecated` and `replacement` are descriptive only; they never rewrite a configured model ID. The initial catalogue adds no new deprecation claims.

Metadata fields:

| Field | Meaning |
| --- | --- |
| `parameters` | Six boolean support flags: `supports_top_p`, `supports_temperature`, `supports_max_tokens`, `supports_max_completion_tokens`, `supports_reasoning_effort`, `supports_service_tier` |
| `reasoning_efforts` | Nonempty, unique list drawn from `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `completion_token_limit` | Completion-token parameter support used by the legacy token helper |
| `chat_reasoning_tools` | Compatibility metadata consumed by Python's existing Auto API decision |
| `explicit_prompt_cache` | Support used by Python's existing explicit-cache optimization |

The catalogue represents **the integration's existing compatibility knowledge**, not an exhaustive provider model inventory or an account-access guarantee. For parity it retains historical differences between the token helper and the parameter profile, as well as the usual reasoning choice list for non-reasoning/unknown models. Parameter support still determines whether reasoning is actually sent.

Exact IDs take precedence. Dated snapshot IDs inherit a matching alias unless explicitly described. Unspecified models retain bundled metadata; unknown family/provider-name compatibility matching remains Python code. Remote data cannot supply regexes, matching expressions, request templates, executable code, endpoints, tool/security rules, Guest Mode policy, or request-building instructions. Adding a new kind of capability requires a code/schema change.

Data-only catalogue updates may broaden the reasoning-effort choices available for a model, but they cannot remove a choice that the currently active catalogue accepted for the same exact, inherited-snapshot, or fallback model path. Agent configuration and Request Rules persist those values, so narrowing them without a migration could make previously valid durable state fail validation on reload. A reasoning-choice removal therefore requires an integration release with an explicit compatibility/migration decision rather than a hot catalogue-only update.

## Failure handling and maintenance

Downloads have a 15-second timeout and a 256 KiB limit, use ETag/If-None-Match, and reject redirects. Validation rejects duplicate JSON keys, duplicate model IDs, unknown fields, invalid types, unsupported schemas, unsupported reasoning choices, and hot updates that would invalidate a previously accepted reasoning choice. Updates and resets are serialized. HA Store persists the complete candidate before a single in-memory publication; HTTP, parsing, validation, and storage failures leave the previous catalogue active. No network request is made from synchronous model helpers or the conversation request path.

When publishing metadata, edit the JSON, increment `catalog_version`, run `tests/test_model_catalog.py`, and review any intended differences from `tests/fixtures/model_capability_parity.json`. That fixture was captured from the pre-migration helpers and must not be regenerated merely to hide a regression. Frontend tests exercise backend-supplied reasoning choices, and `tests_real_ha/test_model_catalog.py` covers the registered admin update/reset boundary and real HA storage.
