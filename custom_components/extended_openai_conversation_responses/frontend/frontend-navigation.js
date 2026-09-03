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

export const SETTINGS_INDEX = [
  {label: "Agent name", description: "Name shown for this conversation agent.", terms: "title assistant", page: "assistant", section: "basics", target: "config-general"},
  {label: "Chat model", description: "Provider model used for responses.", terms: "model provider api format", page: "assistant", section: "basics", target: "config-general"},
  {label: "Maximum response length", description: "Maximum response tokens.", terms: "tokens", page: "assistant", section: "basics", target: "config-general"},
  {label: "Listen for a follow-up", description: "Keep listening after a response.", terms: "continue voice", page: "assistant", section: "basics", target: "config-general"},
  {label: "Reasoning effort", description: "Model reasoning and response controls.", terms: "service tier temperature top p model", page: "assistant", section: "model-responses", target: "config-model"},
  {label: "Conversation continuity", description: "Resume recent conversational context.", terms: "timeout device user follow-up", page: "assistant", section: "conversation", target: "config-conversation"},
  {label: "Conversation context limits", description: "Trim or summarize long conversations.", terms: "context threshold truncate summarize", page: "assistant", section: "conversation", target: "config-context"},
  {label: "Prompt & context", description: "Instructions, date/time, and exposed entity context.", terms: "template devices entities request preview", page: "assistant", section: "prompt-context", target: "config-prompt"},
  {label: "Voice identity", description: "Voice scope, device mappings, and unmapped speakers.", terms: "voice user household satellite follow-up", page: "assistant", section: "voice", target: "config-voice"},
  {label: "Speech post-processing", description: "Clean Markdown, URLs, and phrases for TTS.", terms: "speech regex replacements tts", page: "assistant", section: "speech", target: "config-speech"},
  {label: "Memory settings", description: "Configure long-term, temporary, retrieval, embedding, and shared household memory behavior.", terms: "memory temporary short term long term automatic retrieval lexical hybrid embeddings shared household", page: "data-memory", section: "memory-settings", target: "config-memory"},
  {label: "Local handling", description: "Let Home Assistant handle suitable simple commands before AI.", terms: "home assistant local intents delayed commands exclusions prefer local", page: "capabilities", section: "home-assistant", target: "config-local"},
  {label: "Web search", description: "Let the assistant search the web for current information.", terms: "web current search context detail internet", page: "capabilities", section: "web-skills", target: "config-capabilities"},
  {label: "Skills", description: "Choose installed instruction sets the assistant may load when needed.", terms: "skills instruction sets installed capability", page: "capabilities", section: "web-skills", target: "config-capabilities"},
  {label: "Knowledge Library", description: "Enable Knowledge tools and manage stored reference sources.", terms: "knowledge library sources reference enable search", page: "data-memory", section: "knowledge"},
  {label: "Function Tools & Groups", description: "Manage functions, grouping, and loading modes.", terms: "tools functions yaml", page: "capabilities", section: "functions"},
  {label: "Request Rules", description: "Fast local voice commands and model routing.", terms: "phrases matching fuzzy synonyms local commands model reasoning", page: "capabilities", section: "request-rules"},
  {label: "Guest Mode", description: "Schedule visitor restrictions and select excluded access.", terms: "guest labels areas domains entities knowledge functions", page: "capabilities", section: "guest-mode"},
  {label: "Conversation archive", description: "Retained history and archive search settings.", terms: "archive history retention session", page: "data-memory", section: "conversations", target: "config-archive"},
  {label: "Request debugging", description: "Capture full recent request material for troubleshooting.", terms: "debug request prompt cache timing logs diagnostics", page: "usage-maintenance", section: "request-debug"},
  {label: "Usage retention", description: "Retention for detailed requests and runs.", terms: "usage cleanup maintenance", page: "usage-maintenance", section: "retention", target: "config-retention"},
  {label: "Backup & Restore", description: "Create or restore a full private backup.", terms: "backup export import disaster recovery", page: "usage-maintenance", section: "backup-restore", target: "config-backup"},
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

export function shouldShowGlobalSettingsSearch(page, subsection = null) {
  const view = subsection ? `${page}/${subsection}` : page;
  return page === "assistant" || [
    "capabilities/home-assistant",
    "capabilities/web-skills",
    "capabilities/guest-mode",
    "data-memory/memory-settings",
    "data-memory/knowledge",
    "usage-maintenance/backup-restore",
    "usage-maintenance/retention",
    "usage-maintenance/diagnostics",
    "usage-maintenance/request-debug",
  ].includes(view);
}

export function searchSettings(query) {
  const terms = String(query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return SETTINGS_INDEX.filter((item) => {
    const haystack = `${item.label} ${item.description} ${item.terms}`.toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}
