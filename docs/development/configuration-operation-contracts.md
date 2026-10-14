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
| Legacy Function enable/disable services | Persist the merged normalized candidate, preserving omitted reasoning but materializing ordinary defaults. This differs from the Function editor's owned-field save. | `async_set_function_tools_enabled`, `async_set_function_groups_enabled` |
| Unrelated configuration save through the degraded Function repair handler | Preserve raw Function fields and legacy Guest policy, but persist other normalized values. This differs from the ordinary section-save projection. | repair `configuration_save` handler |

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

## Unresolved policy decisions: unchanged in this PR

1. **Authored versus effective portable transfer.** Sparse source sampling settings
   become explicit default values after transfer and appear in compatible provider
   requests. Existing import tests require defaults. Preserving every omission would
   be a behavioral change and needs a deliberate version/compatibility decision.
2. **Duplicate versus portable transfer.** Duplication materializes reasoning while
   portable transfer preserves its omission. Decide whether these operations should
   converge before changing either contract.
3. **Legacy/degraded saves versus ordinary section saves.** The service and degraded
   repair paths above materialize more defaults. Adopting owned-field persistence
   there needs a separate behavior change with endpoint regressions; helper extraction
   alone does not authorize it.

These are observed behaviors, not claims that the policies are ideal. Tests lock
them down so this refactor cannot silently decide them. In particular, do not add
sampling to the omission policy or route duplicate/legacy saves through a different
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
