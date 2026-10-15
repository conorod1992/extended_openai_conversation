# Native script structure and inspection

PR5 extracts the structural contract from Request Rules and Script Function device
preflight into `script_structure.py`. Home Assistant still validates and executes
scripts. This layer never evaluates templates, authorizes an effect, or decides
whether a condition passes.

## Authored and canonical inputs

`normalize_script_containers()` preserves the existing Request Rule normalization:
single action mappings become lists; `sequence`, `then`, `else` and `default`
normalize their action sequences; parallel action/sequence shorthand becomes
explicit `sequence` branches; choose branches and repeat sequences normalize in
place within an isolated copy. Existing aliases, branch metadata and authored
condition/template text survive. The historical `service_template` alias becomes
`service` only when neither `service` nor `action` was supplied.

`script_containers()` is the authoritative classifier for these structural fields.
It is called only on action/branch mappings, never recursively on arbitrary data.
A `sequence` key inside service data, variables, targets, conditions or triggers
therefore remains ordinary payload. Choose/repeat metadata and conditions are not
executable steps.

`iter_script_actions()` inspects canonical sequences in the established preorder:
parent action, direct sequences (`sequence`, `then`, `else`, `default`, `parallel`),
choose branch sequences, then repeat sequence. It yields original mappings without
copying or changing them. Existing parallel sequence wrappers count as action nodes;
choose branch and repeat metadata mappings do not. Mixed or non-sequence nested
values are skipped as before; schema validity remains HA's responsibility.

`iter_unvalidated_script_actions()` uses the same classifier and recursive walker
for the existing Script Function preflight contract. It accepts single-mapping
shorthand, filters non-mapping entries and inspects choose wrappers. It retains the
legacy repeat-before-choose order and does not acquire Request Rule quotas. It is
not an alternative shorthand normalizer or schema validator. Device rejection
remains a consumer policy, including its exemption for device conditions.

## Separate budgets

The canonical inspection defaults remain 500 action nodes and depth 12. A supplied
counter is shared throughout traversal; it advances before yielding a node, and an
over-limit node fails before being yielded. Depth increases on descent into an
action sequence, and an over-limit depth fails even for an empty sequence. Callers
can provide explicit limits. Request Rules' compatibility adapter passes its current
limits, preserving the existing module-level test/configuration boundary.

`_validate_script_complexity()` remains separate and unchanged. It counts every
payload value/container, including ordinary service data, and bounds full-tree
depth. A script with one action can pass action inspection while exceeding payload
limits. Shorthand normalization and Script Function device preflight gain no new
limits in this refactor.

## Inspection consumers and rewriting policies

Request Rules' Function validation, result-dependency checks, sensitive-action
warnings and authorization inspections use the shared canonical traversal through
a thin limit adapter. Function dependency inspection and match preview import the
shared public iterator with the same explicit Request Rule limits. Script Function
device rejection supplies only its device-action policy; it no longer knows the
nesting grammar.

Normalization, canonical inspection, preflight and rewriting share structural
classification. Stop instrumentation, effect-template mapping and native authorization
insertion now use `rewrite_script_actions()`; their former recursive container walks
have been removed. They retain their own execution/security policies.

## Rewrite contract

`rewrite_script_actions(actions, rewrite_action, transform_input=None)` provides
controlled zero/one/many action output. The callback receives the original mapping,
its structural role and a synchronous `descend()` continuation that rewrites children
into a fresh mapping. It can emit prefixes, retain the descended action, or replace
an action without descending. Call the continuation at most once, during the callback;
do not retain it for later execution. Generated output is never revisited, preventing
double instrumentation and recursion into newly inserted helper actions.

The structural `sequence` role covers `sequence`, `then`, `else` and `default`, plus
the outer action list. Parallel and choose roles remain distinct, so a Stop does not
accidentally acquire sequence scope from being a direct parallel-list member. Existing
scalar/non-list instrumentation compatibility is preserved separately from validated
effect-input mapping.

Without an input transformer, only structural containers are copied/rewritten.
Payload and hydrated HA objects retain their identities and are not mutated. With
`transform_input`, action effect fields and repeat inputs are mapped in original field
order. Choose branch metadata, `if`, and repeat `while`/`until` conditions remain
opaque; the effect consumer separately preserves complete native condition actions.
Payloads with keys such as `sequence`, `stop` or `choose` are still passed as whole
effect inputs, so their templates cannot bypass privacy checks.

The policies remain explicit:

- Stop probes own completion markers and success/error interpretation. An enabled
  Stop is replaced by one enabled native sequence containing its marker and Stop,
  preserving alias/continue-on-error metadata and one enabled decision.
- Native guards own effect classification, the original pending action and its
  enabled flag. Each guard stays immediately beside its effect. Authorization still
  occurs at execution time and can tighten after an await.
- Effect-template mapping owns the condition-action exemption and read wrappers.
  The shared layer identifies structural fields; it does not authorize reads or
  decide whether a condition should execute.

The production phase order is unchanged: result preparation, guard insertion, Stop
probes, HA schema/dynamic validation, effect-read wrapping, then existing result/slot
bindings and execution. Rewriting adds no node/depth quota and does not count inserted
helpers against the existing authored-input budgets. Result-value/payload traversals
remain separate because they intentionally inspect ordinary data as well as actions.

## Regression evidence

The focused tests specify expected action order independently of the classifier,
cover supported shorthand shapes and their nested combinations, and compare Function
references and match previews. They also assert payload/condition isolation, source
immutability, exact action/depth/payload boundaries, shared counters and preflight's
separate compatibility behavior. The initial 27 characterization cases pass on
unchanged `develop` before extraction.

Genuine HA tests compare authored Request Rules and Script Functions with separately
specified canonical scripts executed by HA. They verify service effects, lazy
templates, repeat counts, parallel branches, device rejection before earlier effects,
and full-payload limits that remain stricter than native HA's action count.


PR6 adds baseline transformer contracts for every container role and a direct
replacement/expansion regression. Genuine HA cases compare nested enabled/error
Stops with independently specified native scripts, then tighten Guest permissions
while an awaited HA service is suspended. Those cases verify trusted choose/while/
until conditions, revoked controls, hidden effect reads and owner positive controls.
A separate seeded differential probe matches all three pre-migration transformer
outputs and callback order/identity across 1,000 generated canonical trees.
