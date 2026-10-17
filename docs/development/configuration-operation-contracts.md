# Configuration operation contracts

These contracts describe the existing conversation-agent behavior on `develop`
at `68f04736`. PR4 consolidates the projections without changing these policies.
AI Task options and provider credentials have separate contracts.

## Representations and ownership

The raw Home Assistant subentry dictionary records saved choices. Key presence is
its only source of explicitness: an explicitly saved default is still a choice,
while an absent setting may inherit future model or integration behavior. Do not
reconstruct explicitness by comparing values with current defaults, and do not add
a second synchronized explicit-fields collection.

`normalize_agent_config()` validates values and, with `apply_defaults=True`, supplies
integration/model defaults and compatibility normalization. `merge_agent_config()`
validates the final merged candidate, preserves opaque current fields and controls
model transitions. Neither is a universal persistence projection.

`agent_config_snapshot()` supplies normalized display values and parsed Function
Tools. Reading a snapshot does not save those values. A frontend that explicitly
submits a displayed field makes it part of the edit; the backend cannot infer that
it was merely displayed from its equality with a default.

`ProviderRequestSnapshot` remains the authoritative execution projection. It
resolves the active provider/model, inherited reasoning and compatible sampling
parameters. Persistence helpers must not substitute for that resolver.

## Operation matrix

| Operation | Persistence/default contract | Owning implementation |
| --- | --- | --- |
| Read/display/validation preview | Return normalized values; no persistence. | `agent_config_snapshot`, management validation/preview handlers |
| Import preview | Show normalized display values but preserve omitted reasoning. Guest display defaults are still shown; this is not the transfer persistence dictionary. | `preserve_agent_model_setting_omissions`, `agent_config_snapshot` |
| Ordinary configuration save/update; independent Guest save | Validate the merged candidate, then apply submitted fields to the raw configuration. Untouched values, omissions and opaque fields survive. A full submitted draft owns all its submitted fields. | `project_agent_config_updates` |
| Function editor save/repair | Apply Function Tools, Function Groups and explicitly owned extra fields to the raw configuration. Unrelated settings and omissions survive. Unchanged Functions can retain their exact stored representation. | `persist_valid_function_configuration`, `project_agent_config_fields` |
| Model or Azure underlying-model change | If reasoning is not submitted, discard the old model's effort and normalize the new model's recommendation. Persist the resulting reasoning field, or remove it when unsupported. Re-submitting an unchanged model is not a transition. Explicit reasoning is validated against the selected model. | `merge_agent_config`, `project_agent_config_updates` |
| Explicit reset of editor values | Reset changes the draft; persistence occurs on Save. Submitted reset values are explicit even if equal to defaults. This is not a general delete-field or inheritance-reset API. | frontend reset controls, ordinary save projection |
| New conversation agent | Materialize integration defaults and applicable model recommendations. | creation flow, `normalize_agent_config` |
| Version-1 portable export/import | Materialize ordinary defaults, including sampling. Preserve omitted reasoning and unversioned legacy Guest policy. Tools are parsed for export and serialized for import. Export excludes opaque fields; import rejects unknown configuration fields. Credentials remain outside the agent schema; Function secrets follow the existing redaction/restoration rules. | `project_agent_config_transfer`, `_export_agent`, `_parse_import_document` |
| Backup/export, recovery and selective transfer | Apply the same omission/default policy, retain opaque later-version fields and preserve quarantined raw Function values when safe to do so. Normal tools use the requested frontend/persistence shape. | `project_agent_config_transfer(preserve_unknown=True)`, backup quarantine boundary |
| Private destination recovery snapshot | As above, but malformed destination Function YAML can remain opaque for private recovery. Public export still requires repair of malformed YAML. | `private_configuration_snapshot` |
| Duplicate agent | Normalize recognized fields and preserve legacy Guest policy. Unlike portable transfer, omitted reasoning is materialized; opaque fields are excluded. | management duplicate handler |
| Legacy Function enable/disable services | Validate the merged candidate, then persist only the owned Function Tool or Function Group field. Preserve all unrelated raw values and omissions, including legacy Guest policy. Default Functions are materialized only when the tool field is edited. | `async_set_function_tools_enabled`, `async_set_function_groups_enabled` |
| Unrelated configuration save through the degraded Function repair handler | Preserve raw Function fields and legacy Guest policy; persist only the submitted unrelated fields. | repair `configuration_save` handler, `project_agent_config_updates` |

## Shared projections

`project_agent_config_fields(current, fields, normalized)` applies only the owned
fields, deleting an owned field when normalization omitted it. It deep-copies the
result so callers cannot mutate retained raw state through the projection.

