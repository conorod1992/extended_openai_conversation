# Native Function Tools

Native Function Tools are implemented by Extended OpenAI. Open **Extended OpenAI → Capabilities → Functions → + Add → Function Tool**, then choose **Insert Built-in Function** to insert the current editable preset into the YAML editor. The catalogue, labels, and schemas in that UI are authoritative; presets may evolve.

## Built-in operations

| Function name | Purpose |
|---|---|
| `execute_service` | Run one or more Home Assistant service calls on Assist-exposed targets. The built-in schema caps each invocation at 20 calls. |
| `send_broadcast` | Send a spoken message to a destination or, with `whole_home`, all eligible Assist Satellites except the origin. Busy satellites are queued. |
| `add_automation` | Register an automation from a complete YAML configuration. Review this permission carefully and monitor the integration's automation-registration event. |
| `get_history` | Retrieve history for Assist-exposed entities over an optional ISO 8601 time range. |
| `get_energy` | Read Home Assistant energy dashboard configuration and preferences. |
| `get_statistics` | Retrieve long-term statistics for statistic IDs and a required time range. Entity-backed IDs must be exposed; external integration statistics are also supported. |
| `get_user_from_user_id` | Return the current authenticated Home Assistant user's display name; no user ID is supplied by the model. |

## Service execution

For new configurations, insert **Execute services**. Use a one-item list for a single action. Each item has `domain`, `service`, and `service_data`:

```yaml
spec:
  name: execute_service
  description: Execute Home Assistant services on exposed targets.
  strict: false
  parameters:
    type: object
    properties:
      list:
        type: array
        maxItems: 20
        items:
          type: object
          properties:
            domain: {type: string}
            service: {type: string}
            service_data: {type: object, additionalProperties: true}
          required: [domain, service, service_data]
    required: [list]
function:
  type: native
  name: execute_service
```

Include an authorized target such as `entity_id`, `device_id`, `area_id`, `floor_id`, or `label_id` in `service_data`, together with fields required by the selected Home Assistant service. The schema explicitly allows service-specific keys because valid data varies by service. Keep `strict: false` to preserve this open-ended object with Responses. Each invocation accepts at most 20 actions. Home Assistant exposure and permission checks still apply. Do not assume the model may control every entity or call every service.

### Legacy and advanced single-service implementation

`execute_service_single` remains supported in existing tools and custom YAML. It uses top-level `domain`, `service`, and `service_data` arguments and returns a single result object. It applies the same Home Assistant checks as **Execute services**.

```yaml
function:
  type: native
  name: execute_service_single
```

Keep the existing schema and tool name when editing, importing, or restoring one of these tools. Scheduled jobs continue to use their saved tool definitions.

## History and statistics

`get_history` requires `entity_ids` and accepts optional `start_time`, `end_time`, `include_start_time_state`, `significant_changes_only`, `minimal_response`, and `no_attributes`. Defaults and maximum entity counts are defined by the active preset/runtime. Only request data needed for the answer.

`get_statistics` requires `statistic_ids`, `start_time`, and `end_time`. Optional `period` values currently include `5minute`, `hour`, `day`, `week`, `month`, and `year`; `types` can select supported statistic values, and `units` maps Home Assistant unit classes to unit names. Use the current preset for the full schema.

History and hourly/5-minute Statistics reads allow 1,000 database rows, including helper reads. Daily, weekly, monthly and yearly Statistics allow up to 25,000 underlying database rows across the whole request, so hourly inputs can be reduced by Home Assistant into a small aggregate result. All Statistics results remain limited to 1,000 aggregate records. The shared byte limits remain 256,000 raw text bytes, 8,000 characters per state/attribute cell, and 24,000 UTF-8 bytes in the serialized result.

Oversized requests fail explicitly without returning a partial series. Reduce the number of IDs or the number of requested calendar periods. Home Assistant expands coarse-period dates to calendar boundaries: shortening dates within the same year does not reduce an annual query's work. A coarser period can reduce the output, but not the underlying hourly work. Queries, Home Assistant aggregation/conversion and size checking run in Recorder's executor. SQL limits bound raw reads before conversion; final record and byte limits apply after aggregation.

## Automation creation and Broadcast

`add_automation` can create real Home Assistant automations, but **requires an active, authenticated Home Assistant administrator** as the request's user. A userless request (including a typical satellite request without a linked HA user), an unauthenticated request or a non-administrator cannot use this function, even when the tool is configured. Restrict access to a trusted agent, use a complete valid configuration, and review what was registered. Do not grant this capability casually.

Home Assistant resolves `!secret` values from the configured `automations.yaml` location. The written YAML and automation-registration event retain the references.

`send_broadcast` takes `message` and may take `destination` or `whole_home`. A whole-home announcement excludes the originating satellite. The Broadcast shortcut passes the composed message literally: a leading word such as `that` is **not** removed automatically. Broadcast behavior and delivery targets are also available as a separate Home Assistant action; see [Services and actions](../services.md).

## Availability

A Function Tool must be individually enabled and reachable through its Function Group to be offered for a request. **Load when needed** groups make their full schemas available after the model loads the group. Native tools do not bypass Home Assistant's Assist exposure or authorization checks. See [Function groups](../features/function-groups.md) and [Request debugging](../features/request-debugging.md).

### Service batch failure behavior

`execute_service` runs actions sequentially. If the batch completes, it returns one result per action in input order. In ordinary execution, individual Home Assistant service failures, including CONTROL-permission denials during authorization of an action, return an `error` result and later authorized actions can still run. Errors raised during preparation or validation, such as invalid or unexposed targets, abort the remaining batch. Inspect every returned result for partial failure; earlier successful actions are not rolled back even if a later error aborts the batch. When **Correct safe tool-call errors automatically** is enabled, or when the caller requires execution failures to propagate as exceptions (such as a Request Rule script), backend execution failures also raise and stop subsequent actions. This does not retry failed effects or bypass permissions.
