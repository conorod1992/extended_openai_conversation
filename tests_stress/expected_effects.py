"""Independent expected state and effects, advanced only by requested operations."""

from dataclasses import dataclass, field


@dataclass
class ExpectedEffects:
    memories: dict[str, dict[str, str]] = field(default_factory=dict)
    knowledge: dict[str, str] = field(default_factory=dict)
    rules: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    temporary: dict[str, dict[str, str]] = field(default_factory=dict)
    guest_active: bool = False
    tool_enabled: bool = True
    exposed: bool = False
    max_tokens: int = 600
    effects: list[tuple[str, str, dict]] = field(default_factory=list)

    async def assert_stores(self, memory, knowledge, rules, temporary, users):
        for user in users:
            items = await memory.async_list(user, limit=100)
            assert {
                item.memory_id: item.content for item in items
            } == self.memories.get(user, {})
            items = await temporary.async_active(
                f"user:{user}", owner_scope_id=f"user:{user}"
            )
            assert {
                item.memory_id: item.content for item in items
            } == self.temporary.get(user, {})
        sources = await knowledge.async_list()
        assert {source["source_id"] for source in sources} == set(self.knowledge)
        for source_id, content in self.knowledge.items():
            assert (await knowledge.async_get(source_id)).content == content
        actual = rules.snapshot()["rules"]
        assert {item["id"]: item["name"] for item in actual} == {
            key: value[0] for key, value in self.rules.items()
        }
        for item in actual:
            name, phrase, marker = self.rules[item["id"]]
            assert item["name"] == name
            assert item["phrases"] == [phrase]
            assert item["match_type"] == "equals"
            assert item["action_type"] == "local_action"
            assert item["action"]["actions"] == [
                {"action": "chaos_probe.record", "data": {"message": marker}}
            ]
            assert item["action"]["success_response"] == (
                "Local effect " + marker.removeprefix("rule effect ")
            )