`project_agent_config_updates()` selects submitted fields plus reasoning when a
model changes. The same model-transition predicate is used during merge validation.
Function repair uses the lower-level owned-field projection with its established
ownership set; it does not acquire unrelated defaults by copying a display snapshot.

`project_agent_config_transfer()` takes ownership of a fresh normalized dictionary
and applies the transfer exceptions in one place. The raw source is not mutated.
`preserve_agent_model_setting_omissions()` owns the omission rule shared by merge,
transfer and import preview. Its policy is explicitly limited to reasoning; legacy
Guest compatibility remains owned by `preserve_legacy_guest_policy()`. Backup's
normal and quarantine paths both pass through this projection once. YAML parsing,
Function isolation, redaction and secret restoration remain at their existing
boundaries.


## Effective projection invariants

Cold reads, cache hits, startup prewarming, immediate save responses, and cache
reconstruction must agree on effective values for the same authoritative source.
`agent_config_value_snapshot()` shares the cheap scalar/selector derivations with
strict normalization: legacy prompt migration, numeric coercion, model-dependent
reasoning defaults, memory aliases, selector canonicalization and exposed attribute
preferences. Fast reads reuse already validated Functions/Groups; they do not
recompile templates or repeat Function schemas. Lightweight Settings and retention
views use the same numeric coercion without constructing a full configuration.
The provider-request boundary applies that coercion to a detached sparse mapping,
so legacy sampling strings agree with Management without pinning missing settings
or bypassing model capability filtering.

A source representation must be decoded exactly once. Existing persisted Functions
follow `parse_configured_function_tools()` and `normalize_persisted_agent_config()`:
missing values and historical falsy values (including a raw empty list) use the
legacy defaults. Truthy YAML that decodes to `null` or an empty list represents no
Functions. A newly submitted empty list is an intentional edit and is persisted as
YAML `[]`; it must not pass through the legacy persisted-value fallback. Prewarming
uses the persisted decoder before handing decoded values to the normalizer.

Private recovery configurations are persistence-shaped, including normal Function
YAML. Rollback can apply them directly without turning a decoded frontend empty
list into legacy defaults. Malformed quarantined Function YAML remains opaque in
private recovery. Portable incoming collections and already prepared restore data
use decoded-value normalization, not a second legacy fallback.
Restore reload decisions compare the persisted current source against the prepared
candidate using their respective decoders, so representation-only changes do not
replace an otherwise compatible live agent.

Cache seeding receives canonical response values derived from the saved source;
it does not make normalized defaults part of the saved dictionary. Subentry/data
replacement reconstructs the projection, and catalogue-generation changes expire
model-derived snapshots while preserving the saved revision and field presence.
Scoped writers still use the owned-field persistence helpers described above.

`tests/test_configuration_projection_invariants.py` checks these invariants across
scoped Function/Group/Knowledge/settings/configuration writes, raw-source isolation,
warm/cold/prewarmed/seeded/replaced caches, model/Azure identities, explicit empty
edits, duplication, portable and recovery round trips. Genuine-HA WebSocket and
restore/rollback cases live in `tests_real_ha/test_scoped_configuration_writes.py`.
The Group-save memory assertion passes with the #1257 snapshot implementation and
fails with its predecessor in both warm and cold cases.

## Remaining policy differences

1. **Authored versus effective portable transfer.** Sparse source sampling settings
   become explicit default values after transfer and appear in compatible provider
   requests. Existing import tests require defaults. Preserving every omission would
   be a behavioral change and needs a deliberate version/compatibility decision.
2. **Duplicate versus portable transfer.** Duplication materializes reasoning while
   portable transfer preserves its omission. Decide whether these operations should
   converge before changing either contract.

These are observed behaviors, not claims that the policies are ideal. Tests lock
them down so this refactor cannot silently decide them. In particular, do not add
sampling to the omission policy or route duplication through a different
projection as incidental cleanup.

## Regression evidence

`tests/test_configuration_operation_contracts.py` characterizes sparse and explicit
sampling, display versus persistence, model transitions and reset values, portable
round trips, and normal/quarantined recovery. It compares raw storage and production
`build_provider_request_snapshot()` kwargs and checks source isolation.

Existing Function-settings, save-validation reuse, degraded-mode, transfer,
redaction, recovery and real-HA live-save suites exercise their endpoint boundaries.
The genuine HA regression in `tests_real_ha/test_configuration_operation_contracts.py`
checks a sparse section save and portable round trip through the public management
command and the next serialized provider requests.
