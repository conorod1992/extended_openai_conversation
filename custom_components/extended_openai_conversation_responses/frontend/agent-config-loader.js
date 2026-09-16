import "./quiet-hours-ui.js";

let implementation = null;
let loadPromise = null;

export function getAgentConfigModule() {
  return implementation;
}

export async function ensureAgentConfigModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./agent-config-native-yaml.js")
      .then((module) => {
        implementation = module;
        return module;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}
