# Function types and examples

This page is a reference for the custom-function implementations supported by the integration.

For an introduction, start with [Custom functions](index.md).

## `native`

Native functions are implemented by the integration itself.

Built-in native capabilities include service execution, automation creation, and entity-history retrieval. The exact default Functions configuration is managed by the integration and can be inspected from the agent options.

A native implementation names the integration function to execute:

```yaml
function:
  type: native
  name: execute_service
```

Use native functions when the integration already provides the operation you need rather than rebuilding it as a script or REST function.

## `script`

A script function executes a Home Assistant action sequence and can interpolate model-provided arguments.

Example: add an item to Home Assistant's shopping list.

```yaml
- spec:
    name: add_shopping_item
    description: Add an item to the Home Assistant shopping list.
    parameters:
      type: object
      properties:
        item:
          type: string
          description: Item to add.
      required:
        - item
  function:
    type: script
    sequence:
      - service: shopping_list.add_item
        data:
          name: "{{ item }}"
```

This is a good pattern when Home Assistant already exposes the underlying action and the model only needs a friendly tool interface.

### Returning action response data

When a Home Assistant action supports response data, store it in `_function_result` so the result can be returned to the model.

For example, a calendar function can call `calendar.get_events`, put the service response in `_function_result`, and let the model summarize the returned events.

## `template`

Template functions return a value produced by a Home Assistant template.

```yaml
- spec:
    name: get_house_mode
    description: Get the current household mode.
    parameters:
      type: object
      properties: {}
  function:
    type: template
    value_template: >-
      The house mode is {{ states('input_select.house_mode') }}.
```

Template functions are useful for small calculated or combined values that do not require executing an action.

## `rest`

REST functions request an HTTP endpoint and can process the response with templates.

Use this when the assistant needs data from an API that Home Assistant does not already expose cleanly.

```yaml
- spec:
    name: get_example_data
    description: Get data from an example API.
    parameters:
      type: object
      properties: {}
  function:
    type: rest
    resource: https://example.com/api/status
    value_template: "{{ value_json }}"
```

### Security considerations

Prefer fixed trusted endpoints. Be cautious about allowing the model to construct arbitrary URLs, headers, credentials, or request bodies.

## `scrape`

Scrape functions retrieve a web page and extract selected elements.

They are appropriate for stable pages that do not provide a usable API, but they are inherently more fragile than an API because page markup can change.

```yaml
- spec:
    name: get_site_status
    description: Read the public status displayed on a website.
    parameters:
      type: object
      properties: {}
  function:
    type: scrape
    resource: https://example.com/status
    sensor:
      - name: status
        select: ".status"
    value_template: "{{ status }}"
```

Do not use scraping for private pages that require credentials unless you fully understand how those credentials and responses are handled.

## `composite`

Composite functions chain multiple function implementations.

This is useful when one operation must collect data, transform it, and return a model-friendly result.

A composite can, for example:

1. run a Home Assistant action
2. store the response
3. use a template to reduce the response to the fields the model actually needs

Keeping returned data small can reduce token usage and improve model reliability.

## `sqlite`

SQLite functions query Home Assistant's SQLite database in read-only mode.

They are powerful but advanced.

### Prefer constrained queries

The safest pattern is a query that you define yourself, with the model supplying only bounded parameters such as an entity ID.

Conceptually:

```yaml
- spec:
    name: get_last_state_change
    description: Get the most recent state change for an exposed entity.
    parameters:
      type: object
      properties:
        entity_id:
          type: string
      required:
        - entity_id
  function:
    type: sqlite
    query: >-
      {%- if is_exposed(entity_id) -%}
        SELECT datetime(s.last_updated_ts, 'unixepoch', 'localtime') AS last_updated
        FROM states s
        INNER JOIN states_meta sm ON s.metadata_id = sm.metadata_id
        WHERE sm.entity_id = '{{ entity_id }}'
        ORDER BY s.last_updated_ts DESC
        LIMIT 1
      {%- else -%}
        {{ raise('entity_id should be exposed.') }}
      {%- endif -%}
```

The exact Recorder schema can change between Home Assistant versions, so database queries should be tested against your installation.

### Model-generated SQL

Allowing the model to generate an unrestricted SQL query is flexible but should not be treated as a secure way to restrict data access.

A read-only database connection prevents modification, but it does not guarantee that a generated query reads only exposed entities or only information you intended to disclose.

Use generated SQL only when you accept that trade-off. For ordinary history questions, prefer the integration's built-in history function.

## `delay`

`delay` is a reserved parameter that can defer execution.

A function schema can expose hours, minutes, and seconds as non-negative integers. The integration can then execute the function after the requested delay.

For normal reminders or time-based automation, consider whether a Home Assistant automation, timer, calendar event, or dedicated reminder system is a better fit before relying on delayed tool execution.
