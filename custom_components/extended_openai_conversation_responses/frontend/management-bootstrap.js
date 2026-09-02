const PANEL_TAG = "extended-openai-management-panel";

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
    await import("./management-state-safety.js");
    await import("./management-rendering-performance.js");
    await import("./management-loading-performance.js");
    await import("./management-route-performance.js");
  } catch (err) {
    restore();
    throw err;
  }
}

export {capturePreRegistrationInstallers};
