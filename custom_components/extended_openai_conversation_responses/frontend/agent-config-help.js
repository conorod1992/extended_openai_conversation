const DOCS_ROOT = "https://github.com/conorod1992/extended_openai_conversation/blob/develop/docs";

export const HELP_METADATA = Object.freeze({
  api_mode: Object.freeze({
    title: "API mode",
    paragraphs: ["Auto uses Chat Completions by default and selects Responses for GPT-5.6 and later GPT-5 minor models. Override it when an OpenAI-compatible provider supports only one API style."],
    items: [
      { term: "Auto", text: "Lets the integration choose using its model compatibility rules." },
      { term: "Responses", text: "Uses the newer Responses API style." },
      { term: "Chat Completions", text: "Uses the widely supported Chat Completions API style." },
    ],
    keywords: "provider compatible endpoint responses chat completions auto",
    href: `${DOCS_ROOT}/features/responses-api.md`,
  }),
  continue_conversation: Object.freeze({
    title: "Continue conversation",
    items: [
      { term: "HA Default", text: "Uses Home Assistant's normal continuation behaviour." },
      { term: "Always", text: "Keeps listening after every successful response." },
      { term: "Conditional", text: "Lets the model decide whether an immediate reply is expected, using the integration's continuation tool." },
    ],
    keywords: "follow-up follow up keep listening immediate turn home assistant default conditional",
    href: `${DOCS_ROOT}/features/voice-followups.md`,
  }),
  conversation_continuity: Object.freeze({
    title: "Conversation continuity",
    paragraphs: ["This is separate from Continue conversation, which controls immediate follow-up listening. Continuity lets a new wake-word or Assist request reuse recent model context. It does not perform speaker recognition."],
    items: [
      { term: "Home Assistant default", text: "Uses Home Assistant's normal conversation sessions." },
      { term: "Per device", text: "Requests from the same Assist device can resume a recent conversation until the timeout expires." },
      { term: "Per user", text: "Requests resolved to the same user can resume across devices. Unresolved speech safely falls back to its device or a new session; shared household scope is never treated as a personal user." },
    ],
    keywords: "conversation continuity resume separate Assist invocation cross-device satellite user device speaker recognition",
    href: `${DOCS_ROOT}/features/conversation-continuity.md`,
  }),
  conversation_timeout: Object.freeze({
    title: "Conversation timeout",
    paragraphs: ["This inactivity timeout applies only to active model context. Every successful turn resets it. It is separate from archive retention and archive session timeout."],
    keywords: "inactivity timeout fresh conversation reset context archive separate",
    href: `${DOCS_ROOT}/features/conversation-continuity.md`,
  }),
  web_search_context: Object.freeze({
    title: "Search context",
    paragraphs: ["Low, Medium, and High ask the provider for progressively more search context. More context can improve coverage, but may increase the amount of material sent back with a search."],
    keywords: "web current provider low medium high amount coverage",
    href: `${DOCS_ROOT}/features/web-search.md`,
  }),
  knowledge_library: Object.freeze({
    title: "Knowledge, memory, and archive",
    paragraphs: ["Knowledge sources are reference information deliberately maintained in the local Knowledge Library. The agent can search them when relevant."],
    items: [
      { term: "Knowledge", text: "Maintained reference sources and documents." },
      { term: "Memory", text: "Durable facts remembered for later conversations." },
      { term: "Archive", text: "Retained conversation history." },
    ],
    keywords: "local reference sources documents maintained searchable separate persistent memory history",
    href: `${DOCS_ROOT}/features/knowledge-library.md`,
  }),
  memory_mode: Object.freeze({
    title: "Persistent memory modes",
    paragraphs: ["Memories persist across conversations. They are separate from conversation archive/history, and Automatic does not store every conversation as memory."],
    items: [
      { term: "Off", text: "Persistent memory is disabled for this agent." },
      { term: "Manual", text: "Memories are stored only when explicitly requested or added through the memory tools or UI." },
      { term: "Automatic", text: "The agent may also save useful durable facts automatically, subject to the existing memory rules and safety restrictions." },
    ],
    keywords: "persistent remembered durable facts across conversations manual automatic safety history archive",
    href: `${DOCS_ROOT}/features/persistent-memory.md`,
  }),
  temporary_memory: Object.freeze({
    title: "Temporary memory",
    paragraphs: ["Temporary memory is automatic, silent, scoped with Conversation continuity, and separate from durable persistent memory. The assistant infers practical expiry times in Home Assistant's local timezone."],
    items: [
      { term: "Off", text: "No temporary facts are automatically created or injected." },
      { term: "Balanced", text: "Remembers clearly useful temporary context and infers reasonable expiry times." },
      { term: "Eager", text: "More readily preserves plausible near-term context while still avoiding trivial conversational details." },
    ],
    example: "My parents are visiting this weekend → remembered until the end of Sunday without asking for an exact departure time.",
    keywords: "temporary ephemeral short-term memory automatic silent expiry balanced eager today weekend",
    href: `${DOCS_ROOT}/features/temporary-memory.md`,
  }),
  archive_model_search: Object.freeze({
    title: "Model archive search",
    paragraphs: ["When enabled, the agent receives a bounded search tool for retained conversations in the current data scope. This is useful when an earlier discussion may answer the current request."],
    keywords: "prior discussions retained conversations model search current scope lexical history",
    href: `${DOCS_ROOT}/features/conversation-archive.md`,
  }),
  shared_archive: Object.freeze({
    title: "Shared-household archive",
    paragraphs: ["Allows eligible conversations assigned to the shared household scope to be retained. It does not make private user-scoped archives shared."],
    keywords: "shared household eligible scope privacy retained conversations",
    href: `${DOCS_ROOT}/features/conversation-archive.md`,
  }),
  archive_session_timeout: Object.freeze({
    title: "Archive session timeout",
    paragraphs: ["After this many minutes without activity, the next turn starts a new archive session instead of continuing the previous retained session."],
    keywords: "inactivity new conversation archive session minutes split",
    href: `${DOCS_ROOT}/features/conversation-archive.md`,
  }),
  voice_scope_policy: Object.freeze({
    title: "Unidentified voice policy",
    items: [
      { term: "Unretained", text: "Uses no persistent owner, so memory and archive retention are unavailable for the request." },
      { term: "Shared", text: "Assigns the request to the shared household scope." },
      { term: "Default user", text: "Assigns it to the configured Home Assistant user." },
      { term: "Device mapping", text: "Uses the source satellite or device ID to find a configured owner, then applies the unmapped fallback if no mapping resolves." },
    ],
    keywords: "speaker unidentified owner unretained shared default user device mapping satellite scope",
    href: `${DOCS_ROOT}/features/persistent-memory.md`,
  }),
  voice_unmapped_policy: Object.freeze({
    title: "Unmapped fallback",
    paragraphs: ["This policy is used when Device mapping is selected but the request has no matching device entry."],
    items: [
      { term: "Unretained", text: "Uses no persistent owner." },
      { term: "Shared", text: "Uses the shared household scope." },
      { term: "Default user", text: "Uses the configured default voice owner." },
      { term: "Device mapping", text: "Cannot resolve another mapping at this stage and therefore falls back to unretained." },
    ],
    keywords: "speaker unresolved unknown device satellite fallback owner mapping unretained shared",
    href: `${DOCS_ROOT}/features/persistent-memory.md`,
  }),
  shared_memory_mode: Object.freeze({
    title: "Shared memory mode",
    items: [
      { term: "Disabled", text: "Persistent memory is unavailable in the shared household scope." },
      { term: "Explicit", text: "Shared memories may be added only by an explicit request or through the memory UI." },
      { term: "Automatic", text: "The agent may also save durable shared facts automatically when the agent's Memory mode permits it." },
    ],
    keywords: "household persistent memory disabled explicit automatic shared facts",
    href: `${DOCS_ROOT}/features/persistent-memory.md`,
  }),
  voice_device_mappings: Object.freeze({
    title: "Satellite and device mappings",
    paragraphs: ["Map each Home Assistant source device or voice satellite ID to a user ID, user:<id>, shared, or unretained. Mappings are consulted only when the unidentified voice policy is Device mapping."],
    keywords: "voice satellite source device id user owner shared unretained json mapping",
    href: `${DOCS_ROOT}/features/persistent-memory.md`,
  }),
  custom_replacements: Object.freeze({
    title: "Custom spoken replacements",
    paragraphs: ["Rules use Python regular expressions, run from top to bottom on the completed response, and change spoken output only. An empty replacement removes matching text; capture groups are supported. Because arbitrary regex can depend on future text, progressive TTS is disabled while custom rules are configured. Invalid rules are rejected during validation."],
    example: String.raw`\bHA\b → Home Assistant`,
    keywords: "python regex regular expression capture groups ordered remove validation tts spoken",
  }),
  context_threshold: Object.freeze({
    title: "Context threshold",
    paragraphs: ["This is a context-size threshold based on provider-reported input tokens, not a message count. Higher values preserve more history but can use more context and tokens. What is retained depends on the truncation strategy."],
    keywords: "tokens context size message count threshold history limit",
    href: `${DOCS_ROOT}/features/context-management.md`,
  }),
  context_truncation: Object.freeze({
    title: "Truncation strategies",
    items: [
      { term: "Keep recent messages", text: "Drops the oldest complete turns while preserving the system prompt and newest conversation turns." },
      { term: "Clear all messages", text: "Clears prior conversation content when the threshold is exceeded." },
      { term: "Summarize older messages", text: "Makes one bounded model request to summarize older turns, keeps recent raw turns, and falls back to Keep recent if summarization fails." },
    ],
    keywords: "truncate clear summarize recent older messages context history strategy",
    href: `${DOCS_ROOT}/features/context-management.md`,
  }),
  reasoning_effort: Object.freeze({
    title: "Reasoning effort",
    paragraphs: ["Choose the amount of reasoning requested from supported models. The practical difference depends on the selected model."],
    items: [
      { term: "Low", text: "Favours quicker, lighter reasoning for straightforward tasks." },
      { term: "Medium", text: "Balances reasoning depth with response time." },
      { term: "High", text: "Requests more reasoning for difficult tasks and may take longer or use more billed tokens." },
    ],
    keywords: "thinking latency cost performance low medium high hard tasks",
  }),
  service_tier: Object.freeze({
    title: "Service tier",
    paragraphs: ["Passes the selected processing tier to the provider when supported. Availability, billing, and response characteristics depend on the provider and account."],
    items: [
      { term: "Auto", text: "Lets the provider choose the tier." },
      { term: "Default", text: "Requests the provider's standard tier." },
      { term: "Flex", text: "Requests Flex processing." },
      { term: "Priority", text: "Requests Priority processing." },
    ],
    keywords: "provider processing billing latency auto default flex priority",
  }),
  function_tools: Object.freeze({
    title: "Function tools and groups",
    paragraphs: ["Function groups are optional. Existing ungrouped functions remain always available when enabled. An on-demand group sends only its compact name and description until the model decides the current task needs it.", "Built-in Functions are editable presets for native capabilities implemented directly by Extended OpenAI Conversation. Disabled tools remain configured and grouped, but their schemas are not sent to the model and calls are rejected."],
    items: [
      { term: "Always available", text: "Sends full definitions on every request, matching existing behaviour." },
      { term: "Load when needed", text: "Withholds full definitions until the model requests the group; loaded groups remain available for the active conversation." },
      { term: "Function YAML", text: "spec describes the model-facing schema and function tells Extended OpenAI how to execute it. Grouping does not change this format or its validation." },
      { term: "Enabled", text: "Uses the same saved state in the Tools page and Home Assistant enable/disable actions. Disabled is a capability boundary, not a prompt preference." },
    ],
    keywords: "group on demand load when needed tokens catalogue yaml spec schema validate execute built in enabled disabled",
    href: `${DOCS_ROOT}/functions/overview.mdx`,
  }),
});

