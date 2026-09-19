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
   validation and persistence remain in the handler. Management-only Function Tool
   quarantine is scoped to the eligible sections and resets even after an error.
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
