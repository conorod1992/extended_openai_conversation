# Sharing and private backup security

## Shareable Setup, configuration exports and Rule Sharing

**Shareable exports exclude designated credentials and unclassified custom content by default, and check included content for recognized secret patterns. Custom content you choose to include may still contain secrets. Review the summary and file before sharing, especially Function Tools, scripts and templates.**

Restricted sharing is the default. It includes explicitly classified behavior settings, while omitting arbitrary prompts, identifiers, custom tools, scripts, templates, schemas, Rule text/actions and unknown future configuration. The original saved setup is unchanged. The file includes a value-free omission manifest and a generic title; omitted tools/rules are absent from the imported setup and cannot execute there. Recipients may need to select a model and add omitted custom units separately. Restricted sharing is intentionally a partial setup, not a complete copy of custom behavior.

Choose **Include custom content** to make reviewed sharing useful for legitimate custom definitions. Before download, EOAI displays the exact artifact, its omission summary and an acknowledgement for included custom content. Designated credentials and unsupported typed units remain excluded. Credential-bearing tools/rules are omitted as coherent units rather than silently modifying executable code. Safe custom definitions, templates and schemas can remain portable. Inspect every included custom unit; unknown query names, URL paths, free-form strings, scripts and novel encodings may contain secrets that automation cannot recognize.

No combination of key-name rules, regular expressions or entropy checks can distinguish all arbitrary secrets from legitimate content. Even an innocent-looking short string can be a credential. EOAI therefore promises structural exclusions in restricted mode, not that every export is universally secret-free. Reviewed inclusion is a deliberate author decision, not a certification.

New sharing documents and Rule Packs use version 2. Historical version 1 imports remain supported. All production sharing writers use the same policy. Unsupported future fields are omitted by default; adding a provider-specific pattern is only an additional defense.

## Complete private backups

New full backups use payload version 6; new custom private transfers use version 3. Their private configuration and Rule fields use a versioned safe-YAML envelope inside the JSON/ZIP transport. This preserves opaque YAML source, typed data, inline credentials, signed URLs, and literal text/dictionaries resembling old redaction markers. Those markers are not interpreted in new private formats. Existing backup versions 1–5 and transfer version 1 keep their original redaction/recovery semantics.

Private backups preserve the selected agent's configuration and saved data. Provider connection data, including credentials and credential-bearing URLs, is captured when configuration is included. In the restore interface, **Also restore the provider connection** is a separate explicit choice: the connection can be shared by multiple agents. It is applied after agent restoration, and a concurrent connection edit prevents overwriting it. A connection-update failure does not undo an already committed agent restore; the interface reports that distinction and asks you to import/preview again. Ordinary restoration does not silently replace a shared connection.

The package cannot recreate external HA scripts, secrets, files or remote services. Preserve those dependencies with an appropriate Home Assistant backup. Tokens and signed URLs are preserved exactly, but expiration or provider revocation can still require attention. Expired temporary memories retain their existing restore policy; transient locks/caches and active requests are not restoration data.

**These files are private and unencrypted. Keep them private and store them securely.** ZIP compression, base64 and checksums do not provide confidentiality or prove who authored a file. Shareable export is the route for public sharing; a private backup is the route for complete credential-bearing restoration.

Optional password protection is a follow-up, not part of this implementation. It needs a reviewed authenticated container, a password KDF calibrated for supported HA hardware, bounded KDF/file parameters, authentication before archive parsing, wrong-password/corruption tests and careful plaintext staging cleanup. Encryption will wrap the same lossless payload rather than redact it. No ad-hoc ZIP password scheme or custom streaming cryptography is introduced here.

## Errors, logs and support information

Template and arbitrary backend exception prose is excluded from public tool errors; safe failure categories and guidance remain. Provider errors expose status/category/remediation rather than arbitrary remote messages or IDs. REST execution uses a bounded transport adapter that preserves the actual request while avoiding the inherited HA logger's credential-bearing URL messages.

Normal diagnostics remain allowlisted. Administrator request debugging remains opt-in, transient and potentially private: it may contain prompts, arguments, results and provider material. Do not treat debug captures or copied logs as shareable setups. Administrator access to their own configuration remains available.
