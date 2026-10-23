# Sharing and private backup security

## Shareable Setup, configuration exports and Rule Sharing

**Shareable exports exclude designated credentials and unclassified custom content by default, and check included content for recognized secret patterns. Custom content you choose to include may still contain secrets. Review the summary and file before sharing, especially Function Tools, scripts and templates.**

Restricted sharing is the default. It includes explicitly classified behavior settings, while omitting arbitrary prompts, identifiers, custom tools, scripts, templates, schemas, Rule text/actions and unknown future configuration. The original saved setup is unchanged. The file includes a value-free omission manifest and a generic title; omitted tools/rules are absent from the imported setup and cannot execute there. Recipients may need to select a model and add omitted custom units separately. Restricted sharing is intentionally a partial setup, not a complete copy of custom behavior.

Choose **Include custom content** to make reviewed sharing useful for legitimate custom definitions. Before download, EOAI displays the exact artifact, its omission summary and an acknowledgement for included custom content. Designated credentials and unsupported typed units remain excluded. Credential-bearing tools/rules are omitted as coherent units rather than silently modifying executable code. Safe custom definitions, templates and schemas can remain portable. Inspect every included custom unit; unknown query names, URL paths, free-form strings, scripts and novel encodings may contain secrets that automation cannot recognize.

No combination of key-name rules, regular expressions or entropy checks can distinguish all arbitrary secrets from legitimate content. Even an innocent-looking short string can be a credential. EOAI therefore promises structural exclusions in restricted mode, not that every export is universally secret-free. Reviewed inclusion is a deliberate author decision, not a certification.

Schema property definitions remain structural, but defaults, examples, enum values and constants are checked as instance data. Sensitive property classification follows composed schemas and bounded local references. Unresolved or recursive sensitive references cause the coherent tool to be omitted rather than being fetched or guessed safe. This does not certify arbitrary custom annotations or source strings as secret-free.

Static `$ref` targets can be checked through bounded local pointers. Sensitive `$dynamicRef` and `$recursiveRef` scopes cannot be established by that check, so the containing tool is omitted from reviewed sharing. Non-sensitive references remain portable, and all three reference URI forms are checked for recognized query credentials and user information, including relative references. The accepted singular `example` annotation is checked as instance data too. These sharing restrictions do not change the live tool or private backup.

Only the configured Function's actual parameter schema receives structural exemptions. Script variables, Rule action data and other instance payloads stay data even when they contain keys such as `functions`, `spec` and `parameters`. Credential-bearing units are omitted from sharing; private backups retain the complete original values.

New sharing documents and Rule Packs use version 2. Historical version 1 imports remain supported. All production sharing writers use the same policy. Unsupported future fields are omitted by default; adding a provider-specific pattern is only an additional defense.

## Complete private backups

New full backups use payload version 6; new custom private transfers use version 3. Their private configuration and Rule fields use a versioned safe-YAML envelope inside the JSON/ZIP transport. This preserves opaque YAML source, typed data, inline credentials, signed URLs, and literal text/dictionaries resembling old redaction markers. Those markers are not interpreted in new private formats. Existing backup versions 1–5 and transfer version 1 keep their original redaction/recovery semantics.

Private backups preserve the selected agent's configuration and saved data. Provider connection data, including credentials and credential-bearing URLs, is captured when configuration is included. In the restore interface, **Also restore the provider connection** is a separate explicit choice: the connection can be shared by multiple agents. It is applied after agent restoration, and a concurrent connection edit prevents overwriting it. A connection-update failure does not undo an already committed agent restore; the interface reports that distinction and asks you to import/preview again. Ordinary restoration does not silently replace a shared connection.

The package cannot recreate external HA scripts, secrets, files or remote services. Preserve those dependencies with an appropriate Home Assistant backup. Tokens and signed URLs are preserved exactly, but expiration or provider revocation can still require attention. Expired temporary memories retain their existing restore policy; transient locks/caches and active requests are not restoration data.

Selective restore preserves authoritative unselected configuration verbatim. Private backup and recovery-journal inspection validate a separate effective projection without replacing the stored configuration, including valid and quarantined Function YAML. Internal raw-source provenance is not an import-file flag; historical public backup formats retain their prior semantics.

**These files are private and unencrypted. Keep them private and store them securely.** ZIP compression, base64 and checksums do not provide confidentiality or prove who authored a file. Shareable export is the route for public sharing; a private backup is the route for complete credential-bearing restoration.

Optional password protection is a follow-up, not part of this implementation. It needs a reviewed authenticated container, a password KDF calibrated for supported HA hardware, bounded KDF/file parameters, authentication before archive parsing, wrong-password/corruption tests and careful plaintext staging cleanup. Encryption will wrap the same lossless payload rather than redact it. No ad-hoc ZIP password scheme or custom streaming cryptography is introduced here.

## Errors, logs and support information

Template and arbitrary backend exception prose is excluded from public tool errors; safe failure categories and guidance remain. Backend-owned returned failure diagnostics are also contained at provider serialization, including nested batch results; ordinary successful business data and local dataflow retain their existing semantics. Provider errors expose status/category/remediation rather than arbitrary remote messages or IDs. REST execution uses a bounded transport adapter that preserves the actual request while avoiding the inherited HA logger's credential-bearing URL and response-body messages.

Normal diagnostics remain allowlisted. Administrator request debugging remains opt-in, transient and potentially private: it may contain prompts, arguments, results and provider material. Do not treat debug captures or copied logs as shareable setups. Administrator access to their own configuration remains available.

HA-owned LLM tool failures use the same public error boundary: raised backend errors become source-free guidance, and flagged failure data stays local rather than being forwarded to the provider. Successful tool data keeps its existing meaning. Within EOAI-owned Function Tool, HA tool and Request Rule execution, HA template and Script log records retain their severity, diagnostic category, call site and recognized numeric action position, but omit source, raw exception prose and traceback attachments. This includes undefined-variable warnings from otherwise successful renders. Unrelated Home Assistant tasks keep their normal logging behavior; external integrations and deliberately emitted application logs still require review before sharing.

Ordinary dependency exceptions are contained as well as Home Assistant exceptions, including when a failed exchange is retained in history. Cancellation and process-control exceptions retain their control semantics. Unsupported result leaves use compatibility text while nested private failures retain their provenance; successful siblings remain available. Safe operational logs retain original failure classes and stack locations without exception prose.

Unexpected programming/dependency failures still propagate as source-free runtime exceptions rather than masquerading as ordinary failed tool results. Cancellation history retains a fixed cancellation category without copying the cancellation payload. Rollback checks compare the exact stored configuration and its effective behavior separately, including omitted fields and legacy empty/null Function values.

Template/Script log projection runs at record creation so descendant and dynamically created loggers reach every handler with safe text. The hook chains existing logging factories, activates only in EOAI-owned execution contexts, and leaves unrelated records and logger levels unchanged. Dynamic logger suffixes are omitted because they can contain authored names.
