"""Canonical policy for configured Function Tool names and implementations."""

from __future__ import annotations

import re

from .built_in_functions import BUILT_IN_FUNCTION_PRESETS
from .const import FUNCTION_GROUP_LOADER_TOOL_NAME
from .conversation_archive import archive_tools
from .knowledge import KNOWLEDGE_TOOL_NAMES
from .memory import MEMORY_TOOL_NAMES
from .temporary_memory import TEMPORARY_MEMORY_TOOL_NAMES

FUNCTION_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# These names are owned by integration-generated tools. Configured Function Tools
# must never claim them, even while the corresponding capability is disabled,
# because enabling that capability later would otherwise create a runtime collision.
RESERVED_FUNCTION_TOOL_NAMES = frozenset(
    {
        *MEMORY_TOOL_NAMES,
        *TEMPORARY_MEMORY_TOOL_NAMES,
        *KNOWLEDGE_TOOL_NAMES,
        *(tool["spec"]["name"] for tool in archive_tools()),
        FUNCTION_GROUP_LOADER_TOOL_NAME,
        "guest_mode_restrict",
        "set_continue_conversation",
    }
)

# Keep native validation tied to the same catalogue that populates the management UI.
NATIVE_FUNCTION_IMPLEMENTATIONS = frozenset(
    preset["implementation"] for preset in BUILT_IN_FUNCTION_PRESETS
)
