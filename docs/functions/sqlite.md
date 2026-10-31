# SQLite Function Tool

The `sqlite` implementation runs a read-only query against Home Assistant's SQLite Recorder database by default. It is an advanced data-access capability: read-only access prevents writes, but a query can still disclose private history. Prefer a fixed query with narrowly validated model parameters.

## Configuration

```yaml
function:
  type: sqlite
  query: >-
    SELECT s.state
    FROM states s
    JOIN states_meta sm ON s.metadata_id = sm.metadata_id
    WHERE sm.entity_id = :entity_id
    ORDER BY s.last_updated_ts DESC
    LIMIT 10
  parameters:
    entity_id: "{{ entity_id if is_exposed(entity_id) else raise('Entity must be exposed') }}"
  string_parameters: [entity_id]
  max_rows: 100
  timeout: 5
  max_result_bytes: 65536
```

- `query` is a Jinja template. When omitted, the implementation can use the model argument named `query`; unrestricted generated SQL should be used only when that data-access trade-off is acceptable.
- `db_url` optionally chooses a SQLite filesystem path or `file:` URI; it is opened read-only. This Function Tool does not query a MariaDB or PostgreSQL Recorder database.
- `single` requests a single-result form.
- `parameters` maps SQLite named placeholders to Jinja templates. Scalar values are bound separately from SQL. Bind caller-provided values rather than interpolating them into the query. SQL keywords and operators cannot be bound: choose them from an explicit whitelist.
- `string_parameters` optionally lists parameter names whose rendered values must remain strings, including boundary spaces, tabs, and newlines authored in the parameter template. Home Assistant otherwise parses strings such as `None`, `True`, and `[1]` as native values. Unknown names are rejected before SQL execution; other parameters retain native scalar parsing.
- `max_rows` defaults to 1,000 (range 1–10,000), `timeout` to 5 seconds (range 0.1–30), and `max_result_bytes` to 1 MiB (range 1 KiB–4 MiB). Oversized results fail explicitly rather than returning a partial result.

The template receives reserved `exposed_entities`, `is_exposed(entity_id)`, `is_exposed_entity_in_query(query)`, `validate_datetime(value)`, and `raise(message)` context. Caller arguments cannot replace these values. `validate_datetime` accepts exactly `YYYY-MM-DD HH:MM:SS` for the host-local Recorder examples. Use these helpers to validate entity-backed access, but do not rely on string matching as a complete SQL authorization mechanism. Home Assistant's Recorder schema can change between releases; validate queries against your installation. For ordinary recent history, prefer the built-in `get_history` native Function Tool.

Duplicate result-column names are retained by adding numeric suffixes (`id`, `id_2`, ...). Explicit aliases are reserved first, so an existing `id_2` alias is never overwritten. Prefer explicit SQL aliases when stable names matter.
