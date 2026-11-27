import {CONFIG_OWNER_BY_KEY} from "./management-config-owners.js";
const [modelPage, modelSection] = CONFIG_OWNER_BY_KEY.chat_model.split("/");

const action = (page, subsection, target = null) => ({page, subsection, target});

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function providerRuntimeCheck(facts, fallback = {}) {
  const runtime = facts.provider_runtime || {};
  const loaded = runtime.client_loaded === true;
  const provider = runtime.provider || fallback.provider || "Unknown provider";
  const model = runtime.model || fallback.model || "No model selected";
  if (runtime.authentication_rejected) return {
    id: "provider_runtime", state: "error", title: "Provider connection",
    value: "Credentials rejected", detail: "Reauthenticate or replace the API key in Settings > Devices & services.",
    action: action("usage-maintenance", "diagnostics"),
  };
  return {
    id: "provider_runtime",
    state: loaded ? "ready" : "error",
    title: "Provider connection",
    value: `${provider} · ${model}`,
    detail: loaded
      ? facts.can_manage
        ? "The provider connection is available. Overview does not make a live provider request; use Diagnostics for an on-demand connection test."
        : "The provider connection is available. Overview does not make a live provider request; an administrator can run Diagnostics for a live connection test."
      : "The provider connection is not currently available for this assistant. Your provider and model settings are still saved.",
    action: action("usage-maintenance", "diagnostics"),
  };
}


function modelLifecycleCheck(facts) {
  const lifecycle = facts.model_lifecycle || {};
  if (lifecycle.status !== "deprecated") return null;
  const model = lifecycle.model || facts.provider_runtime?.model || "Selected model";
  const shutdown = lifecycle.shutdown_at || null;
  const confirmed = lifecycle.confirmed_unavailable === true;
  const reached = lifecycle.shutdown_reached === true;
  return {
    id: "model_lifecycle",
    state: confirmed ? "error" : "warning",
    title: "Model lifecycle",
    value: confirmed
      ? "Retired model unavailable"
      : reached
        ? "Shutdown date reached"
        : shutdown
          ? `Scheduled for shutdown · ${shutdown}`
          : "Deprecated",
    detail: confirmed
      ? `${model} was rejected by the provider after its announced shutdown date. Choose a different model for this assistant.`
      : lifecycle.lifecycle_note
        || (shutdown
          ? `${model} is deprecated and is scheduled to stop working on ${shutdown}. Choose a replacement before then.`
          : `${model} is deprecated. Choose a replacement before the provider retires it.`),
    action: action(modelPage, modelSection, "config-chat_model"),
  };
}

