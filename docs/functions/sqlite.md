# SQLite Function Tool

The `sqlite` implementation runs a read-only query against Home Assistant's SQLite Recorder database by default. It is an advanced data-access capability: read-only access prevents writes, but a query can still disclose private history. Prefer a fixed query with narrowly validated model parameters.

## Configuration

```yaml
function:
  type: sqlite
  query: >-
    SELECT state
    FROM states
    WHERE entity_id = '{{ entity_id }}'
    LIMIT 10
  max_rows: 100
  timeout: 5
  max_result_bytes: 65536
```

- `query` is a Jinja template. When omitted, the implementation can use the model argument named `query`; unrestricted generated SQL should be used only when that data-access trade-off is acceptable.
- `db_url` optionally chooses a database URL; it is converted to read-only mode.
- `single` requests a single-result form.
- `max_rows`, `timeout`, and `max_result_bytes` bound results and execution. Current limits are enforced by configuration validation.

The template receives `exposed_entities`, `is_exposed(entity_id)`, `is_exposed_entity_in_query(query)`, and `raise(message)`. Use these helpers to validate entity-backed access, but do not rely on string matching as a complete SQL authorization mechanism. Home Assistant's Recorder schema can change between releases; validate queries against your installation. For ordinary recent history, prefer the built-in `get_history` native Function Tool.
