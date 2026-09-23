// @ts-nocheck
import {describe, expect, it} from "vitest";
import {refreshAfterRepair} from "../../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";

describe("Functions progressive loading", () => {
  it("clears the old draft before one authoritative route reload after repair", async () => {
    const calls: string[] = [];
    const panel = {
      _agentId: "agent-a",
      _clearConfigDraft: () => calls.push("clear"),
      _loadAgents: async (id: string) => { calls.push(`agents:${id}`); },
      _loadSection: async () => { throw new Error("duplicate route reload"); },
      _toast: (message: string) => calls.push(`toast:${message}`),
    };
    await refreshAfterRepair(panel, "Repaired");
    expect(calls).toEqual(["clear", "agents:agent-a", "toast:Repaired"]);
  });
});
