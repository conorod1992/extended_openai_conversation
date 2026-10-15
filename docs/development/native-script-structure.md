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

## Consumers and PR6 boundary

Request Rules' Function validation, result-dependency checks, sensitive-action
warnings and authorization inspections use the shared canonical traversal through
a thin limit adapter. Function dependency inspection and match preview import the
shared public iterator with the same explicit Request Rule limits. Script Function
device rejection supplies only
its device-action policy; it no longer knows the nesting grammar.

Normalization, canonical inspection and preflight share structural classification.
Stop instrumentation, effect-template rewriting and native authorization insertion
retain their existing transformers for PR6. No generic rewriting API is introduced
here. Their migration must preserve one-to-many action expansion, native branch
semantics, metadata and authorization timing.

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
