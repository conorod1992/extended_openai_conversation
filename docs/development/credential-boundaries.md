# Credential boundary maintenance

Legacy import and operational diagnostics have different data-handling requirements from private backup serialization. Do not route private backups through a public projection, reinterpret historical redacted formats as lossless, or infer that arbitrary strings are public because they do not match a pattern.

## Legacy imports

Older reviewed sharing files remain readable through the existing validated import paths. Historical redacted formats retain their destination-bound credential recovery. Private backups and recovery journals preserve source and typed values through their own codecs.

## Operational diagnostics

`operational_errors.failure_diagnostic` creates an immutable snapshot containing fixed category, exception class, bounded parser coordinates and OS error number. It never owns parser input, marks, filenames from error objects, exception prose or traceback objects. `log_handled_failure` adds safe operation labels and optional causal code locations.

The namespace-local record factory also projects exception messages/arguments and strips traceback attachments before handlers format EOAI records. It chains existing factories and covers future child loggers. It leaves other integrations' records alone. HA-owned Template, Script and Scrape records continue through their existing execution-scoped projection.

Pass exception objects or `failure_diagnostic` results, or use `log_handled_failure`. Do not use `str(error)`, f-strings, `%`, `.format`, derived message variables or `extra` fields to copy exception prose before this boundary. A record factory cannot recover the provenance of an already formatted string. Keep authorised editor feedback separate: it can show detailed validation of the administrator's own input without turning that detail into an operational log.

## Review tripwires and evidence

`credential-boundaries.json` records current YAML/JSON parser owners, call counts, conventionally named export/codec owners, their policy and a related regression suite. `tests/test_credential_boundary_inventory.py` discovers direct and imported aliases, rejects unclassified additions/moves, and flags common preformatted-error bypasses. When a tripwire fails:

1. Trace the new owner's input and every output/error/log destination.
2. Classify its public, private, editor or provider role and apply the corresponding boundary.
3. Add a behavioral regression against the final artifact or formatted handlers, including a legitimate-content control.
4. Update the reviewed inventory and related suite pointer deliberately. Do not regenerate it merely to silence a failure.

The inventory is a review aid, not an interprocedural security proof. Parser wrappers or APIs outside the supported YAML/JSON call spellings still need explicit review. Exception dataflow checks do not prove that every arbitrary string passed to logging is safe.

Boundary regressions exercise historical import and private restore fidelity. Operational tests exercise actual Skill discovery and multiple handlers, descendant loggers, causal errors, mapping arguments, unrelated loggers and factory chaining. Existing Rule-result and quarantine diagnostics retain useful operation guidance while using the shared structured representation.

Run the inventory, sharing-boundary and operational-diagnostic-boundary suites together with the affected sharing, private-backup, error-observability, Skill and Rule tests. Passing a finite credential corpus samples behaviors; structural exclusions, complete-unit omission and exact private round trips are the stronger properties being asserted.
