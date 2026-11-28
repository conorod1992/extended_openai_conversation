"""Strict JSON preserves genuine shared values and rejects invalid data."""

import pytest

from custom_components.extended_openai_conversation_responses.strict_json import (
    check_document_unicode,
    strict_json_loads,
)


def test_decoded_document_rejects_cycles_without_rejecting_shared_fragments():
    fragment = {"title": "Café"}
    document = {"first": fragment, "second": [fragment]}
    check_document_unicode(document)
    fragment["parent"] = document
    with pytest.raises(ValueError, match="circular reference"):
        check_document_unicode(document)


@pytest.mark.parametrize("value", ["1e9999", "-1e9999"])
def test_exponents_that_overflow_are_rejected_as_nonfinite(value):
    with pytest.raises(ValueError, match="Non-finite"):
        strict_json_loads('{"value": ' + value + '}')
