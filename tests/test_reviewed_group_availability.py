"""Omitting private tools cannot promote safe tools out of their availability groups."""



from tests.test_function_groups import _group, _tool


def test_restricted_export_still_reports_omitted_groups():
    from custom_components.extended_openai_conversation_responses.sharing import (
        project_configuration,
    )

    projected, security = project_configuration(
        {
            "functions": [{**_tool("held_tool"), "enabled": True}],
            "function_groups": [_group("held", ["held_tool"], enabled=False)],
        },
    )
    assert projected["functions"] == []
    assert projected["function_groups"] == []
    assert any(item["field"] == "function_groups" for item in security["omitted"])
