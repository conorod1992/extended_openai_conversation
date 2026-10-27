"""Small Management classifications shared by admission and response timing.

These predicates do not authorize work, acquire gates or validate payloads.
Handlers retain those contracts, including the final revision check after awaits.
Frontend effects remain local to each bundle and are checked for consistency.
"""

from typing import Any

TOOL_MUTATIONS = frozenset(
    {"save", "set_enabled", "delete", "save_group", "delete_group", "ha_add"}
)


def is_tool_mutation(section: Any, action: Any) -> bool:
    """Identify Tool/Group writers without interpreting malformed request fields.

    Membership selects the existing optional-revision preflight and timing path.
    It does not imply exclusive maintenance ownership or early authorization.
    """
    return section == "tools" and isinstance(action, str) and action in TOOL_MUTATIONS
