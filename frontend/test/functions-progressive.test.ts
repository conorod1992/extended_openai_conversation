// @ts-nocheck
import {describe, expect, it} from "vitest";
import {readFile} from "node:fs/promises";
import {refreshAfterRepair} from "../../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";

const source = (name: string) => readFile(new URL(`../../custom_components/extended_openai_conversation_responses/frontend/${name}`, import.meta.url), "utf8");

describe("Functions progressive loading", () => {
  it("keeps editor and HA catalogue implementation off the initial list dependency path", async () => {
    const entry = await source("agent-config-tools.js");
    const list = await source("agent-config-tools-base.js");
    expect(entry).not.toContain('from "./agent-config-native-yaml.js"');
    expect(list).not.toContain('from "./ha-llm-tools.js"');
    expect(list).toContain('import("./agent-config-native-yaml.js")');
    expect(list).toContain('import("./ha-llm-tools.js")');
  });

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
