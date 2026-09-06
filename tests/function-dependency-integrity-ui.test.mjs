import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const bootstrapSource = await readFile(frontend("management-bootstrap.js"), "utf8");
assert.match(bootstrapSource, /management-function-dependencies\.js/);

const dependencyModule = await import(frontend("management-function-dependencies.js"));

const calls = [];
class FakePanel {
  constructor() {
    this._configData = {revision: "revision-1", config: {functions: []}};
    this._sectionCache = new Map([
      ["agent-1|capabilities/request-rules", {function_catalog: ["stale"]}],
    ]);
    this._eocSectionCacheTimes = new Map([
      ["agent-1|capabilities/request-rules", Date.now()],
    ]);
  }

  async _call(section, action, extra = {}) {
    calls.push({section, action, extra});
    return action === "validate_current"
      ? {valid: true}
      : {revision: `revision-${calls.length + 1}`};
  }

  _invalidateAfterMutation() {}
}

const registry = {
  get(name) {
    return name === "extended-openai-management-panel" ? FakePanel : undefined;
  },
  whenDefined() {
    return Promise.resolve();
  },
};

assert.equal(dependencyModule.installFunctionDependencyIntegrity(registry), true);
assert.equal(dependencyModule.installFunctionDependencyIntegrity(registry), false);

const panel = new FakePanel();
const first = await panel._call("tools", "save", {tool: {spec: {name: "one"}}});
assert.equal(calls[0].extra.revision, "revision-1");
assert.equal(first.revision, "revision-2");
assert.equal(panel._configData.revision, "revision-2");

await panel._call("tools", "set_enabled", {name: "one", enabled: false});
assert.equal(calls[1].extra.revision, "revision-2");
assert.equal(panel._configData.revision, "revision-3");

panel._invalidateAfterMutation("agent-1", "tools", "set_enabled");
assert.equal(
  panel._sectionCache.has("agent-1|capabilities/request-rules"),
  false,
  "Function Tool mutations must invalidate the Request Rules function catalogue",
);
assert.equal(
  panel._eocSectionCacheTimes.has("agent-1|capabilities/request-rules"),
  false,
);

panel._sectionCache.set("agent-1|capabilities/request-rules", {rules: ["old-order"]});
panel._eocSectionCacheTimes.set("agent-1|capabilities/request-rules", Date.now());
panel._invalidateAfterMutation("agent-1", "request_rules", "move");
assert.equal(
  panel._sectionCache.has("agent-1|capabilities/request-rules"),
  false,
  "moving a Request Rule must invalidate its cached ordering",
);

await panel._call("tools", "validate_current", {});
assert.equal(calls[2].extra.revision, undefined, "read-only tool calls need no revision");
assert.equal(panel._configData.revision, "revision-3");
