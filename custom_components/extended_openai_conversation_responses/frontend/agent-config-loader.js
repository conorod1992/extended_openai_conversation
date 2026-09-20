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
        return implementation;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}
