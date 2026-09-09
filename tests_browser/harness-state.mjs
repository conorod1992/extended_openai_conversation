const KEY = "extended-openai-browser-harness-state-v3";
const clone = (value) => structuredClone(value);
const now = () => new Date().toISOString();

function freshState() {
  return {
    agent: {
      entry_id: "entry-1", subentry_id: "agent-1", title: "Jarvis", provider: "OpenAI",
      model: "gpt-5-mini", function_count: 1, function_group_count: 1, memory_mode: "Manual",
      memory_count: 1, knowledge_source_count: 0, archive_enabled: true,
      guest_mode: {state: "inactive", has_home_assistant_exclusions: true},
    },
    scopes: [{scope_id: "user:test-user", scope_type: "user", display_name: "Test User", is_current_user: true, memory_count: 1, conversation_count: 2}],
    configuration: {
      title: "Jarvis", revision: 7,
      config: {
        chat_model: "gpt-5-mini", api_mode: "responses", max_tokens: 1200,
        max_function_calls_per_conversation: 8, function_tool_error_recovery: true,
        continue_conversation: "never",
        functions: [{spec: {name: "baseline_tool", description: "Baseline browser fixture Function Tool", parameters: {type: "object", properties: {}}}, function: {type: "script", sequence: []}, enabled: true}],
        function_groups: [{id: "baseline-group", name: "Baseline group", description: "Baseline browser fixture Function Group", loading_mode: "on_demand", functions: ["baseline_tool"], guest_allowed: false, enabled: true}],
      },
      options: {
        api_mode: [{value: "responses", label: "Responses"}, {value: "chat_completions", label: "Chat Completions"}],
        continue_conversation: [{value: "never", label: "Never"}, {value: "always", label: "Always"}],
        reasoning_effort: [{value: "low", label: "Low"}, {value: "medium", label: "Medium"}, {value: "high", label: "High"}],
        memory_retrieval_mode: [{value: "lexical", label: "Lightweight lexical"}, {value: "hybrid", label: "Hybrid semantic"}],
      },
      defaults: {}, model_capabilities: {}, local_handling: {supported: true, intents: [], pipeline_conflicts: []},
    },
    memories: [{memory_id: "memory-1", scope_id: "user:test-user", content: "Baseline browser fixture memory", category: "general", source: "manual", created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-01T12:00:00Z"}],
    requestRules: {
      revision: 3, defaults: {word_forms: true, wording_alternatives: true, fuzzy: false, fuzzy_threshold: 90}, wording_groups: [], diagnostics: {},
      rules: [{id: "rule-1", name: "Baseline rule", enabled: true, phrases: ["baseline route"], match_type: "contains", action_type: "model_routing", action: {model: "gpt-5-mini", reasoning_effort: "", scope: "request", reset: false, success_response: "Updated"}, matching_behavior: "defaults", matching: {word_forms: true, wording_alternatives: true, fuzzy: false, fuzzy_threshold: 90}, order: 0}],
    },
    toolYamls: {baseline_tool: "spec:\n  name: baseline_tool\n  description: Baseline browser fixture Function Tool\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n"},
    nextMemoryId: 2, nextRuleId: 2, failedConfigurationOnce: false,
  };
}

function load() {
  try { const raw = localStorage.getItem(KEY); if (raw) return JSON.parse(raw); } catch (_err) {}
  const state = freshState(); localStorage.setItem(KEY, JSON.stringify(state)); return state;
}

export function createStateBackend({partialOverview = false, failConfigurationOnce = false} = {}) {
  let state = load();
  const save = () => localStorage.setItem(KEY, JSON.stringify(state));
  const counts = () => {
    state.agent.function_count = state.configuration.config.functions?.length || 0;
    state.agent.function_group_count = state.configuration.config.function_groups?.length || 0;
    state.agent.memory_count = state.memories.length;
    state.scopes[0].memory_count = state.memories.filter((m) => m.scope_id === state.scopes[0].scope_id).length;
  };
  const tools = () => ({functions: clone(state.configuration.config.functions || []), function_groups: clone(state.configuration.config.function_groups || []), references: {}});
  const normalizeRules = () => state.requestRules.rules.forEach((r, i) => { r.order = i; });
  const parseTool = (yaml) => {
    const text = String(yaml || "");
    const name = text.match(/^\s*name:\s*([^\n#]+)/m)?.[1]?.trim();
    const description = text.match(/^\s*description:\s*([^\n#]+)/m)?.[1]?.trim() || "Browser Function Tool";
    const type = (text.match(/(?:^|\n)function:\s*\n([\s\S]*)/m)?.[1] || "").match(/^\s*type:\s*([^\n#]+)/m)?.[1]?.trim() || "script";
    if (!name) return {valid: false, errors: [{message: "spec.name is required"}]};
    const config = {spec: {name, description, parameters: {type: "object", properties: {}}}, function: {type, sequence: []}, enabled: true};
    state.toolYamls[name] = text; save();
    return {valid: true, errors: [], name, type, config};
  };
  const backup = () => JSON.stringify({fixture_format: 1, title: state.configuration.title, created_at: now(), state: clone(state)}, null, 2);
  const inspect = (text) => {
    const doc = JSON.parse(text); if (!doc?.state?.configuration) throw new Error("Invalid browser fixture backup");
    const s = doc.state;
    return {title: doc.title || s.configuration.title, summary: {created_at: doc.created_at || now(), integration_version: "browser-fixture", request_rules: s.requestRules?.rules?.length || 0, persistent_memories: s.memories?.length || 0, temporary_memories: 0, knowledge_sources: 0, archive_sessions: 0, archive_turns: 0, usage_runs: 0, usage_requests: 0, guest_mode_scheduled: false}};
  };

  async function call(message) {
    const key = `${message.section || ""}/${message.action || ""}`;
    if (key === "usage/summary") return {today: {total_tokens: 1234}, month: {total_tokens: 5678}, lifetime: {total_tokens: 9999}};
    if (key === "conversations/settings") return {archive_enabled: true, archive_retention_days: 30, archive_model_search_enabled: false};
    if (key === "knowledge/list") { if (partialOverview) throw new Error("Knowledge fixture unavailable"); return {sources: []}; }
    if (key === "scopes/catalog") return {scopes: clone(state.scopes)};
    if (key === "guest_mode/get") return {config: {}, state: "inactive", legacy_policy: false};
    if (key === "service_catalog/get") return {services: {}};

    if (key === "configuration/get") return clone(state.configuration);
    if (key === "configuration/validate") return {valid: true, errors: {}, model_capabilities: {}};
    if (key === "configuration/update" || key === "configuration/save") {
      if (failConfigurationOnce && !state.failedConfigurationOnce) { state.failedConfigurationOnce = true; save(); throw new Error("Fixture rejected configuration save once"); }
      state.configuration = {...state.configuration, title: message.title, revision: state.configuration.revision + 1, config: clone(message.config)};
      state.agent.title = message.title; state.agent.model = message.config.chat_model; counts(); save();
      return clone(state.configuration);
    }

    if (key === "memories/list") return {memories: clone(state.memories.filter((m) => !message.scope_id || m.scope_id === message.scope_id)), total: state.memories.length};
    if (key === "memories/add") {
      const memory = {memory_id: `memory-${state.nextMemoryId++}`, scope_id: message.scope_id, content: message.content, category: message.category || "general", source: "manual", created_at: now(), updated_at: now()};
      state.memories.push(memory); counts(); save(); return {memory: clone(memory)};
    }
    if (key === "memories/update") {
      const memory = state.memories.find((m) => m.memory_id === message.memory_id && m.scope_id === message.scope_id); if (!memory) throw new Error("Memory not found");
      memory.content = message.content; memory.category = message.category || "general"; memory.updated_at = now(); save(); return {memory: clone(memory)};
    }
    if (key === "memories/delete") { state.memories = state.memories.filter((m) => !(m.memory_id === message.memory_id && m.scope_id === message.scope_id)); counts(); save(); return {deleted: true}; }

    if (key === "request_rules/list") return clone(state.requestRules);
    if (key === "request_rules/create") { state.requestRules.rules.push({...clone(message.rule), id: `rule-${state.nextRuleId++}`}); normalizeRules(); state.requestRules.revision++; save(); return {revision: state.requestRules.revision}; }
    if (key === "request_rules/update") { const i = state.requestRules.rules.findIndex((r) => r.id === message.rule_id); if (i < 0) throw new Error("Request Rule not found"); state.requestRules.rules[i] = {...clone(message.rule), id: message.rule_id}; normalizeRules(); state.requestRules.revision++; save(); return {revision: state.requestRules.revision}; }
    if (key === "request_rules/delete") { state.requestRules.rules = state.requestRules.rules.filter((r) => r.id !== message.rule_id); normalizeRules(); state.requestRules.revision++; save(); return {revision: state.requestRules.revision}; }
    if (key === "request_rules/move") { const i = state.requestRules.rules.findIndex((r) => r.id === message.rule_id), j = message.direction === "up" ? i - 1 : i + 1; if (i >= 0 && j >= 0 && j < state.requestRules.rules.length) [state.requestRules.rules[i], state.requestRules.rules[j]] = [state.requestRules.rules[j], state.requestRules.rules[i]]; normalizeRules(); state.requestRules.revision++; save(); return {revision: state.requestRules.revision}; }
    if (key === "request_rules/defaults") { state.requestRules.defaults = clone(message.defaults); state.requestRules.revision++; save(); return {revision: state.requestRules.revision}; }
    if (key === "request_rules/wording_groups") { state.requestRules.wording_groups = clone(message.wording_groups); state.requestRules.revision++; save(); return {revision: state.requestRules.revision}; }

    if (key === "tools/starter") return {yaml: "spec:\n  name: browser_tool\n  description: Browser Function Tool\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n"};
    if (key === "tools/built_in_catalog") return {functions: []};
    if (key === "tools/serialize") return {yaml: state.toolYamls[message.tool?.spec?.name] || ""};
    if (key === "tools/validate_yaml") return parseTool(message.yaml);
    if (key === "tools/save") {
      const tool = clone(message.tool), list = state.configuration.config.functions || [], original = message.original_name;
      const i = list.findIndex((t) => t.spec?.name === (original || tool.spec?.name)); if (i >= 0) list[i] = tool; else list.push(tool);
      if (original && original !== tool.spec?.name) { for (const g of state.configuration.config.function_groups || []) g.functions = (g.functions || []).map((n) => n === original ? tool.spec.name : n); delete state.toolYamls[original]; }
      counts(); save(); return tools();
    }
    if (key === "tools/delete") { state.configuration.config.functions = (state.configuration.config.functions || []).filter((t) => t.spec?.name !== message.name); for (const g of state.configuration.config.function_groups || []) g.functions = (g.functions || []).filter((n) => n !== message.name); delete state.toolYamls[message.name]; counts(); save(); return tools(); }
    if (key === "tools/set_enabled") { const tool = (state.configuration.config.functions || []).find((t) => t.spec?.name === message.name); if (tool) tool.enabled = message.enabled; save(); return tools(); }
    if (key === "tools/save_group") { const group = clone(message.group), list = state.configuration.config.function_groups || [], original = message.original_id || group.id; for (const g of list) if (g.id !== original) g.functions = (g.functions || []).filter((n) => !(group.functions || []).includes(n)); const i = list.findIndex((g) => g.id === original); if (i >= 0) list[i] = group; else list.push(group); counts(); save(); return tools(); }
    if (key === "tools/delete_group") { state.configuration.config.function_groups = (state.configuration.config.function_groups || []).filter((g) => g.id !== message.group_id); counts(); save(); return tools(); }
    if (key === "tools/validate_current") return {valid: true, errors: []};

    if (key === "backup/create") return {json: backup(), filename: "browser-fixture-full-backup.json"};
    if (key === "backup/inspect") return inspect(message.document);
    if (key === "backup/restore") { const doc = JSON.parse(message.document); if (!doc?.state?.configuration) throw new Error("Invalid browser fixture backup"); state = clone(doc.state); counts(); save(); return {restored: true}; }

    return ({"conversations/list": {sessions: []}, "conversations/active": {active: []}, "usage/daily": {days: []}, "usage/runs": {runs: []}, "usage/retention": {}})[key] ?? {};
  }

  counts();
  return {call, state: () => clone(state), reset: () => { state = freshState(); save(); return clone(state); }, agent: () => clone(state.agent), scopes: () => clone(state.scopes)};
}
