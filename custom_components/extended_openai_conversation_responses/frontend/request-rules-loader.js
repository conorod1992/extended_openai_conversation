let implementation = null;
let loadPromise = null;

export function getRequestRulesModule() {
  return implementation;
}

export async function ensureRequestRulesModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./request-rules-ui-impl.js")
      .then((module) => {
        implementation = module;
        return module;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}
