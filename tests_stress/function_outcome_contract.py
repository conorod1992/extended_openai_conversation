"""Independent result shapes shared by real backends and enclosing workflows."""

import json

BACKENDS = (
    "template",
    "script",
    "rest",
    "scrape",
    "sqlite",
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "composite",
)
BUSINESS_VALUES = (
    None,
    False,
    0,
    "",
    [],
    {},
    {"status": "error", "error": "business data"},
)


def backend_configuration(kind, root, url):
    path = str(root / "outcome.txt")
    if kind == "template":
        return {"type": kind, "value_template": "{{ payload }}", "parse_result": True}
    if kind == "script":
        return {
            "type": kind,
            "sequence": [
                {"action": "outcome_probe.read", "response_variable": "business"},
                {"variables": {"_function_result": "{{ business.payload }}"}},
            ],
        }
    if kind == "rest":
        return {"type": kind, "resource": url + "/value"}
    if kind == "scrape":
        return {
            "type": kind,
            "resource": url + "/html",
            "sensor": [{"select": ".probe", "name": "value"}],
        }
    if kind == "sqlite":
        return {
            "type": kind,
            "db_url": str(root / "outcome.db"),
            "query": "SELECT value FROM probes",
            "single": True,
        }
    if kind == "bash":
        return {
            "type": kind,
            "command": "printf '%s' '{{ payload_json }}'",
            "allow_unsafe_shell": True,
            "cwd": str(root),
        }
    if kind == "read_file":
        return {"type": kind, "path": path, "allow_dir": [str(root)]}
    if kind == "write_file":
        return {
            "type": kind,
            "path": path,
            "content": "{{ payload_json }}",
            "allow_dir": [str(root)],
        }
    if kind == "edit_file":
        return {
            "type": kind,
            "path": path,
            "old_text": "START",
            "new_text": "{{ payload_json }}",
            "allow_dir": [str(root)],
        }
    if kind == "composite":
        return {
            "type": kind,
            "sequence": [
                {
                    "type": "composite",
                    "sequence": [backend_configuration("template", root, url)],
                }
            ],
        }
    raise AssertionError(kind)


def expected_business_result(kind, value, root):
    serialized = json.dumps(value)
    if kind in {"template", "script", "composite"}:
        return value
    if kind in {"rest", "scrape"}:
        return serialized
    if kind == "sqlite":
        return {"value": serialized}
    if kind == "bash":
        return {"exit_code": 0, "stdout": serialized}
    if kind == "read_file":
        return {"content": serialized, "size": len(serialized.encode())}
    if kind == "write_file":
        return {
            "success": True,
            "path": str(root / "outcome.txt"),
            "bytes_written": len(serialized.encode()),
        }
    if kind == "edit_file":
        return {"success": True, "path": str(root / "outcome.txt"), "replacements": 1}
    raise AssertionError(kind)


def expected_consumer_result(kind, value, root, workflow):
    # Request Rule captures explicitly decode received JSON text. Other callers
    # preserve REST/scrape text, including an HTTP error body's business fields.
    if workflow == "request_rule" and kind in {"rest", "scrape"}:
        return value
    if workflow == "provider" and kind in {"rest", "scrape"}:
        return json.dumps(value, separators=(",", ":"))
    return expected_business_result(kind, value, root)
