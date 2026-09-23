# Home Assistant actions and services

Extended OpenAI exposes Home Assistant actions for explicit automations and integrations. They are separate from model-facing Function Tools. Find the current field selectors in **Developer Tools → Actions**; action schemas can evolve with the integration.

| Action | Purpose |
|---|---|
| `process` | Send text through an agent's normal request pipeline without routing through `conversation.process` again. |
| `query_image` | Send images and a prompt to a configured provider/model. |
| `change_config` | Validate and update provider connection settings for an integration entry. |
| `broadcast` | Queue or send a spoken announcement to selected Assist Satellites. |
| `call_function` | Invoke an enabled configured Function Tool from a Request Rule action sequence. |
| `download_skill`, `reload_skills` | Install a repository Skill or reload manually changed Skills. |
| `memory_list`, `memory_delete`, `memory_clear` | Inspect and manage persistent memories in the calling user's scope. Deletion and clearing are destructive. |
| `enable_function_tools`, `disable_function_tools` | Change individual Function Tool availability for an agent. |
| `enable_function_groups`, `disable_function_groups` | Change Function Group availability without changing member tool state. |
| `guest_mode_update`, `guest_mode_disable` | Schedule or end Guest Mode for an agent. |
| `enable_quiet_hours`, `disable_quiet_hours` | Enable or disable the saved Quiet Hours schedule. |

## Process a request

`process` sends text through the selected Extended OpenAI agent's normal processing pipeline. It does not bypass Request Rules, Guest Mode, memory, continuity, model routing, tools, or response cleanup. It can perform real Home Assistant actions; it is not a dry run.

```yaml
action: extended_openai_conversation_responses.process
data:
  text: "Turn off the kitchen light"
  agent_id: conversation.example_agent
response_variable: result
```

The action also accepts `conversation_id`, `device_id`, `satellite_id`, and `language` where relevant. `agent_id` can be omitted when only one integration agent is available.

## Update provider settings

`change_config` accepts the config entry and any connection fields to update, such as `api_key`, `base_url`, `api_version`, `organization`, `skip_authentication`, or `api_provider`. The updated connection is validated before it is saved; failed validation leaves the existing settings in place. Prefer selecting the config entry from the action UI rather than copying an ID.

## Request image analysis

`query_image` takes a `config_entry`, `prompt`, and `images` array; the optional `model`, `api_mode`, and `max_tokens` are shown in the action UI. Use a provider and model that support the selected vision request. Local image paths must satisfy Home Assistant's external-directory access requirements.

## Broadcast

`broadcast` sends a TTS announcement to announcement-capable Assist Satellites. Choose targets by entity, device, area, floor, or label, or set `whole_home`. Busy satellites can queue the message until its configured TTL expires. Broadcast must be enabled in the Extended OpenAI management UI.

## Skills actions

Download an example Skill with:

```yaml
action: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
```

Normal releases use the integration-version tag. `source_ref` can select a branch, tag, or commit for development and testing. Downloads refresh the Skill catalogue automatically. After manually editing Skill files, call `reload_skills`.

## Administrative actions

The memory, Function Tool/Group, Guest Mode, and Quiet Hours actions are explicit Home Assistant actions. They are not automatically exposed to the model as Function Tools. For available fields and required confirmations, use each action's selector and description in Developer Tools → Actions. Avoid wiring destructive memory actions to an untrusted automation trigger.