function functionToolsCheck(facts) {
  const tools = facts.function_tools || {};
  if (tools.unavailable === true) return {
    id: "function_tools", state: "unknown", title: "Function Tools",
    value: "Unable to determine", detail: "Overview could not check the selected assistant's Function Tools.",
    action: action("capabilities", "functions"),
  };
  if (tools.loading === true) return {
    id: "function_tools", state: "unknown", title: "Function Tools",
    value: "Loading…", detail: "Checking the selected assistant's Function Tools.",
    action: action("capabilities", "functions"),
  };
  const invalid = Number(tools.invalid_count || 0);
  const invalidGroups = Number(tools.invalid_group_count || 0);
  const usable = Number(tools.usable_count || 0);
  const enabled = Number(tools.enabled_count || 0);
  if (tools.validation_error && tools.isolatable === false) {
    return {
      id: "function_tools",
      state: "error",
      title: "Function Tools",
      value: "Configuration needs repair",
      detail: "The saved Function Tools need to be repaired together before they can be used.",
      action: action("capabilities", "functions"),
    };
  }
  if (invalid > 0) {
    return {
      id: "function_tools",
      state: "warning",
      title: "Function Tools",
      value: `${invalid} ${invalid === 1 ? "function needs" : "functions need"} repair`,
      detail: `${enabled} ${enabled === 1 ? "Function Tool is" : "Function Tools are"} available. Invalid tools remain unavailable until they are repaired.`,
      action: action("capabilities", "functions"),
    };
  }
  if (invalidGroups > 0) {
    return {
      id: "function_tools",
      state: "warning",
      title: "Function Tools",
      value: `${invalidGroups} Function ${invalidGroups === 1 ? "Group needs" : "Groups need"} repair`,
      detail: `${enabled} Function ${enabled === 1 ? "Tool is" : "Tools are"} available.`,
      action: action("capabilities", "functions"),
    };
  }
  return {
    id: "function_tools",
    state: "ready",
    title: "Function Tools",
    value: `${enabled} ${enabled === 1 ? "function" : "functions"} available`,
    detail: usable
      ? "All configured Function Tools pass validation."
      : "No custom Function Tools are currently configured.",
    action: action("capabilities", "functions"),
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
  if (facts.exposed_entity_count_loading === true) return {
    id: "home_assistant_exposure", state: "unknown", title: "Home Assistant access",
    value: "Loading…", detail: "Counting entities exposed to Assist.",
    action: action("capabilities", "home-assistant"),
  };
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
  const memory = facts.memory || {};
  const mode = memory.mode;
  const off = mode === "off";
  if (memory.repair_required || !off && (memory.failed || memory.keyword_fallback)) return {
    id: "memory", state: "warning", title: "Long-term memory",
    value: memory.repair_required ? "Storage needs repair" : memory.failed ? "Unavailable" : "Keyword fallback",
    action: memory.repair_required ? action("usage-maintenance", "backup-restore") : action("data-memory", "memory-settings"),
  };
  if (off) {
    return {
      id: "memory",
      state: "neutral",
      title: "Long-term memory",
      value: "Off by choice",
      detail: "Long-term memory is optional and is currently disabled.",
      action: action("data-memory", "memory-settings"),
    };
  }
  if (memory.loading === true) {
    return {
      id: "memory",
      state: "unknown",
      title: "Long-term memory",
      value: "Loading…",
      detail: "Loading the current long-term memory status.",
      action: action("data-memory", "memory-settings"),
    };
  }
  if (memory.available === false) {
    return {
      id: "memory",
      state: "unknown",
      title: "Long-term memory",
      value: "Unable to determine",
      detail: "Overview could not load the current long-term memory status.",
      action: action("data-memory", "memory-settings"),
    };
  }
  return {
    id: "memory",
    state: "ready",
    title: "Long-term memory",
    value: titleCase(mode),
    detail: "Long-term memory is enabled for this assistant.",
    action: action("data-memory", "memory-settings"),
  };
}

function knowledgeCheck(facts) {
  const knowledge = facts.knowledge || {};
  if (knowledge.repair_required || knowledge.enabled && knowledge.failed) return {
    id: "knowledge", state: "warning", title: "Knowledge Library",
    value: knowledge.repair_required ? "Storage needs repair" : "Unavailable",
    action: action("usage-maintenance", "backup-restore"),
  };
  if (knowledge.enabled === false) {
    return {
      id: "knowledge",
      state: "neutral",
      title: "Knowledge Library",
      value: "Off by choice",
      detail: "Knowledge Library access is optional and is currently disabled.",
      action: action("data-memory", "knowledge"),
    };
  }
  if (knowledge.loading === true) {
    return {
      id: "knowledge",
      state: "unknown",
      title: "Knowledge Library",
      value: "Loading…",
      detail: "Loading the stored Knowledge source count.",
      action: action("data-memory", "knowledge"),
    };
  }
  if (knowledge.available === false) {
    return {
      id: "knowledge",
      state: "unknown",
      title: "Knowledge Library",
      value: "Unable to determine",
      detail: "Overview could not load the current stored-source count.",
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
    value: knowledge.total_source_count ? "No enabled sources" : "Enabled, no sources",
    detail: "Knowledge tools require at least one enabled source.",
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
  if (web.available !== true) {
    return {
      id: "web_search",
      state: "unknown",
      title: "Web Search",
      value: "Unable to determine",
      detail: "Overview could not determine whether hosted Web Search is available for the current provider configuration.",
      action: action("capabilities", "web-skills"),
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

export function buildSetupHealth(facts = {}, fallback = {}) {
  const unavailable = facts.unavailable === true;
  const loading = !Object.keys(facts).length;
  const primaryCheck = unavailable || loading ? {
    id: "setup_health",
    state: "unknown",
    title: "Setup health",
    value: loading ? "Loading…" : "Unable to determine",
    detail: loading
      ? "Loading the current setup checks."
      : "Overview could not complete all setup checks. Available checks are shown below.",
  } : null;
  const checks = [
    primaryCheck,
    !unavailable && facts.provider_runtime && providerRuntimeCheck(facts, fallback),
    !unavailable && modelLifecycleCheck(facts),
    facts.function_tools && functionToolsCheck(facts),
    !unavailable && facts.prompt_state && instructionsCheck(facts),
    ("exposed_entity_count" in facts || facts.exposed_entity_count_loading) && exposureCheck(facts),
    facts.memory && (facts.memory.mode != null || facts.memory.available === false) && memoryCheck(facts),
    facts.knowledge && (typeof facts.knowledge.enabled === "boolean" || facts.knowledge.available === false) && knowledgeCheck(facts),
    !unavailable && facts.web_search && webSearchCheck(facts),
  ].filter(Boolean);
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