export function helpSearchTerms(key) {
  const entry = HELP_METADATA[key];
  if (!entry) return "";
  return [entry.title, ...(entry.paragraphs || []), ...(entry.items || []).flatMap((item) => [item.term, item.text]), entry.example || "", entry.keywords || ""].join(" ");
}

export function helpButton(panel, key) {
  const entry = HELP_METADATA[key];
  if (!entry) throw new Error(`Unknown configuration help key: ${key}`);
  return `<button type="button" class="help-button" data-help="${panel._e(key)}" aria-label="More information about ${panel._e(entry.title)}" aria-haspopup="dialog" aria-expanded="false"><span aria-hidden="true">i</span></button>`;
}

export function helpPopover() {
  return `<dialog id="config-help-popover" class="help-popover" aria-labelledby="config-help-title"><div class="help-popover-header"><h2 id="config-help-title"></h2><button type="button" class="help-close" aria-label="Close help">&times;</button></div><div id="config-help-content" class="help-popover-content"></div></dialog>`;
}

function renderContent(panel, entry) {
  const paragraphs = (entry.paragraphs || []).map((value) => `<p>${panel._e(value)}</p>`).join("");
  const items = entry.items?.length ? `<dl>${entry.items.map((item) => `<div><dt>${panel._e(item.term)}</dt><dd>${panel._e(item.text)}</dd></div>`).join("")}</dl>` : "";
  const example = entry.example ? `<pre><code>${panel._e(entry.example)}</code></pre>` : "";
  const link = entry.href ? `<a class="help-link" href="${panel._e(entry.href)}" target="_blank" rel="noopener noreferrer">Learn more<span class="sr-only"> (opens in a new tab)</span></a>` : "";
  return `${paragraphs}${items}${example}${link}`;
}

