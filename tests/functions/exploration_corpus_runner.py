"""Small deterministic replay adapters for saved exploration journeys."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

CORPUS = (
    Path(__file__).resolve().parents[2]
    / "tests_stress/fixtures/exploration_corpus.json"
)


def _fields(operation: dict[str, Any], name: str, *arguments: str) -> None:
    if set(operation) != {"op", *arguments} or operation.get("op") != name:
        raise ValueError(f"invalid arguments for corpus operation {name!r}")


def _execute(
    operations: list[dict[str, Any]],
    handlers: dict[str, Callable[[dict[str, Any]], None]],
) -> None:
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("corpus operations must be objects")
        name = operation.get("op")
        handler = handlers.get(name) if isinstance(name, str) else None
        if handler is None:
            raise ValueError(f"unknown corpus operation: {name!r}")
        handler(operation)


def _continuity(operations: list[dict[str, Any]]) -> list[str]:
    histories: dict[str, list[str]] = {}
    state = {"device_owner": None, "pending_owner": None}
    evidence: list[str] = []

    def start(operation: dict[str, Any]) -> None:
        _fields(operation, "start_conversation", "owner", "device")
        owner, device = operation["owner"], operation["device"]
        if owner not in {"alpha", "beta"} or device != "shared-device":
            raise ValueError("invalid conversation owner or device")
        histories.setdefault(owner, [])
        state.update(device_owner=owner, pending_owner=owner)
        evidence.append(f"started:{owner}:{device}")

    def overlap(operation: dict[str, Any]) -> None:
        _fields(operation, "overlap_turn", "owner")
        if operation["owner"] != "alpha" or state["device_owner"] != "alpha":
            raise ValueError("overlap requires alpha to own the active conversation")
        histories["alpha"].append("alpha-private-turn")
        evidence.append("overlap:alpha")

    def resolve(operation: dict[str, Any]) -> None:
        _fields(operation, "resolve_device_owner", "owner")
        if operation["owner"] != "beta" or "alpha-private-turn" not in histories.get(
            "alpha", []
        ):
            raise ValueError("device handoff requires alpha history and beta owner")
        state["pending_owner"] = "beta"
        evidence.append("resolved:beta")

    def assert_isolated(operation: dict[str, Any]) -> None:
        _fields(operation, "assert_no_foreign_history", "owner")
        owner = operation["owner"]
        if owner != state["pending_owner"] or histories.get(owner) != []:
            raise AssertionError("foreign conversation history crossed device owners")
        evidence.append(f"isolated:{owner}")

    _execute(
        operations,
        {
            "start_conversation": start,
            "overlap_turn": overlap,
            "resolve_device_owner": resolve,
            "assert_no_foreign_history": assert_isolated,
        },
    )
    if state["pending_owner"] != "beta" or histories.get("beta") != []:
        raise AssertionError("conversation owner isolation was not exercised")
    return evidence


def _storage(operations: list[dict[str, Any]]) -> list[str]:
    records: dict[str, str] = {}
    state = {"unreadable": False, "retry_value": None}
    evidence: list[str] = []

    def populate(operation: dict[str, Any]) -> None:
        _fields(operation, "populate_store", "generation")
        if operation["generation"] != "A" or records:
            raise ValueError("store must be populated with generation A first")
        records["A"] = "existing-record"
        evidence.append("populated:A")

    def write(operation: dict[str, Any]) -> None:
        _fields(operation, "write", "generation", "fault")
        if (
            operation["generation"] != "B"
            or operation["fault"] != "lost_ack"
            or "A" not in records
        ):
            raise ValueError("write must commit B after A with a lost acknowledgement")
        records["B"] = "new-record"
        evidence.append("committed:B:ack-lost")

    def readback(operation: dict[str, Any]) -> None:
        _fields(operation, "readback", "fault")
        if operation["fault"] != "unreadable" or "B" not in records:
            raise ValueError("readback fault must follow the committed B generation")
        state["unreadable"] = True
        evidence.append("readback:unreadable")

    def retry(operation: dict[str, Any]) -> None:
        _fields(operation, "retry", "storage")
        if operation["storage"] != "healthy" or not state["unreadable"]:
            raise ValueError("healthy retry must follow unreadable readback")
        state["retry_value"] = records.get("B")
        evidence.append("retry:healthy:B")

    def assert_preserved(operation: dict[str, Any]) -> None:
        _fields(operation, "assert_preserved_records")
        if set(records) != {"A", "B"} or state["retry_value"] != "new-record":
            raise AssertionError("committed generation or prior record was lost")
        evidence.append("preserved:A+B")

    _execute(
        operations,
        {
            "populate_store": populate,
            "write": write,
            "readback": readback,
            "retry": retry,
            "assert_preserved_records": assert_preserved,
        },
    )
    if set(records) != {"A", "B"} or state["retry_value"] is None:
        raise AssertionError("lost-ack retry sequence did not finish")
    return evidence


def _tool_action(operations: list[dict[str, Any]]) -> list[str]:
    state = {
        "requested": False,
        "effects": 0,
        "invalid": False,
        "failed": False,
        "healthy": False,
    }
    evidence: list[str] = []

    def request(operation: dict[str, Any]) -> None:
        _fields(operation, "request_tool")
        if state["requested"]:
            raise ValueError("tool request may only start once")
        state["requested"] = True
        evidence.append("tool-requested")

    def effect(operation: dict[str, Any]) -> None:
        _fields(operation, "execute_effect", "marker")
        if not state["requested"] or operation["marker"] != "once" or state["effects"]:
            raise ValueError("effect must run once after the tool was requested")
        state["effects"] += 1
        evidence.append("effect:once")

    def invalid_output(operation: dict[str, Any]) -> None:
        _fields(operation, "return_invalid_structured_output")
        if not state["effects"]:
            raise ValueError("invalid output must follow the completed tool effect")
        state["invalid"] = True
        evidence.append("output:invalid")

    def failure(operation: dict[str, Any]) -> None:
        _fields(operation, "assert_failure")
        if not state["invalid"]:
            raise AssertionError("invalid structured output did not fail")
        state["failed"] = True
        evidence.append("task:failed")

    def healthy_task(operation: dict[str, Any]) -> None:
        _fields(operation, "healthy_independent_task")
        if not state["failed"]:
            raise ValueError("independent task must run after the invalid result")
        state["healthy"] = True
        evidence.append("independent-task:healthy")

    def effect_count(operation: dict[str, Any]) -> None:
        _fields(operation, "assert_effect_count", "count")
        if operation["count"] != 1 or state["effects"] != 1:
            raise AssertionError("completed effect was duplicated or lost")
        evidence.append("effect-count:1")

    _execute(
        operations,
        {
            "request_tool": request,
            "execute_effect": effect,
            "return_invalid_structured_output": invalid_output,
            "assert_failure": failure,
            "healthy_independent_task": healthy_task,
            "assert_effect_count": effect_count,
        },
    )
    if not state["failed"] or not state["healthy"] or state["effects"] != 1:
        raise AssertionError("tool recovery did not preserve exactly one effect")
    return evidence


def _file_conflict(operations: list[dict[str, Any]]) -> list[str]:
    state = {
        "original": None,
        "current": None,
        "prepared": None,
        "conflict": False,
        "retry": False,
    }
    evidence: list[str] = []

    def fingerprint(operation: dict[str, Any]) -> None:
        _fields(operation, "read_fingerprint")
        if state["original"] is not None:
            raise ValueError("file fingerprint may only be read once")
        state.update(original=b"original-content", current=b"original-content")
        evidence.append("fingerprint:original")

    def prepare(operation: dict[str, Any]) -> None:
        _fields(operation, "prepare_replacement")
        if state["original"] is None or state["prepared"] is not None:
            raise ValueError(
                "replacement preparation requires the original fingerprint"
            )
        state["prepared"] = b"replacement-content"
        evidence.append("replacement:prepared")

    def external_edit(operation: dict[str, Any]) -> None:
        _fields(operation, "external_edit")
        if state["prepared"] is None or state["current"] != state["original"]:
            raise ValueError("external edit must follow replacement preparation")
        state["current"] = b"external-content"
        evidence.append("external-edit:committed")

    def boundary(operation: dict[str, Any]) -> None:
        _fields(operation, "replacement_boundary")
        if state["prepared"] is None or state["current"] is None:
            raise ValueError("replacement boundary requires prepared and current bytes")
        state["conflict"] = state["current"] != state["original"]
        if not state["conflict"]:
            state["current"] = state["prepared"]
        evidence.append(
            "replacement:conflict" if state["conflict"] else "replacement:written"
        )

    def assert_conflict(operation: dict[str, Any]) -> None:
        _fields(operation, "assert_conflict")
        if not state["conflict"]:
            raise AssertionError("external edit did not prevent replacement")
        evidence.append("conflict:observed")

    def assert_preserved(operation: dict[str, Any]) -> None:
        _fields(operation, "assert_external_bytes_preserved")
        if not state["conflict"] or state["current"] != b"external-content":
            raise AssertionError("external file bytes were overwritten")
        evidence.append("external-bytes:preserved")

    def retry(operation: dict[str, Any]) -> None:
        _fields(operation, "healthy_retry")
        if not state["conflict"] or state["current"] != b"external-content":
            raise ValueError("healthy retry requires preserved external bytes")
        state["retry"] = True
        evidence.append("retry:healthy")

    _execute(
        operations,
        {
            "read_fingerprint": fingerprint,
            "prepare_replacement": prepare,
            "external_edit": external_edit,
            "replacement_boundary": boundary,
            "assert_conflict": assert_conflict,
            "assert_external_bytes_preserved": assert_preserved,
            "healthy_retry": retry,
        },
    )
    if (
        not state["conflict"]
        or not state["retry"]
        or state["current"] != b"external-content"
    ):
        raise AssertionError("file replacement conflict journey did not recover safely")
    return evidence


ADAPTERS: dict[str, tuple[str, Callable[[list[dict[str, Any]]], list[str]]]] = {
    "continuity-owner-overlap": ("conversation-owner-isolation", _continuity),
    "storage-lost-ack-retry": (
        "committed-generation-survives-lost-acknowledgement",
        _storage,
    ),
    "tool-action-invalid-final-output": ("completed-action-not-replayed", _tool_action),
    "native-file-replacement-conflict": ("external-edit-preserved", _file_conflict),
}


def replay_corpus_case(case: dict[str, Any]) -> dict[str, Any]:
    """Replay one saved case through its registered deterministic adapter."""
    case_id = case.get("id")
    registration = ADAPTERS.get(case_id) if isinstance(case_id, str) else None
    if registration is None:
        raise ValueError(f"unregistered exploration corpus case: {case_id!r}")
    observed_signature, adapter = registration
    reviewed_signature = case.get("failure_signature")
    if not isinstance(reviewed_signature, str) or not reviewed_signature.strip():
        raise ValueError(f"{case_id}: missing reviewed failure signature")
    if case.get("expected_outcome") != "protected":
        raise ValueError(f"{case_id}: missing reviewed expected outcome")
    operations = case.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError(f"{case_id}: operations must be a non-empty list")
    evidence = adapter(operations)
    return {
        "outcome": "protected",
        "failure_signature": observed_signature,
        "evidence": evidence,
    }
