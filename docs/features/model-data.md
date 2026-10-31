# Model data catalogue

Extended OpenAI ships a JSON catalogue for OpenAI conversational and reasoning models and the provider capabilities this integration uses. Configuration, Request Rules, previews, and live requests use the same active capabilities. This is deliberately narrower than OpenAI's full model and product directory.

## Updating and resetting

In **Assistant → Model & responses → Model parameters**, administrators can select **Update model data** or **Use bundled model data**. The buttons affect every agent in this Home Assistant installation. They do not change saved agent settings, send an OpenAI request, or require an API key. The available controls refresh after a successful update.

An hourly background timer checks whether 24 hours have elapsed since the last attempt. The first automatic check runs within an hour of integration setup; startup never waits for a network request. Attempt time is persisted, including after failures, to avoid repeated checks across restarts. Manual updates bypass the interval. Reset removes the downloaded override and ETag and postpones the next automatic check for 24 hours; it does not permanently disable updates.

If returning to bundled model data would invalidate a saved agent request or Request Rule, including API, tools, Web Search, output limits or reasoning choices, reset is blocked and the downloaded data remains active. Change the affected saved setting/rule first, or install a newer integration whose bundled catalogue supports it. This prevents an administrative reset from making durable configuration invalid.

The fixed HTTPS source is this repository's `develop` branch:

`custom_components/extended_openai_conversation_responses/model_catalog.json`

Updates are independent of an installed integration release: maintainers can publish a validated catalogue change to `develop` without users reinstalling the integration. A failed download (including a missing file) keeps the current metadata.

Downloaded data is stored in HA's `.storage/extended_openai_conversation_responses.model_catalog`. Files under `custom_components` are never overwritten. The bundled catalogue remains the fallback. Reloading an entry retains the shared catalogue; restarting HA validates and restores the stored override. An older downloaded version cannot override a newer bundled catalogue after an integration upgrade.

## Data contract

The root has `schema_version`, `catalog_version`, `compatibility`, `defaults`, and `models`:

- `schema_version` is currently **8**. Increment it for any field or semantic change that needs new EOAI code. The validator also accepts schemas 5, 6, and 7 and migrates older persisted schemas where supported.
- `catalog_version` is a monotonically increasing factual revision. Increment it for a capability-data change within an already supported schema. Older versions and changed content with the same version are rejected.
- `compatibility.minimum_eoai_version` is the earliest integration release that understands this catalogue. A newer schema or minimum release is shown as an incompatible update; active and saved catalogues remain usable. Rejected data is never cached with an ETag, so a later compatible update can be discovered.
- `defaults` gives conservative metadata for unknown exact IDs.
- Each model has `id`, `display_name`, and `kind` (`alias` or `snapshot`). Full records declare the capability fields below. A snapshot with `alias_of` can instead declare only its actual overrides. Its parent must be an explicit catalogue ID; there is no family-name or date-pattern inference. Nested capability objects can override individual fields. The effective record is validated when the catalogue is loaded or applied.

Metadata fields:

| Field | Meaning |
| --- | --- |
| `api`, `function_calling` | API support and tool support, including effort-limited Chat tool calling |
| `reasoning`, `temperature`, `top_p` | Reasoning choices and effort-dependent sampling support |
| `limits`, `output_tokens`, `streaming` | Token ceilings, output-token parameters, and streaming support |
| `structured_outputs`, `responses_web_search` | Provider features EOAI currently uses |
| `service_tiers` | Provider-accepted request values; account entitlement is separate |
| `recommended_profile`, `auto_api` | EOAI application policy, rather than provider facts |
| `status`, `deprecated_at`, `shutdown_at`, `lifecycle_note` | Model lifecycle metadata. Dates are ISO `YYYY-MM-DD`; deprecated models remain usable until the provider rejects them. |
| `explicit_prompt_cache` | Whether EOAI uses its explicit cache-breakpoint optimization; `false` does not imply a lack of implicit provider caching |

The catalogue is compatibility knowledge for EOAI's current request paths, not an account-access guarantee. It does not mirror unrelated hosted tools or specialized audio, image, embedding, and moderation products.

Deprecated models are hidden from new model suggestions but retained for existing saved configurations. A structured `shutdown_at` date drives Overview warnings but never blocks a request by itself. After that date, EOAI only treats a model as retired when the provider also returns a model-unavailable/not-found failure; that confirmed failure produces an actionable Assist error and Home Assistant Repair until the model is changed or a later request succeeds.

### GPT-4 retirement on 14 January 2027

OpenAI's [14 October 2026 deprecation notice](https://developers.openai.com/api/docs/deprecations#2026-10-14-gpt-4-series-model-deprecations) retires `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, and `gpt-4.1-mini` on **14 January 2027**. Catalogue revision 15 records these dates for the aliases and the exact snapshots below:

| Alias | Snapshots retiring on 14 January 2027 |
| --- | --- |
| `gpt-4o` | `gpt-4o-2024-08-06`, `gpt-4o-2024-11-20` |
| `gpt-4o-mini` | `gpt-4o-mini-2024-07-18` |
| `gpt-4.1` | `gpt-4.1-2025-04-14` |
| `gpt-4.1-mini` | `gpt-4.1-mini-2025-04-14` |

