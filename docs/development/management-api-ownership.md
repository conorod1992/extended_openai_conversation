# Management API ownership

`management_ui.async_management_command` is the stable Management API entry point.
Setup and feature installers must not replace it, the internal request pipeline,
or any section handler. The immutable section map is declared in the owner; it is
not a runtime registration mechanism.

## Request order

1. Acquire the maintenance command lease. It remains outermost, including response
   projection, to preserve the former effective dispatch order and protect writes
   throughout their lifetime.
2. Validate the common section/action contract and reject integration-global
   unauthorized reads before resolving an agent. Quiet Hours and the cheap agent
   catalog have explicit early routes.
3. Resolve the selected entry/subentry once into a frozen request context.
4. Apply the existing strict Request Rule dependency preflight and Function Tool
   revision checks. These checks run only for administrators; non-admin requests
   receive the section's authorization error rather than schema diagnostics.
5. Call one explicit section handler. Section-specific authorization, confirmation,
   validation and persistence remain in the handler. Repair-aware sections call
   explicit inspection helpers; configuration preview and execution stay strict.
6. Project shared configuration guidance, exposed-attribute and repair metadata,
   then return while releasing the maintenance lease on success or failure.

History, Memory and Usage limits are applied at their query/projection owners,
not by materializing an unbounded response and passing it through wrappers.
Overview uses the bounded, already-loaded manager snapshots; feature status does
not trigger a second Usage read. Non-admin private-history/Usage projections and
owner-scoped Temporary Memory operations retain their existing boundaries.
Temporary Memory counts in the scope catalog include explicit zero values.

## Section owners

The explicit map in `management_ui.py` covers Overview, Function Repair, Request
Rules, Guest Mode, Backup, Configuration, Tools, Scopes, Service Catalog,
Diagnostics, Usage, Conversations, Memories, Knowledge and Settings. Quiet Hours
is integration-global and is handled separately before agent selection.

Feature modules provide ordinary operations, validators, query helpers or result
projections. They do not install Management dispatch behavior. Function-group
reference changes are passed explicitly to the single configuration persistence
operation, with the expected revision, rather than through request-local patch
state.

## Tool and Group operation classification

`management_operations.is_tool_mutation()` owns the backend classification used
by revision admission and Tool timing. The inventory no longer belongs to the
dependency-integrity module. It does not dispatch, authorize or validate requests.

| Action | Handler-owned payload and validation |
| --- | --- |
| `save` | One `yaml` or `tool` definition, optional `original_name`; strict validation and reference reconciliation. |
| `set_enabled` | Existing string `name`, Boolean `enabled`. |
| `delete` | Existing string `name`, explicit confirmation and dependency checks. |
| `save_group` | Valid Group object and member names, optional original ID; specialized repair and capacity rules. |
| `delete_group` | Unambiguous saved Group ID and explicit confirmation. |
| `ha_add` | Selected HA references, asynchronous discovery and agent reread; optional destination Group. |

All six retain section-owned administrator checks after agent selection and the
dispatcher's shared maintenance lease. Shared admission does not mean read-only,
and mutation status must never select exclusive admission automatically. The
outer revision check remains administrator-guarded and permits omitted revisions
for legacy clients. Persistence captures and rechecks the revision around awaited
dependency validation; that is a separate guarantee, not redundant admission.
Named `serialize` reads retain their conditional revision check in the handler.

Frontend `mutationEffects()` remains bundle-local: these operations invalidate
Configuration and Request Rules and use the Tools revision queue. That queue is
not a server authorization or mandatory-revision rule. Function Repair and
Configuration saves have different acknowledgement ownership and stay separate.
Unknown operations retain existing server errors and frontend null effects.

`management-operation-contract.test.mjs` runs the actual pure backend classifier
against actual frontend effects, using candidate names discovered from consumers
instead of another hand-maintained action list. Deliberate classification and
invalidation changes must fail the check. This prevents one-sided drift without
runtime metadata loading, code generation or version negotiation. It cannot infer
a newly mutating operation if both consumers omit it; public persistence/revision
and frontend observable-effect tests remain independent safeguards.

Early permissions, resource permissions, self-owned Backup/diagnostic/live-test
gates and operation-specific request schemas remain with their existing owners.
They do not share equivalent contracts and do not need a universal registry.

## Boundaries

This refactor does not change stored formats, frontend assets, the standalone
Memory surface, conversation request entry, Function Tool execution or memory
retrieval. Remaining installers for those runtimes are separate follow-up work;
repeated activation must nevertheless leave the Management dispatcher unchanged.

`tests/test_management_command_ownership.py` guards immutable routing, stable
identity after installer activation/repeated setup, authorization and maintenance
order, invalid requests, previews and structural ownership. The genuine-HA History
suite verifies identity across actual setup/reload and persisted Temporary Memory
scope counts through the WebSocket API. Behavioral tests exercise these owners
rather than reconstructing obsolete wrapper stacks.

## Configuration mutation compatibility

`configuration/update` uses the same reviewed live-update decision as Configuration Save. Title, model and other setup-owned changes trigger the parent reload.

The legacy `settings/update` writer accepts only fields in `settings_snapshot`. It requires the current Configuration revision, validates the merged provider request, and uses the same live-update decision. Its response includes the new revision. Callers should obtain the current revision from `configuration/get` before writing.

Lightweight setup import validates provider compatibility in both preview and persistence. AI Task native flows validate the final candidate before saving. Provider changes validate all existing Conversation and AI Task configurations and static Request Rule overrides before changing the parent entry.
