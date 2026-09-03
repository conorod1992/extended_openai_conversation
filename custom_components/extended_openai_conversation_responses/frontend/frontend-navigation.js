export const NAVIGATION = [
  {id: "overview", label: "Overview", path: "/extended-openai/overview", sections: []},
  {id: "guide", label: "Guide", path: "/extended-openai/guide", sections: []},
  {id: "assistant", label: "Assistant", path: "/extended-openai/assistant/basics", sections: [
    {id: "basics", label: "Basics", description: "Name the assistant and choose its everyday model and response behavior."},
    {id: "model-responses", label: "Model & responses", description: "Fine-tune supported model response controls."},
    {id: "conversation", label: "Conversation", description: "Control how recent conversations are carried into later requests."},
    {id: "prompt-context", label: "Prompt & context", description: "Manage instructions and live Home Assistant context."},
    {id: "voice", label: "Voice", description: "Choose identity and data scope behavior for voice devices."},
    {id: "speech", label: "Speech", description: "Clean assistant responses before they are spoken."},
  ]},
  {id: "capabilities", label: "Capabilities", path: "/extended-openai/capabilities/home-assistant", sections: [
    {id: "home-assistant", label: "Home Assistant & local handling", description: "Review Home Assistant access and choose which simple commands should stay local."},
    {id: "web-skills", label: "Web search & Skills", description: "Configure current-information search and installed instruction sets."},
    {id: "request-rules", label: "Request Rules", description: "Create fast local commands and route AI requests by phrase."},
    {id: "functions", label: "Functions", description: "Manage custom Function Tools and on-demand groups."},
    {id: "guest-mode", label: "Guest Mode", description: "Limit what visitors can see, use, and remember."},
  ]},
  {id: "data-memory", label: "Data & Memory", path: "/extended-openai/data-memory/memory-settings", sections: [
    {id: "memory-settings", label: "Memory settings", description: "Control long-term, temporary, retrieval, and shared memory behavior."},
    {id: "memories", label: "Memories", description: "Review durable and automatically expiring memories."},
    {id: "knowledge", label: "Knowledge Library", description: "Control and maintain larger reference sources for on-demand search."},
    {id: "conversations", label: "Conversation history", description: "Review continuity and retained conversation archives."},
  ]},
  {id: "usage-maintenance", label: "Usage & Maintenance", path: "/extended-openai/usage-maintenance/usage", sections: [
    {id: "usage", label: "Usage", description: "Review token usage and recent runs."},
    {id: "request-debug", label: "Request debugging", description: "Capture and inspect complete recent provider requests."},
    {id: "backup-restore", label: "Backup & Restore", description: "Create or restore a private agent backup."},
    {id: "diagnostics", label: "Diagnostics", description: "Test the provider and inspect the selected agent."},
    {id: "retention", label: "Retention & maintenance", description: "Control detailed usage retention and cleanup."},
  ]},
];

const LEGACY_ROUTES = {
  configuration: ["assistant", "basics"],
  tools: ["capabilities", "functions"],
  guest: ["capabilities", "guest-mode"],
  memories: ["data-memory", "memories"],
  knowledge: ["data-memory", "knowledge"],
  conversations: ["data-memory", "conversations"],
  usage: ["usage-maintenance", "usage"],
  diagnostics: ["usage-maintenance", "diagnostics"],
};

const LEGACY_NESTED_ROUTES = {
  "assistant/advanced": ["capabilities", "web-skills"],
};

const setting = (label, description, terms, page, section, configKey = null, target = null, extra = {}) => ({
  label, description, terms, page, section, configKey, target, ...extra,
});

