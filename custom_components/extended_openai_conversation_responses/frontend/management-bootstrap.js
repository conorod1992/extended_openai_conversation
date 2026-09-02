const PANEL_TAG = "extended-openai-management-panel";
const PROPERTY_REPLAY_PATCHED = Symbol.for("extended-openai.management-property-replay");
const BOOTSTRAP_MODULES = [
  "./management-state-safety.js",
  "./management-rendering-performance.js",
  "./management-loading-performance.js",
  "./management-route-performance.js",
  "./debug-management.js",
];

function preloadBootstrapModules(documentRef = globalThis.document) {
  const head = documentRef?.head;
  if (!head?.append) return;
  for (const specifier of BOOTSTRAP_MODULES) {
    const href = new URL(specifier, import.meta.url).href;
    if (documentRef.querySelector?.(`link[rel="modulepreload"][href="${href}"]`)) continue;
    const link = documentRef.createElement("link");
    link.rel = "modulepreload";
    link.href = href;
    head.append(link);
  }
}

function installPreDefinitionPropertyReplay(constructor) {
  const prototype = constructor?.prototype;
  if (!prototype || prototype[PROPERTY_REPLAY_PATCHED]) return false;
  prototype[PROPERTY_REPLAY_PATCHED] = true;

  const originalConnected = prototype.connectedCallback;
  prototype.connectedCallback = function(...args) {
    // Home Assistant may create an unknown custom-panel element and assign these
    // properties before the module has finished defining the element. Those own
    // properties shadow the class setters after upgrade unless they are replayed.
    for (const name of ["hass", "route"]) {
      if (!Object.prototype.hasOwnProperty.call(this, name)) continue;
      const value = this[name];
      delete this[name];
      this[name] = value;
    }
    return originalConnected?.apply(this, args);
  };
  return true;
}

function capturePreRegistrationInstallers(registry) {
  if (!registry || registry.get?.(PANEL_TAG)) return () => {};

  const hadOwnDefine = Object.prototype.hasOwnProperty.call(registry, "define");
  const hadOwnGet = Object.prototype.hasOwnProperty.call(registry, "get");
  const hadOwnWhenDefined = Object.prototype.hasOwnProperty.call(registry, "whenDefined");
  const previousDefine = registry.define;
  const previousGet = registry.get;
  const previousWhenDefined = registry.whenDefined;
  const nativeDefine = previousDefine.bind(registry);
  const nativeGet = previousGet.bind(registry);
  const nativeWhenDefined = previousWhenDefined.bind(registry);
  const pending = [];
  let restored = false;

  const restoreProperty = (name, value, hadOwn) => {
    if (hadOwn) registry[name] = value;
    else delete registry[name];
  };
  const restore = () => {
    if (restored) return;
    restored = true;
    restoreProperty("define", previousDefine, hadOwnDefine);
    restoreProperty("get", previousGet, hadOwnGet);
    restoreProperty("whenDefined", previousWhenDefined, hadOwnWhenDefined);
  };

  registry.whenDefined = function(name) {
    if (name !== PANEL_TAG) return nativeWhenDefined(name);
    return {
      then(onFulfilled) {
        if (typeof onFulfilled === "function") pending.push(onFulfilled);
        return Promise.resolve(false);
      },
    };
  };

  registry.define = function(name, constructor, options) {
    if (name !== PANEL_TAG) return nativeDefine(name, constructor, options);

    restoreProperty("whenDefined", previousWhenDefined, hadOwnWhenDefined);
    restoreProperty("define", previousDefine, hadOwnDefine);
    registry.get = function(candidate) {
      return candidate === PANEL_TAG ? constructor : nativeGet(candidate);
    };
    try {
      installPreDefinitionPropertyReplay(constructor);
      for (const install of pending.splice(0)) install();
    } finally {
      restoreProperty("get", previousGet, hadOwnGet);
      restored = true;
    }
    return nativeDefine(name, constructor, options);
  };

  return restore;
}

if (typeof customElements !== "undefined") {
  const restore = capturePreRegistrationInstallers(customElements);
  try {
    // Fetch the independent patch modules concurrently, while preserving their
    // deterministic evaluation/installation order below.
    preloadBootstrapModules();
    await import("./management-state-safety.js");
    await import("./management-rendering-performance.js");
    await import("./management-loading-performance.js");
    await import("./management-route-performance.js");
    // Request debugging extends the management panel too. Install that extension
    // before registration so the route cannot depend on a later microtask race.
    await import("./debug-management.js");
  } catch (err) {
    restore();
    throw err;
  }
}

export {
  BOOTSTRAP_MODULES,
  capturePreRegistrationInstallers,
  installPreDefinitionPropertyReplay,
  preloadBootstrapModules,
};
