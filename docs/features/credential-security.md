# Sharing and private backup security

## Legacy public export files

Existing Shareable Setup and configuration export files retain their import validation, including version 1 and version 2 files. Older reviewed exports can contain authored code and secrets. Inspect them before restoring or sharing them again. Historical redaction placeholders may require credentials from the destination configuration or manual credential re-entry as directed by the import preview.

## Complete private backups

New full backups use payload version 6; new custom private transfers use version 3. Their private configuration and Rule fields use a versioned safe-YAML envelope inside the JSON/ZIP transport. This preserves opaque YAML source, typed data, inline credentials, signed URLs, and literal text/dictionaries resembling old redaction markers. Those markers are not interpreted in new private formats. Existing backup versions 1–5 and transfer version 1 keep their original redaction/recovery semantics.

Private backups preserve the selected agent's configuration and saved data. Provider connection data, including credentials and credential-bearing URLs, is captured when configuration is included. In the restore interface, **Also restore the provider connection** is a separate explicit choice: the connection can be shared by multiple agents. It is applied after agent restoration, and a concurrent connection edit prevents overwriting it. A connection-update failure does not undo an already committed agent restore; the interface reports that distinction and asks you to import/preview again. Ordinary restoration does not silently replace a shared connection.

The package cannot recreate external HA scripts, secrets, files or remote services. Preserve those dependencies with an appropriate Home Assistant backup. Tokens and signed URLs are preserved exactly, but expiration or provider revocation can still require attention. Expired temporary memories retain their existing restore policy; transient locks/caches and active requests are not restoration data.

Selective restore preserves authoritative unselected configuration verbatim. Private backup and recovery-journal inspection validate a separate effective projection without replacing the stored configuration, including valid and quarantined Function YAML. Internal raw-source provenance is not an import-file flag; historical public backup formats retain their prior semantics.

Function edits and Management editor transport use a shared safe YAML writer that preserves Unicode line separators rather than folding them into spaces. Toggling, editing or repairing a restored Function therefore retains its exact scalar values, including passwords and literal redaction-marker text. Ordinary Unicode remains readable. This does not change the private backup format or reinterpret existing backup data.

Request Rule enable/disable sends only that field; the server retains the exact stored action and condition values and checks its revision after validation. Whole-Rule updates that would replace an existing integer outside JavaScript's safe integer range are rejected with guidance instead of silently persisting a rounded value. Native exact-value updates can retain those numbers. To change or remove such numeric values, use a lossless private import; do not convert credentials to strings unless the action supports that type. Private backups and immediate restoration retain the original numeric types and values unchanged.

**These files are private and unencrypted. Keep them private and store them securely.** ZIP compression, base64 and checksums do not provide confidentiality or prove who authored a file.

There is no built-in password protection for these exports. If you need an encrypted copy, protect the downloaded file with your own trusted storage or encryption tools.

## Errors, logs and support information

Template and arbitrary backend exception prose is excluded from public tool errors; safe failure categories and guidance remain. Backend-owned returned failure diagnostics are also contained at provider serialization, including nested batch results; ordinary successful business data and local dataflow retain their existing semantics. Provider errors expose status/category/remediation rather than arbitrary remote messages or IDs. REST execution uses a bounded transport adapter that preserves the actual request while avoiding the inherited HA logger's credential-bearing URL and response-body messages.

Normal diagnostics remain allowlisted. Administrator request debugging remains opt-in, transient and potentially private: it may contain prompts, arguments, results and provider material. Review debug captures and copied logs for private content before sharing. Administrator access to their own configuration remains available.

HA-owned LLM tool failures use the same public error boundary: raised backend errors become source-free guidance, and flagged failure data stays local rather than being forwarded to the provider. Successful tool data keeps its existing meaning. Within EOAI-owned Function Tool, HA tool and Request Rule execution, HA template and Script log records retain their severity, diagnostic category, call site and recognized numeric action position, but omit source, raw exception prose and traceback attachments. This includes undefined-variable warnings from otherwise successful renders. Unrelated Home Assistant tasks keep their normal logging behavior; external integrations and deliberately emitted application logs still require review before sharing.

