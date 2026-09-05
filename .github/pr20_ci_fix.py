from pathlib import Path

path = Path("tests/test_responses_api.py")
text = path.read_text()

# This direct transformer test does not provide a real ChatLog, so use a completed
# event without usage rather than the shared helper that exercises usage tracing.
annotation_start = text.index(
    "async def test_responses_annotation_event_after_cited_text_is_observed"
)
annotation_end = text.index("\ndef _function_call(", annotation_start)
annotation = text[annotation_start:annotation_end]
old = "            _completed_event(),\n"
new = (
    '            _event("response.completed", response=SimpleNamespace(usage=None)),\n'
)
if old not in annotation:
    raise SystemExit("annotation completed-event marker not found")
annotation = annotation.replace(old, new, 1)
text = text[:annotation_start] + annotation + text[annotation_end:]

# These direct transformer fixtures previously ended at output-item completion.
# Append the explicit response.completed terminal event now required by PR20.
multiple_start = text.index("async def test_responses_stream_supports_multiple_tool_calls")
multiple_end = text.index(
    "\n\nasync def test_responses_stream_preserves_web_search_and_citations",
    multiple_start,
)
multiple = text[multiple_start:multiple_end]
old = """            )
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
"""
new = """            )
        ]
        + [_event("response.completed", response=SimpleNamespace(usage=None))]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
"""
if old not in multiple:
    raise SystemExit("multiple-tool stream closing marker not found")
multiple = multiple.replace(old, new, 1)
text = text[:multiple_start] + multiple + text[multiple_end:]

search_start = text.index("async def test_responses_stream_preserves_web_search_and_citations")
search_end = text.index(
    "\n\nasync def test_web_search_coexists_with_ha_tool_and_conditional_finalizer",
    search_start,
)
search = text[search_start:search_end]
old = """            _event("response.output_item.done", item=message_item),
        ]
    )
"""
new = """            _event("response.output_item.done", item=message_item),
            _event("response.completed", response=SimpleNamespace(usage=None)),
        ]
    )
"""
if old not in search:
    raise SystemExit("web-search stream closing marker not found")
search = search.replace(old, new, 1)
text = text[:search_start] + search + text[search_end:]

path.write_text(text)
