# Native Function Tools

Native Function Tools are implemented by Extended OpenAI. Open **Extended OpenAI → Capabilities → Functions → Add Function Tool → Insert Built-in Function** to insert the current editable preset into the YAML editor. The catalogue, labels, and schemas in that UI are authoritative; presets may evolve.

## Built-in operations

| Function name | Purpose |
|---|---|
| `execute_service` | Run a list of Home Assistant service calls on Assist-exposed targets. The built-in schema caps each invocation at 20 calls. |
| `execute_service_single` | Run one Home Assistant service call with top-level `domain`, `service`, and `service_data` arguments. |
| `send_broadcast` | Send a spoken message to a destination or, with `whole_home`, all eligible Assist Satellites except the origin. Busy satellites are queued. |
| `add_automation` | Register an automation from a complete YAML configuration. Review this permission carefully and monitor the integration's automation-registration event. |
| `get_history` | Retrieve history for Assist-exposed entities over an optional ISO 8601 time range. |
| `get_energy` | Read Home Assistant energy dashboard configuration and preferences. |
| `get_statistics` | Retrieve long-term statistics for statistic IDs and a required time range. Entity-backed IDs must be exposed; external integration statistics are also supported. |
| `get_user_from_user_id` | Return the current authenticated Home Assistant user's display name; no user ID is supplied by the model. |

## Service execution

`execute_service` accepts a list. Each item has `domain`, `service`, and `service_data`:

```yaml
- spec:
    name: execute_service
    description: Execute Home Assistant services on exposed targets.
    parameters:
      type: object
      properties:
        list:
          type: array
          items:
            type: object
            properties:
              domain: {type: string}
              service: {type: string}
              service_data: {type: object}
            required: [domain, service, service_data]
      required: [list]
  function:
    type: native
    name: execute_service
```

Include an authorized target such as `entity_id`, `device_id`, `area_id`, `floor_id`, or `label_id` in `service_data`, together with fields required by the selected Home Assistant service. The schema allows service-specific keys because valid data varies by service. Home Assistant exposure and permission checks still apply. Do not assume the model may control every entity or call every service.

`execute_service_single` uses the same Home Assistant checks with one call:

```yaml
function:
  type: native
  name: execute_service_single
```

Use the built-in preset to retain the current parameter schema.

## History and statistics

`get_history` requires `entity_ids` and accepts optional `start_time`, `end_time`, `include_start_time_state`, `significant_changes_only`, `minimal_response`, and `no_attributes`. Defaults and maximum entity counts are defined by the active preset/runtime. Only request data needed for the answer.

`get_statistics` requires `statistic_ids`, `start_time`, and `end_time`. Optional `period` values currently include `5minute`, `hour`, `day`, `week`, `month`, and `year`; `types` can select supported statistic values, and `units` maps Home Assistant unit classes to unit names. Use the current preset for the full schema.

## Automation creation and Broadcast

`add_automation` can create real Home Assistant automations. Restrict access to a trusted agent, use a complete valid configuration, and review what was registered. Do not grant this capability casually.

`send_broadcast` takes `message` and may take `destination` or `whole_home`. A whole-home announcement excludes the originating satellite. Broadcast behavior and delivery targets are also available as a separate Home Assistant action; see [Services and actions](../services.md).

## Availability

A Function Tool must be individually enabled and reachable through its Function Group to be offered for a request. **Load when needed** groups make their full schemas available after the model loads the group. Native tools do not bypass Home Assistant's Assist exposure or authorization checks. See [Function groups](../features/function-groups.md) and [Request debugging](../features/request-debugging.md).
