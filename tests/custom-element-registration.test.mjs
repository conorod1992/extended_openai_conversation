import assert from "node:assert/strict";

const frontend = (name, evaluation) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}?evaluation=${evaluation}`,
  import.meta.url,
);
const constructors = new Map();
const attempts = new Map();

globalThis.window = {
  location: {pathname: "/extended-openai/assistant/basics"},
  addEventListener() {},
  removeEventListener() {},
};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = {querySelector: () => null}; }
};
globalThis.customElements = {
  get(name) { return constructors.get(name); },
  define(name, constructor) {
    attempts.set(name, (attempts.get(name) || 0) + 1);
    if (constructors.has(name)) throw new Error(`Duplicate definition: ${name}`);
    constructors.set(name, constructor);
  },
  whenDefined(name) { return constructors.has(name) ? Promise.resolve() : Promise.reject(new Error(name)); },
};

let logs = 0;
const warn = console.warn;
const error = console.error;
console.warn = () => { logs++; };
console.error = () => { logs++; };
try {
  for (const [moduleName, tag, exportName] of [
    ["management-panel.js", "extended-openai-management-panel", "ExtendedOpenAIManagementPanel"],
    ["debug-panel.js", "extended-openai-debug-panel", "ExtendedOpenAIDebugPanel"],
  ]) {
    const first = await import(frontend(moduleName, "first"));
    assert.equal(constructors.get(tag), first[exportName], `${tag} registers on first startup`);
    const second = await import(frontend(moduleName, "second"));
    assert.notEqual(second[exportName], first[exportName], "the module was evaluated again");
    assert.equal(constructors.get(tag), first[exportName], "the original constructor remains registered");
    assert.equal(attempts.get(tag), 1, "a second evaluation does not call define");
  }
} finally {
  console.warn = warn;
  console.error = error;
}
assert.equal(logs, 0, "duplicate evaluation stays silent");