export function closeHelp(panel, { restoreFocus = true } = {}) {
  const popover = panel.shadowRoot.querySelector("#config-help-popover");
  if (!popover?.open) return;
  popover.close();
  popover.style.left = "";
  popover.style.top = "";
  panel.shadowRoot.querySelectorAll("[data-help]").forEach((button) => button.setAttribute("aria-expanded", "false"));
  if (restoreFocus) panel._helpTrigger?.focus({ preventScroll: true });
  panel._helpTrigger = null;
}

function positionPopover(popover, trigger) {
  if (window.matchMedia("(max-width: 679px)").matches) return;
  const rect = trigger.getBoundingClientRect();
  const width = Math.min(380, window.innerWidth - 24);
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - popover.offsetHeight - 12))}px`;
}

export function bindHelp(panel) {
  const root = panel.shadowRoot;
  const popover = root.querySelector("#config-help-popover");
  if (!popover) return;
  root.querySelectorAll("[data-help]").forEach((button) => button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const entry = HELP_METADATA[button.dataset.help];
    if (!entry) return;
    closeHelp(panel, { restoreFocus: false });
    panel._helpTrigger = button;
    root.querySelector("#config-help-title").textContent = entry.title;
    root.querySelector("#config-help-content").innerHTML = renderContent(panel, entry);
    popover.showModal();
    button.setAttribute("aria-expanded", "true");
    positionPopover(popover, button);
    popover.querySelector(".help-close").focus();
  }));
  popover.querySelector(".help-close")?.addEventListener("click", () => closeHelp(panel));
  popover.addEventListener("click", (event) => { if (event.target === popover) closeHelp(panel); });
  popover.addEventListener("cancel", (event) => { event.preventDefault(); closeHelp(panel); });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && popover.open) {
      event.preventDefault();
      closeHelp(panel);
    }
  });
}