export const SETTINGS_INDEX = [
  setting("Agent name", "Name shown for this conversation agent.", "title assistant", "assistant", "basics", "__title", "config-__title", {format:"text"}),
  setting("Chat model", "Provider model used for responses.", "model provider", "assistant", "basics", "chat_model", "config-chat_model", {format:"text"}),
  setting("Provider API format", "How requests are formatted for the configured provider.", "api responses chat completions auto provider format", "assistant", "basics", "api_mode", "config-api_mode"),
  setting("Maximum response length", "Maximum tokens the model may use in one response.", "tokens max output response length", "assistant", "basics", "max_tokens", "config-max_tokens", {suffix:" tokens"}),
  setting("Maximum tool calls per conversation", "Stops runaway action loops after this many tool calls.", "function tools calls budget conversation", "assistant", "basics", "max_function_calls_per_conversation", "config-max_function_calls_per_conversation"),
  setting("Listen for a follow-up", "Whether Home Assistant keeps listening after a response.", "continue conversation follow up voice", "assistant", "basics", "continue_conversation", "config-continue_conversation"),

  setting("Response creativity (temperature)", "Controls how varied supported model responses may be.", "temperature creative predictable", "assistant", "model-responses", "temperature", "config-temperature", {capability:"supports_temperature"}),
  setting("Response diversity (Top P)", "Controls how widely a supported model samples possible words.", "top p diversity sampling", "assistant", "model-responses", "top_p", "config-top_p", {capability:"supports_top_p"}),
  setting("Reasoning effort", "How much work a supported model spends on difficult tasks.", "reasoning thinking effort", "assistant", "model-responses", "reasoning_effort", "config-reasoning_effort", {capability:"supports_reasoning_effort"}),
  setting("Processing tier", "Provider service tier used for requests when supported.", "service tier flex priority processing", "assistant", "model-responses", "service_tier", "config-service_tier", {capability:"supports_service_tier"}),
  setting("Use shorter tool-call IDs", "Compatibility option for providers that reject normal tool-call identifiers.", "short tool call id compatibility provider", "assistant", "model-responses", "shorten_tool_call_id", "config-shorten_tool_call_id", {format:"boolean"}),

  setting("Remember recent conversation", "How a new Assist request resumes recent context.", "conversation continuity resume device user session", "assistant", "conversation", "conversation_continuity", "config-conversation_continuity"),
  setting("Conversation timeout", "Starts a fresh conversation after this much inactivity.", "timeout inactivity fresh session minutes", "assistant", "conversation", "conversation_timeout_minutes", "config-conversation_timeout_minutes", {suffix:" min"}),
  setting("Trim history after", "Input-token threshold that triggers conversation-history reduction.", "context threshold tokens truncate history", "assistant", "conversation", "context_threshold", "config-context_threshold", {suffix:" input tokens"}),
  setting("Conversation history trimming", "Strategy used when recent context becomes too large.", "truncate summarize clear recent strategy context", "assistant", "conversation", "context_truncate_strategy", "config-context_truncate_strategy"),

  setting("Include current date & time", "Adds the Home Assistant-local date and time to the model context.", "date time current context prompt", "assistant", "prompt-context", "current_datetime_enabled", "config-current_datetime_enabled", {format:"boolean"}),
  setting("Include exposed devices", "Adds exposed entity names and current states to the model context.", "devices entities exposed states context prompt", "assistant", "prompt-context", "exposed_entities_enabled", "config-exposed_entities_enabled", {format:"boolean"}),
  setting("Current date/time format", "Optional custom prompt template for current date and time.", "advanced context template datetime format", "assistant", "prompt-context", "current_datetime_template", "config-prompt", {format:"template"}),
  setting("Device context format", "Optional custom prompt template for exposed entity context.", "advanced context template devices entities format", "assistant", "prompt-context", "exposed_entities_template", "config-prompt", {format:"template"}),
  setting("System prompt", "Instructions supplied to the assistant on each request.", "prompt instructions system template", "assistant", "prompt-context", "prompt", "prompt-editor", {format:"prompt"}),

  setting("When the speaker is not identified", "Retained-data scope used when voice identity cannot be resolved.", "voice unidentified speaker scope household user", "assistant", "voice", "voice_scope_policy", "config-voice_scope_policy"),
  setting("When a device has no mapping", "Retained-data scope used for an unmapped voice device.", "voice unmapped satellite device scope", "assistant", "voice", "voice_unmapped_policy", "config-voice_unmapped_policy"),
  setting("Default Home Assistant user ID", "User whose retained data is used when a voice policy selects the default user.", "voice default user id identity", "assistant", "voice", "voice_default_user_id", "config-voice_default_user_id", {format:"text"}),
  setting("Voice device assignments", "Maps satellites and voice devices to users or household data.", "voice mappings satellite devices assignments json", "assistant", "voice", "voice_device_mappings", "voice-mappings", {format:"mapping"}),

  setting("Speech post-processing", "Cleans text before it is spoken while keeping the original response.", "speech tts processing", "assistant", "speech", "speech_processing_enabled", "config-speech_processing_enabled", {format:"boolean"}),
  setting("Remove Markdown links and formatting", "Prevents Markdown formatting from being spoken.", "speech markdown links formatting tts", "assistant", "speech", "speech_strip_markdown", "config-speech_strip_markdown", {format:"boolean"}),
  setting("Remove bare URLs", "Prevents raw web addresses from being spoken.", "speech urls web addresses tts", "assistant", "speech", "speech_strip_urls", "config-speech_strip_urls", {format:"boolean"}),
  setting("Custom speech replacements", "Completed-response replacement rules used for spoken output.", "speech regex custom replacements rules", "assistant", "speech", "speech_regex_replacements", "regex-rules", {format:"count", singular:"rule", plural:"rules"}),

  setting("Use Extended OpenAI local handling", "Try Home Assistant's built-in commands after Request Rules and before AI.", "local home assistant intents prefer handling", "capabilities", "home-assistant", "local_intents_enabled", "config-local", {format:"boolean"}),
  setting("Send delayed device commands to AI", "Lets deferred Function Tools handle commands such as turning something off later.", "local delayed scheduled deferred commands ai", "capabilities", "home-assistant", "local_intent_delayed_commands_to_ai", "config-local", {format:"boolean"}),
  setting("Command types sent to AI", "Home Assistant intent types excluded from Extended OpenAI local handling.", "local exclusions intents command types ai", "capabilities", "home-assistant", "local_intent_exclusions", "config-local", {format:"count", singular:"command type", plural:"command types"}),
  setting("Web search", "Lets the assistant search the web for current information.", "web current search internet", "capabilities", "web-skills", "web_search", "config-web_search", {format:"boolean"}),
  setting("Web search detail", "How much supporting material the provider returns for web searches.", "web search context detail", "capabilities", "web-skills", "web_search_context", "config-web_search_context"),
  setting("Skills", "Installed instruction sets the assistant may load when needed.", "skills instruction sets installed capability", "capabilities", "web-skills", "skills", "config-skills", {format:"count", singular:"skill", plural:"skills"}),
  setting("Request Rules", "Fast local commands and model-routing rules.", "phrases fuzzy synonyms local commands model routing", "capabilities", "request-rules"),
  setting("Function Tools & Groups", "Custom functions and on-demand loading groups.", "tools functions groups yaml", "capabilities", "functions"),
  setting("Guest Mode", "Visitor restrictions, schedules, and access policy.", "guest visitors restrictions schedule privacy", "capabilities", "guest-mode", null, null, {source:"guest-mode"}),

  setting("Long-term memory", "Whether durable memories are disabled, explicit-only, or automatic.", "memory persistent durable automatic manual", "data-memory", "memory-settings", "memory_mode", "config-memory_mode"),
  setting("Short-term memory", "How readily temporary details are remembered until they expire.", "memory temporary short term eager balanced", "data-memory", "memory-settings", "temporary_memory", "config-temporary_memory"),
  setting("Automatically include memories", "Maximum relevant memories automatically supplied to a new conversation.", "memory automatic include retrieve limit", "data-memory", "memory-settings", "memory_auto_retrieve_limit", "config-memory_auto_retrieve_limit"),
  setting("Memory retrieval", "Lexical or Hybrid relevance matching for stored memories.", "memory retrieval lexical hybrid semantic", "data-memory", "memory-settings", "memory_retrieval_mode", "config-memory_retrieval_mode"),
  setting("Embedding model", "Embedding model used by Hybrid memory retrieval.", "memory embeddings semantic hybrid model", "data-memory", "memory-settings", "memory_embedding_model", "config-memory_embedding_model", {format:"text"}),
  setting("Shared household memory", "Whether household-wide durable memories may be used and saved.", "memory shared household explicit automatic", "data-memory", "memory-settings", "shared_memory_mode", "config-shared_memory_mode"),
  setting("Knowledge Library access", "Whether stored Knowledge sources are available to the assistant.", "knowledge library sources enable access", "data-memory", "knowledge", "knowledge_enabled", "knowledge-enabled-toggle", {format:"boolean"}),
  setting("Stored memories", "Review, add, edit, reassign, or remove saved memories.", "memory stored records manage", "data-memory", "memories"),

  setting("Save conversation history", "Stores conversations locally for later review and optional search.", "archive history save retain conversations", "data-memory", "conversations", "archive_enabled", "config-archive_enabled", {format:"boolean"}),
  setting("Keep archived conversations for", "How long saved conversations remain available.", "archive retention days history", "data-memory", "conversations", "archive_retention_days", "config-archive_retention_days", {suffix:" days"}),
  setting("Start a new archive after", "Inactivity interval that starts a new archived conversation.", "archive session timeout inactivity", "data-memory", "conversations", "archive_session_timeout_minutes", "config-archive_session_timeout_minutes", {suffix:" min"}),
  setting("Let the assistant search the archive", "Allows model search across retained conversations.", "archive model search conversations", "data-memory", "conversations", "archive_model_search_enabled", "config-archive_model_search_enabled", {format:"boolean"}),
  setting("Save shared-household conversations", "Also archives eligible conversations assigned to the shared household.", "archive shared household conversations", "data-memory", "conversations", "shared_archive_enabled", "config-shared_archive_enabled", {format:"boolean"}),

  setting("Request details retention", "How long detailed per-request usage records are retained.", "usage request details retention days", "usage-maintenance", "retention", "usage_request_retention_days", "config-usage_request_retention_days", {suffix:" days"}),
  setting("Run details retention", "How long detailed run records are retained.", "usage run details retention days", "usage-maintenance", "retention", "usage_run_retention_days", "config-usage_run_retention_days", {suffix:" days"}),
  setting("Request debugging", "Capture and inspect complete recent provider requests.", "debug request prompt cache timing logs diagnostics", "usage-maintenance", "request-debug"),
  setting("Backup & Restore", "Create or restore a full private agent backup.", "backup export import disaster recovery", "usage-maintenance", "backup-restore"),
  setting("Diagnostics", "Test the provider and inspect the selected agent.", "diagnostics provider connection test", "usage-maintenance", "diagnostics"),
];

