"""Check the conservative process-restart CI classification contract."""

from ci.classify_real_ha_specialists import ALL, classify, matrix_shards


def test_specialist_path_classification() -> None:
    component = "custom_components/extended_openai_conversation_responses/"
    cases = {
        "frontend/src/panel.ts": frozenset(),
        component + "frontend/management.js": frozenset(),
        component + "usage.py": frozenset(),
        component + "delayed_tools.py": frozenset("ab"),
        component + "provider_loop.py": frozenset("b"),
        component + "temporary_memory.py": frozenset("c"),
        component + "__init__.py": ALL,
        component + "new_runtime.py": ALL,
        ".github/workflows/real-ha.yml": ALL,
        "ci/classify_real_ha_specialists.py": ALL,
        "tests_real_ha/process_harness.py": ALL,
    }
    for path, expected in cases.items():
        assert classify([path], pull_request=True) == expected, path

    # --no-renames reports both old and new paths, so a move cannot hide risk.
    assert classify([component + "usage.py", component + "new_runtime.py"], pull_request=True) == ALL
    assert classify([], pull_request=True) == ALL
    assert classify(["frontend/src/panel.ts"], pull_request=False) == ALL


def test_logical_specialists_expand_to_every_parallel_case() -> None:
    assert matrix_shards(frozenset()) == ["parallel-safe", "stateful-serial"]
    assert matrix_shards(frozenset("a"))[-1:] == ["process-restart-a"]
    assert matrix_shards(frozenset("b"))[-2:] == [
        "process-restart-b-active-request", "process-restart-b-immediate-tool"
    ]
    assert matrix_shards(frozenset("c"))[-2:] == [
        "process-restart-c-before-commit", "process-restart-c-after-commit"
    ]
    assert len(matrix_shards(classify([], pull_request=False))) == 7
    assert len(matrix_shards(classify([".github/workflows/real-ha.yml"], pull_request=True))) == 7
