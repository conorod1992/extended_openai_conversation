// @ts-nocheck
import {describe, expect, it} from "vitest";
import {refreshAfterRepair} from "../../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";

describe("Functions progressive loading", () => {
  it("preserves unrelated edits using the repair acknowledgement without a configuration reread", async () => {
    const calls: string[] = [];
    const saved = {revision:"new", config:{functions:[{spec:{name:"repaired"}}], function_groups:[]}};
    let cached;
    const panel = {
      _agentId:"agent-a", _draftAgentId:"agent-a",
      _configData:{revision:"old", title:"Saved", function_repair:{invalid_tools:[{name:"broken"}]}, config:{prompt:"Saved prompt", functions:[], function_groups:[]}},
      _draft:{prompt:"Unsaved prompt", functions:[], function_groups:[]}, _draftTitle:"Unsaved title",
      _clearConfigDraft: () => { throw new Error("repair must not clear unrelated edits"); },
      _call:async () => { throw new Error("repair must reconcile its acknowledgement without rereading configuration"); },
      _rememberCleanConfiguration: data => { cached = structuredClone(data); },
      _syncConfigDirty: () => {},
      _loadAgents:async (id:string) => { calls.push(`agents:${id}`); },
      _loadSection:async () => { throw new Error("duplicate route reload"); },
      _toast:(message:string) => calls.push(`toast:${message}`),
    };
    await refreshAfterRepair(panel, "Repaired", saved);
    expect(panel._draft.prompt).toBe("Unsaved prompt");
    expect(panel._draftTitle).toBe("Unsaved title");
    expect(panel._configData.config.prompt).toBe("Saved prompt");
    expect(panel._draft.functions).toEqual(saved.config.functions);
    expect(panel._configData.revision).toBe("new");
    expect(cached.function_repair).toBeUndefined();
    expect(cached.config.functions).toEqual(saved.config.functions);
    expect(calls).toEqual(["agents:agent-a", "toast:Repaired"]);
  });
});