The notice includes fine-tuned versions of these base models. EOAI does not infer capabilities or lifecycle dates from custom/fine-tuned ID prefixes; those IDs retain the existing unknown-model handling and provider errors. Owners of fine-tuned configurations must also plan migration.

The four newly documented snapshot IDs keep Responses as their no-tool Auto path, preserving the behavior they had as unknown exact IDs. Existing aliases and the already documented August 2024 snapshot keep their previous Auto routing.

Newly catalogued snapshots use the documented `max_completion_tokens` field for explicit Chat Completions requests. OpenAI and Azure already used that field for these IDs. Third-party endpoints exposing these exact OpenAI IDs must support it; custom unknown IDs keep their existing `max_tokens` fallback.

OpenAI recommends [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna). Select it explicitly in **Assistant → Model & responses** after testing representative conversations, Home Assistant actions, Request Rules and any AI Task settings. EOAI does not rewrite saved models, defaults or routing rules. The existing model continues to be sent unchanged, including after the announced date; a provider-confirmed retirement then asks you to select an available model instead of silently substituting one.

Follow the [GPT-6 migration guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra): use **Responses** for function tools with reasoning. Luna's **Chat Completions** function calling requires `reasoning_effort: none`. Temperature and `top_p` are supported only with `none`; otherwise omit them. Luna supports a 1,050,000-token context and up to 128,000 output tokens, with `medium` as the provider's default reasoning effort. EOAI's existing Luna record already describes these constraints. Review the reasoning and sampling settings when changing models; this is not a drop-in rename for every saved request.

Similar names do not imply the same schedule. The [separate notices](https://developers.openai.com/api/docs/deprecations) retire `gpt-4o-2024-05-13` and `gpt-4.1-nano` (including its 2025-04-14 snapshot) on **23 October 2026**, with `gpt-5.6-sol` and `gpt-5.6-luna` as the respective replacements. Audio/realtime, transcription and TTS variants have their own notices and dates. They do not inherit the January 14 metadata. The older May GPT-4o snapshot is not added by inheriting the newer alias's capabilities: [Structured Outputs support](https://developers.openai.com/api/docs/guides/structured-outputs) differs. This update adds only the verified January 14 snapshots.

Exact IDs take precedence. Only snapshots that explicitly name a parent inherit capabilities. Unspecified IDs receive conservative unknown metadata. Parent chains are resolved once into an exact-ID index at catalogue activation; runtime requests never search or merge a family tree. Remote data cannot supply regexes, matching expressions, request templates, executable code, endpoints, tool/security rules, Guest Mode policy, or request-building instructions. Adding a new kind of capability requires a code/schema change.

Data-only catalogue updates cannot silently narrow previously accepted durable request choices. Transition checks compare effective inherited capabilities, including API paths, function calling, reasoning choices, sampling, service tiers, streaming, and output ceilings. Narrowing requires an integration release with an explicit compatibility/migration decision.

## Failure handling and maintenance

Downloads have a 15-second timeout and a 256 KiB limit, use ETag/If-None-Match, and reject redirects. Validation rejects duplicate JSON keys, duplicate model IDs, unknown fields, invalid types, unsupported schemas, unsupported reasoning choices, and hot updates that would invalidate a previously accepted reasoning choice/capability. Updates and resets are serialized. HA Store persists the complete candidate before a single in-memory publication; HTTP, parsing, validation, and storage failures leave the previous catalogue active. No network request is made from synchronous model helpers or the conversation request path.

When publishing metadata, edit the single JSON feed and increment `catalog_version`. If consumer code must change, increment `schema_version`, update the supported validator declaration, and register the new semantic fingerprint in `ci/catalog_schema_contracts.json`. Ordinary CI runs `ci/catalog_contract_guard.py` to detect unversioned changes to key shapes and enum sets. Run `tests/test_model_catalog.py` and `tests/test_model_catalog_transitions.py`, and review any intended differences from `tests/fixtures/model_capability_parity.json`. That fixture was captured from the pre-migration helpers and must not be regenerated merely to hide a regression. Frontend tests exercise backend-supplied reasoning choices, and `tests_real_ha/test_model_catalog.py` covers the registered admin update/reset boundary and real HA storage.

Classify every catalogue capability leaf and tool-condition dimension in `ci/catalog_capability_consumers.json` as backend, frontend, both, or informational. Ordinary CI compares this registry with the complete bundled catalogue and the validator's accepted condition dimensions. The generated SDK wire matrix checks the current models against UI projection, backend validation, and actual OpenAI SDK JSON serialization. The small live canary selects six supported claims; its weekly schedule runs in plan-only mode. An authorized manual run with plan-only disabled can call OpenAI production. Its report contains case identities and failure classes, never prompts, responses, or credentials. The weekly SDK compatibility job runs the focused wire suite against selected versions across the manifest's supported 2.x/3.x range, plus a newer 3.x early-warning lane. That experimental lane does not expand the manifest's supported range.


### Output token limits for custom providers

Unknown models on a custom Chat Completions endpoint use `max_tokens` for the
configured output limit. Models listed in the catalog use their exact mapping;
OpenAI and Azure retain `max_completion_tokens`. Responses uses `max_output_tokens`.
Catalog schema 8 also permits a model to specify `chat_completions: max_tokens`
with `legacy_max_tokens: chat_completions_only`. Older schema catalogs remain readable.