export function routeFromPath(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  const marker = parts.lastIndexOf("extended-openai");
  const route = marker >= 0 ? parts.slice(marker + 1) : parts;
  if (!route.length) return {page: "overview", section: null, legacy: false};
  const nestedLegacy = LEGACY_NESTED_ROUTES[`${route[0] || ""}/${route[1] || ""}`];
  if (nestedLegacy) {
    const [page, section] = nestedLegacy;
    return {page, section, legacy: true};
  }
  if (LEGACY_ROUTES[route[0]]) {
    const [page, section] = LEGACY_ROUTES[route[0]];
    return {page, section, legacy: true};
  }
  const page = NAVIGATION.find((item) => item.id === route[0]);
  if (!page) return {page: "overview", section: null, legacy: false};
  const section = page.sections.find((item) => item.id === route[1])?.id || page.sections[0]?.id || null;
  return {page: page.id, section, legacy: false};
}

export function routePath(page, section = null) {
  return `/extended-openai/${page}${section ? `/${section}` : ""}`;
}

export function pageMetadata(page) {
  return NAVIGATION.find((item) => item.id === page) || NAVIGATION[0];
}

export function shouldShowGlobalSettingsSearch() {
  return true;
}

export function searchSettings(query) {
  const normalized = String(query || "").trim().toLowerCase();
  const terms = normalized.split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return SETTINGS_INDEX
    .map((item, index) => {
      const haystack = `${item.label} ${item.description} ${item.terms} ${item.configKey || ""}`.toLowerCase();
      if (!terms.every((term) => haystack.includes(term))) return null;
      const label = item.label.toLowerCase();
      const score = label === normalized ? 0 : label.startsWith(normalized) ? 1 : label.includes(normalized) ? 2 : 3;
      return {item, index, score};
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .map(({item}) => item);
}
