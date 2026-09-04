const action = (page, subsection, target = null) => ({page, subsection, target});

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function providerRuntimeCheck(facts) {
  const runtime = facts.provider_runtime || {};
  const loaded = runtime.client_loaded === true;
  return {
    id: "provider_runtime",
    state: loaded ? "ready" : "error",
    title: "Provider runtime",
    value: `${runtime.provider || "Unknown provider"} · ${runtime.model || "No model selected"}`,
    detail: loaded
      ? facts.can_manage
        ? "API client is loaded. Overview does not make a live provider request; use Diagnostics for an on-demand connection test."
        : "API client is loaded. Overview does not make a live provider request; an administrator can run Diagnostics for a live connection test."
      : "The provider API client is not currently available to this config entry.",
    action: action("usage-maintenance", "diagnostics"),
  };
}

function instructionsCheck(facts) {
  if (facts.prompt_state === "empty") {
    return {
      id: "instructions",
      state: "warning",
      title: "Assistant instructions",
      value: "Empty",
      detail: "No system instructions are configured for this assistant.",
      action: action("assistant", "prompt-context", "prompt-editor"),
    };
  }
  if (facts.prompt_state === "custom") {
    return {
      id: "instructions",
      state: "ready",
      title: "Assistant instructions",
      value: "Customised",
      detail: "This assistant uses customised system instructions.",
      action: action("assistant", "prompt-context", "prompt-editor"),
    };
  }
  return {
    id: "instructions",
    state: "ready",
    title: "Assistant instructions",
    value: "Starter instructions",
    detail: "The built-in starter prompt is in use. Custom instructions are optional.",
    action: action("assistant", "prompt-context", "prompt-editor"),
  };
}

function exposureCheck(facts) {
  const count = facts.exposed_entity_count;
  if (!Number.isFinite(count)) {
    return {
      id: "home_assistant_exposure",
      state: "unknown",
      title: "Home Assistant access",
      value: "Unable to determine",
      detail: "Overview could not count the entities currently exposed to Assist.",
      action: action("capabilities", "home-assistant"),
    };
  }
  return {
    id: "home_assistant_exposure",
    state: count > 0 ? "ready" : "warning",
    title: "Home Assistant access",
    value: `${Number(count).toLocaleString()} ${count === 1 ? "entity" : "entities"} exposed to Assist`,
    detail: count > 0
      ? "These are the entities currently available through Home Assistant's Assist exposure rules."
      : "No entities are currently exposed to Assist. The assistant can still answer non-device questions, but Home Assistant device access will be limited.",
    action: action("capabilities", "home-assistant"),
  };
}

function memoryCheck(facts) {
  const mode = String(facts.memory_mode || "off");
  const off = mode === "off";
  return {
    id: "memory",
    state: off ? "neutral" : "ready",
    title: "Persistent memory",
    value: off ? "Off by choice" : titleCase(mode),
    detail: off
      ? "Persistent memory is optional and is currently disabled."
      : "Persistent memory is enabled for this assistant.",
    action: action("data-memory", "memory-settings"),
  };
}

function knowledgeCheck(facts) {
  const knowledge = facts.knowledge || {};
  if (!knowledge.enabled) {
    return {
      id: "knowledge",
      state: "neutral",
      title: "Knowledge Library",
      value: "Off by choice",
      detail: "Knowledge Library access is optional and is currently disabled.",
      action: action("data-memory", "knowledge"),
    };
  }
  if (knowledge.available === false) {
    return {
      id: "knowledge",
      state: "unknown",
      title: "Knowledge Library",
      value: "Unable to determine",
      detail: "Knowledge Library is enabled, but Overview could not load the stored-source count.",
      action: action("data-memory", "knowledge"),
    };
  }
  const count = Number(knowledge.source_count || 0);
  if (count > 0) {
    return {
      id: "knowledge",
      state: "ready",
      title: "Knowledge Library",
      value: `${count.toLocaleString()} ${count === 1 ? "source" : "sources"}`,
      detail: "Knowledge Library access is enabled and has stored reference material.",
      action: action("data-memory", "knowledge"),
    };
  }
  return {
    id: "knowledge",
    state: "warning",
    title: "Knowledge Library",
    value: "Enabled, no sources",
    detail: "Knowledge Library access is enabled but there are no stored sources to search.",
    action: action("data-memory", "knowledge"),
  };
}

function webSearchCheck(facts) {
  const web = facts.web_search || {};
  if (!web.enabled) {
    return {
      id: "web_search",
      state: "neutral",
      title: "Web Search",
      value: "Off by choice",
      detail: "Hosted Web Search is optional and is currently disabled.",
      action: action("capabilities", "web-skills"),
    };
  }
  if (web.available === false) {
    const needsResponses = web.reason === "requires_responses";
    return {
      id: "web_search",
      state: "warning",
      title: "Web Search",
      value: "Needs attention",
      detail: web.message || "The current provider configuration cannot attach hosted Web Search.",
      action: needsResponses
        ? action("assistant", "basics", "config-api_mode")
        : action("capabilities", "web-skills", "config-web_search"),
    };
  }
  return {
    id: "web_search",
    state: "ready",
    title: "Web Search",
    value: "Available",
    detail: "The current provider/API configuration can attach hosted Web Search when requested.",
    action: action("capabilities", "web-skills"),
  };
}

export function buildSetupHealth(facts = {}) {
  const checks = [
    providerRuntimeCheck(facts),
    instructionsCheck(facts),
    exposureCheck(facts),
    memoryCheck(facts),
    knowledgeCheck(facts),
    webSearchCheck(facts),
  ];
  const errorCount = checks.filter((check) => check.state === "error").length;
  const warningCount = checks.filter((check) => check.state === "warning").length;
  const unknownCount = checks.filter((check) => check.state === "unknown").length;
  const state = errorCount ? "error" : warningCount || unknownCount ? "warning" : "ready";
  return {
    state,
    summary: state === "error" ? "Needs attention" : state === "warning" ? "Review recommended" : "Ready",
    error_count: errorCount,
    warning_count: warningCount,
    unknown_count: unknownCount,
    can_manage: facts.can_manage === true,
    live_provider_tested: facts.live_provider_tested === true,
    checks,
  };
}