Ordinary dependency exceptions are contained as well as Home Assistant exceptions, including when a failed exchange is retained in history. Cancellation and process-control exceptions retain their control semantics. Unsupported result leaves use compatibility text while nested private failures retain their provenance; successful siblings remain available. Safe operational logs retain original failure classes and stack locations without exception prose.

Unexpected programming/dependency failures still propagate as source-free runtime exceptions rather than masquerading as ordinary failed tool results. Cancellation history retains a fixed cancellation category without copying the cancellation payload. Rollback checks compare the exact stored configuration and its effective behavior separately, including omitted fields and legacy empty/null Function values.

Template/Script log projection runs at record creation so descendant and dynamically created loggers reach every handler with safe text. The hook chains existing logging factories, activates only in EOAI-owned execution contexts, and leaves unrelated records and logger levels unchanged. Dynamic logger suffixes are omitted because they can contain authored names.

The shared prompt renderer uses that same scoped protection for custom system prompts, current-date/time templates and exposed-entity templates, including previews and cached templates. Undefined-variable warnings and render errors retain their severity and safe diagnostics without copying private source that was absent from the rendered prompt. Rendering results, public error handling and unrelated HA template logs keep their existing semantics.

The same scoped protection covers HA's Scrape coordinator, including DEBUG logging of otherwise unselected HTML. Raw response bodies, exception prose and traceback attachments are omitted before handlers receive records. Refresh duration, success, failure/recovery categories, severity, call sites and extraction diagnostics remain available. Other Home Assistant Scrape tasks keep their normal logging behavior.

EOAI-owned exception log records have a namespace-wide creation boundary, including future descendant loggers. Exception arguments and traceback attachments become immutable source-free diagnostics: error class, fixed category, bounded YAML/JSON line and column, storage remediation and causal code locations. Operation labels, log levels and call sites remain useful. Skill discovery uses the same diagnostic representation for malformed frontmatter, metadata validation and read failures; skipped Skills do not copy ignored private metadata into logs. Authorised editor validation remains detailed and separate from operational logging. The boundary chains existing record factories and leaves other integrations' records unchanged; HA-owned Template/Script/Scrape records retain their scoped protection. Developers must pass exception objects or structured diagnostics rather than interpolating private error prose before logging; architecture tests enforce this convention and inventory parser/export boundaries.


## Caller authorization context

Home Assistant action authorization distinguishes an unbound caller from explicitly bound userless voice/system execution. Actions must receive a caller Context or inherit one from an explicit request binding; missing authority is rejected before dispatch. Native service tools forward their supplied LLM caller Context to this shared boundary. Read-only context lookup does not itself establish mutation authority.

## Completion notifications and Recorder

The `extended_openai_conversation_responses.conversation.finished` event contains only completion metadata: `schema_version: 2`, `agent_id`, `conversation_id`, `status`, and, when applicable, `handled_locally` and `error_type`. Home Assistant Recorder can persist these events unless excluded in its configuration. EOAI does not put user requests, system prompts, transcripts, tool arguments/results, attachments, or request Context objects in them, including when archiving is disabled or a conversation is private.

This intentionally replaces the previous event's `user_input` and `messages` fields. Automations using those fields must be updated to use completion metadata; use the explicitly enabled, ownership-scoped archive for retained conversation text. Upgrading does not delete transcripts already captured by Recorder or by other event consumers. Review existing Recorder history and its retention settings if older completion events contained private material. This does not change Home Assistant's separate service/state recording or deliberately authored logging actions.

## Pending private output

Private export operations belong to the Home Assistant account and authentication lifetime that started them. An account change retires pending private output. Returning to the original account does not revive those exports. Start a new export after returning. Same-user navigation can still complete an explicitly requested export for its original assistant. Retired transfers are cancelled where the current connection permits it; server expiry cleans up otherwise. Debug and effective-request clipboard fallback writes use the same ownership boundary. Files or clipboard writes already published before an account change cannot be revoked by the panel.
