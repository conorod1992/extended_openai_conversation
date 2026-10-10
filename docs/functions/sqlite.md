# SQLite Function Tool

The `sqlite` implementation runs a read-only query against Home Assistant's SQLite Recorder database by default. It is an advanced data-access capability: read-only access prevents writes, but a query can still disclose private history. Prefer a fixed query with narrowly validated model parameters.

## Configuration

```yaml
function:
  type: sqlite
  query: >-
    SELECT state
    FROM states
    WHERE entity_id = :entity_id
    LIMIT 10
  parameters:
    entity_id: "{{ entity_id if is_exposed(entity_id) else raise('Entity must be exposed') }}"
  max_rows: 100
  timeout: 5
  max_result_bytes: 65536
```

- `query` is a Jinja template. When omitted, the implementation can use the model argument named `query`; unrestricted generated SQL should be used only when that data-access trade-off is acceptable.
- `db_url` optionally chooses a database URL; it is converted to read-only mode.
- `single` requests a single-result form.
- `parameters` maps SQLite named placeholders to Jinja templates. Scalar values are bound separately from SQL. Bind caller-provided values rather than interpolating them into the query. SQL keywords and operators cannot be bound: choose them from an explicit whitelist.
- `string_parameters` optionally lists parameter names whose rendered values must remain strings. Home Assistant otherwise parses strings such as `None`, `True`, and `[1]` as native values. Unknown names are rejected before SQL execution; other parameters retain native scalar parsing.
- `max_rows`, `timeout`, and `max_result_bytes` bound results and execution. Current limits are enforced by configuration validation.

The template receives reserved `exposed_entities`, `is_exposed(entity_id)`, `is_exposed_entity_in_query(query)`, `validate_datetime(value)`, and `raise(message)` context. Caller arguments cannot replace these values. `validate_datetime` accepts exactly `YYYY-MM-DD HH:MM:SS` for the host-local Recorder examples. Use these helpers to validate entity-backed access, but do not rely on string matching as a complete SQL authorization mechanism. Home Assistant's Recorder schema can change between releases; validate queries against your installation. For ordinary recent history, prefer the built-in `get_history` native Function Tool.
