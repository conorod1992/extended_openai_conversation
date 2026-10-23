# Credential boundary maintenance

The sharing and operational diagnostic boundaries are deliberately separate from private backup serialization. Do not route private backups through a public projection, reinterpret historical redacted formats as lossless, or infer that arbitrary strings are public because they do not match a pattern.

## Sharing writers

Current Setup, configuration and Rule Pack writers call `sharing.project_sharing_sections`. Only classified sections survive. Writers build an explicit envelope from the projected result and public metadata; they must not merge source configuration, provider connections or future source fields back into it.

The reviewed pipeline has four responsibilities:

1. Apply shared designated-key and scalar credential policies.
2. Interpret schema structure, instance literals and bounded references. Property names have structural meaning; scalar annotations still receive `redact_text` checks. Schema URI/vocabulary checks additionally handle URI-reference forms.
3. Inspect supported structured request strings without executing source.
4. Admit only complete custom units with no redaction markers, recognised credential scalar values, unsupported typed leaves or non-finite values. Omit credential-bearing tools/rules rather than altering runnable source.

Schema traversal has explicit schema-node and annotation-data contexts. `_SCHEMA_MAP_KEYWORDS` classifies named schema maps; `_SCHEMA_CHILD_KEYWORDS` classifies single/array applicator locations, including legacy tuple `items`. `dependentRequired` is a validated map of property-name lists. Other fields enter a data context through the ordinary redactor, including their own annotation key, and can never switch back to schema context merely by containing `properties`, `$defs`, `functions` or similar names. URI/dialect/vocabulary checks are defensive in either context; opaque references are not followed as schema authority. Public extension data stays portable, but a custom dialect's credential-named structure may require conservative omission. To support another structural vocabulary, classify its semantics and add actual-artifact credential and portability tests rather than restoring generic recursion.

Restricted mode selects established public settings and omits custom units regardless of credential spelling. Reviewed mode deliberately includes arbitrary authored content after checks and explicit review. Neither tests nor pattern matching prove that arbitrary included text is secret-free. New Function types remain omitted in restricted mode; reviewed opaque source remains uncertain.

Historical helpers retain their legacy schema/placeholder behavior. The current sharing pipeline applies its schema-value pass and final admission independently of those exemptions. Private v6/v3 backups and recovery journals retain their source, credentials and types without these projections.

## Operational diagnostics

`operational_errors.failure_diagnostic` creates an immutable snapshot containing fixed category, exception class, bounded parser coordinates and OS error number. It never owns parser input, marks, filenames from error objects, exception prose or traceback objects. `log_handled_failure` adds safe operation labels and optional causal code locations.

The namespace-local record factory also projects exception messages/arguments and strips traceback attachments before handlers format EOAI records. It chains existing factories and covers future child loggers. It leaves other integrations' records alone. HA-owned Template, Script and Scrape records continue through their existing execution-scoped projection.

Pass exception objects or `failure_diagnostic` results, or use `log_handled_failure`. Do not use `str(error)`, f-strings, `%`, `.format`, derived message variables or `extra` fields to copy exception prose before this boundary. A record factory cannot recover the provenance of an already formatted string. Keep authorised editor feedback separate: it can show detailed validation of the administrator's own input without turning that detail into an operational log.

## Review tripwires and evidence

`credential-boundaries.json` records current YAML/JSON parser owners, call counts, conventionally named export/codec owners, their policy and a related regression suite. `tests/test_credential_boundary_inventory.py` discovers direct and imported aliases, rejects unclassified additions/moves, checks current sharing writers use the shared projection, and flags common preformatted-error bypasses. When a tripwire fails:

1. Trace the new owner's input and every output/error/log destination.
2. Classify its public, private, editor or provider role and apply the corresponding boundary.
3. Add a behavioral regression against the final artifact or formatted handlers, including a legitimate-content control.
4. Update the reviewed inventory and related suite pointer deliberately. Do not regenerate it merely to silence a failure.

The inventory is a review aid, not an interprocedural security proof. Parser wrappers or APIs outside the supported YAML/JSON call spellings still need explicit review. Exception dataflow checks do not prove that every arbitrary string passed to logging is safe. New public writer APIs should use the export naming convention and register their role; classification alone is not evidence that they are safe.

Boundary regressions exercise actual Setup/configuration writers, unknown-format values in designated fields, URL placement changes, persisted YAML, future envelope/section omission and private restoration. A deliberate schema-walker regression must still be caught by final unit admission. Operational tests exercise actual Skill discovery and multiple handlers, descendant loggers, causal errors, mapping arguments, unrelated loggers and factory chaining. Existing Rule-result and quarantine diagnostics retain useful operation guidance while using the shared structured representation.

Run the inventory, sharing-boundary and operational-diagnostic-boundary suites together with the affected sharing, private-backup, error-observability, Skill and Rule tests. Passing a finite credential corpus samples behaviors; structural exclusions, complete-unit omission and exact private round trips are the stronger properties being asserted.
