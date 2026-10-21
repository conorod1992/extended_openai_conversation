"""Shared integration conversation IDs, including explicit privacy namespaces."""

from uuid import uuid4

GUEST_CONTINUITY_NAMESPACE = "guest"
GUEST_CONVERSATION_ID_PREFIX = "extended-openai-guest-"


def create_conversation_id(agent_id: str, namespace: str | None = None) -> str:
    """Create an ID that continuity can recognize after an ownership replacement."""
    prefix = "extended-openai-" + (f"{namespace}-" if namespace is not None else "")
    return f"{prefix}{agent_id}-{uuid4().hex}"
