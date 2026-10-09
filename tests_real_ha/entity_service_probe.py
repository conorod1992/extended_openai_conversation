"""Register effect probes through HA's genuine entity-service dispatcher."""

from functools import partial

from homeassistant.core import HassJob
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.service import entity_service_call


class ProbeEntity(Entity):
    _attr_should_poll = False

    def __init__(self, hass, entity_id):
        self.hass = hass
        self.entity_id = entity_id

    @property
    def available(self):
        state = self.hass.states.get(self.entity_id)
        return state is not None and state.state != "unavailable"

    async def async_request_call(self, coro):
        return await coro


def register_entity_service_probe(hass, domain, service, handler):
    def entities():
        return {
            state.entity_id: ProbeEntity(hass, state.entity_id)
            for state in hass.states.async_all(domain)
        }

    async def dispatch(_entity, call):
        return await handler(call)

    hass.services.async_register(
        domain, service,
        partial(entity_service_call, hass, entities, HassJob(dispatch)),
    )
