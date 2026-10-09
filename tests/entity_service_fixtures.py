"""Entity-service registrations for authorization test doubles."""

from functools import partial
from types import SimpleNamespace

from homeassistant.helpers import service


def registered_entity_service():
    return SimpleNamespace(
        job=SimpleNamespace(target=partial(service.entity_service_call, None, {}))
    )
