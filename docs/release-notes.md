# Release notes

## 7.0.0 — in preparation

These notes describe the development branch being prepared for 7.0.0, compared with 6.8.3. They do not announce a published release. Request Rules, the Management UI, Function Groups, Skills, memory, and agent backups already existed; this release extends and hardens them.

### New features

- **Additional entity attributes** lets you include selected live attributes for entities already exposed to Assist. Selections follow entity-registry identities across renames and are carried with agent configuration. See [Exposed entity attributes](features/exposed-entity-attributes.md).
- **Quiet Hours** adds a daily satellite volume limit and optional wake-word sound policy, with per-satellite overrides, a schedule entity for automations, and restoration that respects independent device changes. See [Quiet Hours](features/quiet-hours.md).
- **Refreshable model data** supplies exact-model capability and lifecycle information to settings, Request Rules, previews, and live requests. Updates and reset controls apply across the installation and do not replace saved model choices. See [Model data](features/model-data.md).
- **Optional safe Function Tool error recovery** lets the model correct malformed JSON or invalid configured-tool arguments before dispatch, with at most two corrections per request. It is off by default. Permission denials, unavailable or changed tools, and failures after dispatch are not recoverable through this option; actions that may already have run are not automatically repeated. See [Function Tools](functions/index.md).

### Behaviour changes and expanded features

- **Request Rules** support native Home Assistant action sequences, templates, conditions, variables, response actions, and optional continuation to AI after normal local completion. Stop and false conditions retain Home Assistant script behaviour. Rules can be organized and shared; new rules start enabled, while imported rules start disabled. Sensitive actions still need narrow matching and valid caller permissions. See [Request Rules](features/request-rules.md).
- **Management** uses the Extended OpenAI sidebar for conversation-agent settings and feature editors. Related settings share a draft, while dedicated editors save their own content. Revision checks and reconnect recovery help preserve edits and prevent stale saves; inspect the saved state after an uncertain result before retrying. AI Task configuration remains in Home Assistant's integration dialogs. See [Configuration](configuration.md).
- **Existing configuration** preserves saved models and custom prompts. Exact historical stock prompts and native Function Tool schemas receive compatibility updates. An unsupported historical `gpt-5-mini`/`flex` default is corrected to the normal `default` service tier without substituting the saved model. See [Upgrade notes](migration.md#upgrading-this-fork-to-700).
- **Tools, Groups, and Skills** are resolved against current enabled state, dependencies, and permissions. Disabled or unavailable capabilities are withheld; on-demand groups do not enable disabled members. Skill instructions do not grant additional execution permissions. See [Function Groups](features/function-groups.md) and [Skills](features/skills.md).
- **Context management** can use conservative local estimates when provider usage is missing. These estimates never become reported Usage tokens. Optional summaries normally run after the reply; a subsequent turn waits for a pending summary. Live continuity and loaded group/routing state restart fresh after a Home Assistant restart. See [Context management](features/context-management.md).
- **Backup and restore** provide private Full/Custom exports and redacted Shareable Setup exports, section selection, owner mapping where needed, and an explicit choice when restoring a shared provider connection. Private exports can include API credentials and are not encrypted. See [Backup & Restore](features/backup-restore.md).

### Fixes and reliability improvements

- Storage, restore, and configuration updates have stronger validation and recovery boundaries. Damaged stores are preserved behind repair barriers rather than silently replaced with empty data. A validated restore can recover supported state; an external backup may need a clean assistant if the original cannot initialize.
- Interrupted provider continuations use execution-recovery checks to avoid automatically repeating completed or uncertain tool effects. This is bounded recovery protection, not an exactly-once guarantee for arbitrary external systems.
- Home Assistant caller permissions, Assist exposure, Guest restrictions, and delayed-job authorization are checked at their relevant execution boundaries. Voice data mappings do not grant action permissions.
- Provider stream handling and retained history better preserve complete replies and tool-call/result relationships. Failed or cancelled requests retain available provider-reported Usage without adding local context estimates.
- Management saves and restores retain committed outcomes across lost or malformed acknowledgements and refresh failures. Editors recover their own drafts without treating a failed refresh as proof that a save failed.

### Before upgrading

1. Run **Home Assistant 2026.7.0 or newer**, as required by this repository's HACS metadata. Take a Home Assistant backup and a private agent backup before updating. Keep backups secure; agent backups do not include every Home Assistant dependency or global setting.
2. Update the integration through HACS or replace its complete integration directory, then restart Home Assistant. Open **Extended OpenAI** from the sidebar and select each conversation agent. Refresh an already-open browser or Companion App frontend if old controls remain visible.
3. Review saved Function Tools, Groups, selected Skills, Request Rules, and Guest policies. Invalid configurations may require repair instead of being silently dropped. Review imported rules before enabling them, and test commands on harmless devices: **Test request** performs real work.
4. Check the selected provider/model and any routing overrides. Auto mode uses capability data; a custom Chat Completions provider is not automatically Responses-compatible. For Azure deployment aliases, set the underlying model binding. Hosted Web Search requires direct OpenAI Responses support. See [Compatibility](reference/compatibility.md).
5. Review retained-data policies, Voice Identity assignments, and backup choices. Turning memory off does not delete existing records. Hybrid semantic retrieval sends eligible memory text and search queries to the embedding provider; lexical retrieval avoids that path. Request-debug captures are opt-in, administrator-only, and held in memory separately from Usage retention. The conversation-finished event now contains completion metadata instead of transcript text; update automations using its former `user_input` or `messages` fields. Upgrading does not delete older events already saved by Recorder. See [Completion notifications and Recorder](features/credential-security.md#completion-notifications-and-recorder).
6. Test ordinary entity control, a follow-up conversation, local Rules, and any custom tools with the users and satellites that will use them. Check Home Assistant Repairs and [Troubleshooting](troubleshooting.md) if data or configuration is blocked.

Keep the previous Home Assistant backup for rollback. A newer agent export is not a promise that an older integration can read newer configuration or storage formats.

For migration from the original upstream integration, see [Migration](migration.md). Older published release summaries remain available in [GitHub Releases](https://github.com/conorod1992/extended_openai_conversation/releases).
