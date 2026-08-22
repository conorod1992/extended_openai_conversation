export const NAVIGATION = [
  {id: "overview", label: "Overview", path: "/extended-openai/overview", sections: []},
  {id: "guide", label: "Guide", path: "/extended-openai/guide", sections: []},
  {id: "assistant", label: "Assistant", path: "/extended-openai/assistant/basics", sections: [
    {id: "basics", label: "Basics"},
    {id: "model-responses", label: "Model & responses"},
    {id: "conversation", label: "Conversation"},
    {id: "prompt-context", label: "Prompt & context"},
    {id: "voice", label: "Voice"},
    {id: "speech", label: "Speech"},
    {id: "advanced", label: "Advanced"},
  ]},
  {id: "capabilities", label: "Capabilities", path: "/extended-openai/capabilities/home-assistant", sections: [
    {id: "home-assistant", label: "Home Assistant"},
    {id: "functions", label: "Functions"},
    {id: "guest-mode", label: "Guest Mode"},
  ]},
  {id: "data-memory", label: "Data & Memory", path: "/extended-openai/data-memory/memories", sections: [
    {id: "memories", label: "Memories"},
    {id: "knowledge", label: "Knowledge Library"},
    {id: "conversations", label: "Conversation history"},
  ]},
  {id: "usage-maintenance", label: "Usage & Maintenance", path: "/extended-openai/usage-maintenance/usage", sections: [
    {id: "usage", label: "Usage"},
    {id: "backup-restore", label: "Backup & Restore"},
    {id: "diagnostics", label: "Diagnostics"},
    {id: "retention", label: "Retention & maintenance"},
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
  {label: "Memory and Knowledge settings", description: "Configure retrieval and available data capabilities.", terms: "memory temporary embeddings knowledge web search skills", page: "assistant", section: "advanced", target: "config-capabilities"},
  {label: "Function Tools & Groups", description: "Manage functions, grouping, and loading modes.", terms: "tools functions yaml", page: "capabilities", section: "functions"},
  {label: "Guest Mode", description: "Schedule visitor restrictions and select excluded access.", terms: "guest labels areas domains entities knowledge functions", page: "capabilities", section: "guest-mode"},
  {label: "Conversation archive", description: "Retained history and archive search settings.", terms: "archive history retention session", page: "data-memory", section: "conversations", target: "config-archive"},
  {label: "Usage retention", description: "Retention for detailed requests and runs.", terms: "usage cleanup maintenance", page: "usage-maintenance", section: "retention", target: "config-retention"},
  {label: "Backup & Restore", description: "Create or restore a full private backup.", terms: "backup export import disaster recovery", page: "usage-maintenance", section: "backup-restore", target: "config-backup"},
];

export function routeFromPath(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  const marker = parts.lastIndexOf("extended-openai");
  const route = marker >= 0 ? parts.slice(marker + 1) : parts;
  if (!route.length) return {page: "overview", section: null, legacy: false};
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

export function searchSettings(query) {
  const terms = String(query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return SETTINGS_INDEX.filter((item) => {
    const haystack = `${item.label} ${item.description} ${item.terms}`.toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}
