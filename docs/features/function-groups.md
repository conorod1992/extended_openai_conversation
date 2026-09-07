# Function groups

Function groups let an agent keep related configured functions **always available** or **load them only when needed**. They are optional: every existing ungrouped function keeps the previous behaviour and sends its full schema on each model request.

This feature is useful when an agent has many functions whose names, descriptions, parameters, and examples would otherwise consume input on unrelated requests.

## How it works

An on-demand group initially contributes only compact metadata: its stable ID, friendly name, and concise description. The integration adds an internal `load_function_groups` tool containing that catalogue. It does not include member function names, parameter schemas, examples, or execution configuration.

When the model determines that a group is relevant, it can load one or several groups in the normal tool loop:

```json
{"groups": ["calendar", "reminders"]}
```

Loading performs no Home Assistant action. The integration validates the requested IDs, records them for the active conversation, returns a short result, and rebuilds the next provider request with the detailed schemas from those groups. The configured functions still pass through the same backend validation, Home Assistant permissions, exposed-entity restrictions, and execution safeguards as always.

There is no separate classifier request, routing model, embedding lookup, or local keyword matching. The same conversational model chooses a group from its compact semantic description.

## Availability modes and enabled state

- **Always available** sends every individually enabled and currently executable member's complete schema immediately.
- **Load when needed** withholds complete member schemas until the model requests the group.
- **Ungrouped functions** remain always available for backwards compatibility.
- **Disabled group** keeps the group and all of its member settings saved, but none of the group's members are available to the model and an on-demand group cannot be loaded.

The group enabled switch is independent of each Function Tool's own enabled switch. Disabling a group does **not** disable its members individually. When the group is enabled again, only members that are individually enabled become available; individually disabled members stay disabled.

Availability is resolved from the effective runtime state, not merely from saved membership. An enabled on-demand group appears in `load_function_groups` only when it has at least one member that can currently execute and the group loader itself is usable. Individually disabled or otherwise unavailable members are omitted. If the last executable member of a loaded on-demand group becomes unavailable, its loaded state is discarded; if a member later becomes available again, the model must load that group again rather than silently regaining a previously hidden tool.

This also applies to the built-in `load_skill` Function Tool. If it is grouped, the Skills group is loadable only while at least one selected installed Skill can actually be loaded. The live conversation path and the management **Preview** use the same effective availability rules.

Existing groups that were saved before the group enabled switch existed are treated as enabled, so no migration step is required from the user.

A function belongs to at most one group. Group membership is stored separately from the function YAML, so the existing `spec` and `function` format does not change.

## Configure groups in the UI

1. Open **Extended OpenAI** in the Home Assistant sidebar.
2. Select the conversation agent and open **Capabilities → Functions**.
3. Select **Create group**.
4. Enter a friendly name. Review the generated stable group ID.
5. Write a concise description that explains when the capability is useful.
6. Choose **Always available** or **Load when needed**.
7. Search for and select the member functions.
8. Select **Save group**. The group persists immediately.

The overview shows a distinct card for each group, its availability, member count, description, and expandable member list. Search matches groups and functions. Editing a group can move selected functions from another group without editing YAML.

Use the **Enabled** switch on a group card when you want to temporarily make the entire group unavailable without changing member-tool settings. A disabled group remains visible. Enable it again before editing its membership or other group details.

Deleting a group persists immediately and never deletes its functions. They become ungrouped and therefore **Always available**. Deleting a function removes its group assignment. Renaming a function through the editor updates its assignment. Function and group changes do not save or discard an unrelated settings draft.

## Home Assistant actions

Administrators and trusted Home Assistant automations can change group availability without changing member Function Tool state:

```yaml
service: extended_openai_conversation_responses.disable_function_groups
data:
  config_entry: YOUR_CONFIG_ENTRY_ID
  agent_id: conversation.extended_openai_conversation_responses
  function_groups:
    - calendar
```

Use `extended_openai_conversation_responses.enable_function_groups` with the same fields to enable groups again. One action can name several group IDs.

These actions change only group availability. The existing `enable_function_tools` and `disable_function_tools` actions continue to control individual Function Tool state.

## Lifecycle and follow-ups

Loaded groups persist for the same active conversation. For example:

1. “Remind me at 8 to put the bins out” loads **Reminders** and calls the create function.
2. “Actually make that 9” can use the update function without loading **Reminders** again.

A separate or expired conversation starts with no on-demand groups loaded. State is isolated by provider entry, conversation agent, and active conversation key. It is stored explicitly rather than inferred from textual history, so context truncation does not unload a group halfway through a conversation.

Loaded state is intentionally ephemeral and resets when Home Assistant or the integration restarts, when the agent is reconfigured/reloaded, or when its conversation state expires. The model can load the group again naturally after a reset. No separate function-group timeout is added.

If an on-demand group is disabled or loses all executable members while it is loaded, its loaded state is discarded and its members disappear from subsequent model requests. If a provider had already returned a call for a member before the change, Extended OpenAI re-checks the latest persisted tool/group state immediately before execution and rejects a stale disabled call.

The loader does not consume the agent's **Maximum function calls** allowance because it performs no user action. It has a separate hard limit of five loader rounds within one model run, while the existing overall tool-loop bound remains in place.

## Token-use example

**Before**

An agent has 50 configured functions. Every request includes all 50 complete schemas, even a general knowledge question.

**After**

The agent keeps a few general functions always available and creates on-demand groups for Reminders, Conditional Notifications, Deferred Actions, Gmail, and Calendar. A general question sends the general schemas plus the compact group catalogue. A calendar request first loads **Calendar**, after which only the Calendar group's complete schemas are added for the active conversation.

Schema sizes vary, so the integration does not promise an exact token percentage. Diagnostics expose:

- configured function count;
- full configured schemas sent on the latest request;
- available on-demand group count;
- loaded group IDs;
- serialized function-schema character count.

The character count is labelled as serialized size, not a provider token count.

## Compatibility and limitations

- Legacy configurations, imports, exports, and duplicated agents remain valid; existing groups default to enabled.
- Groups, assignments, and group enabled state are included in agent duplication and import/export.
- Responses and Chat Completions use the same integration-owned function loop, so grouping works in both modes when Function Tools are available for the request.
- Built-in Web Search, memory, Knowledge Library, archive, temporary-memory and continuation capabilities are not placed in user Function Groups.
- The built-in `load_skill` Function Tool may be grouped, including in an enabled on-demand group. If Skills are selected for the agent, configuration validation prevents that loader or its group from being disabled and prevents the tool-call budget from being set to zero. Runtime availability additionally requires at least one selected Skill to be currently installed.
- Empty groups, or groups whose members are all currently unavailable, do not appear in the loadable catalogue.
- The first use of an on-demand group may add one model round-trip.
