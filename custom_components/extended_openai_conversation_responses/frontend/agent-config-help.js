const HELP_INDEX = Object.freeze({
  "api_mode": {
    "title": "Provider API format",
    "searchTerms": "Provider API format Auto uses Chat Completions by default and selects Responses for GPT-5.6 and later GPT-5 minor models. Override it when an OpenAI-compatible provider supports only one API style. Auto Lets the integration choose using its model compatibility rules. Responses Uses the newer Responses API style. Chat Completions Uses the widely supported Chat Completions API style.  provider compatible endpoint responses chat completions auto"
  },
  "continue_conversation": {
    "title": "Listen for a follow-up",
    "searchTerms": "Listen for a follow-up HA Default Uses Home Assistant's normal continuation behaviour. Always Keeps listening after every successful response. Conditional Lets the model decide whether an immediate reply is expected, using the integration's continuation tool.  follow-up follow up keep listening immediate turn home assistant default conditional"
  },
  "conversation_continuity": {
    "title": "Remember recent conversation",
    "searchTerms": "Remember recent conversation This is separate from immediate follow-up listening. It lets a new wake-word or Assist request continue a recent conversation. It does not identify speakers. Use Home Assistant sessions Starts or continues conversations using Home Assistant's normal session handling. Remember by voice device Requests from the same Assist device can continue a recent conversation until the timeout expires. Remember by user across devices Requests assigned to the same user can continue across devices. Unidentified speech safely falls back to its device or a new conversation.  conversation continuity resume separate Assist invocation cross-device satellite user device speaker recognition"
  },
  "conversation_timeout": {
    "title": "Conversation timeout",
    "searchTerms": "Conversation timeout This inactivity timeout applies only to active model context. Every successful turn resets it. It is separate from archive retention and archive session timeout.  inactivity timeout fresh conversation reset context archive separate"
  },
  "web_search_context": {
    "title": "Web search detail",
    "searchTerms": "Web search detail Low, Medium, and High ask the provider for progressively more search context. More context can improve coverage, but may increase the amount of material sent back with a search.  web current provider low medium high amount coverage"
  },
  "knowledge_library": {
    "title": "Knowledge, memory, and archive",
    "searchTerms": "Knowledge, memory, and archive Knowledge sources are reference information deliberately maintained in the local Knowledge Library. The agent can search them when relevant. Knowledge Maintained reference sources and documents. Memory Durable facts remembered for later conversations. Archive Retained conversation history.  local reference sources documents maintained searchable separate persistent memory history"
  },
  "memory_mode": {
    "title": "Persistent memory modes",
    "searchTerms": "Persistent memory modes Memories persist across conversations. They are separate from conversation archive/history, and Automatic does not store every conversation as memory. Off Persistent memory is disabled for this agent. Manual Memories are stored only when explicitly requested or added through the memory tools or UI. Automatic The agent may also save useful durable facts automatically, subject to the existing memory rules and safety restrictions.  persistent remembered durable facts across conversations manual automatic safety history archive"
  },
  "temporary_memory": {
    "title": "Temporary memory",
    "searchTerms": "Temporary memory Temporary memory is automatic, silent, scoped with Conversation continuity, and separate from durable persistent memory. The assistant infers practical expiry times in Home Assistant's local timezone. Off No temporary facts are automatically created or injected. Balanced Remembers clearly useful temporary context and infers reasonable expiry times. Eager More readily preserves plausible near-term context while still avoiding trivial conversational details. My parents are visiting this weekend → remembered until the end of Sunday without asking for an exact departure time. temporary ephemeral short-term memory automatic silent expiry balanced eager today weekend"
  },
  "archive_model_search": {
    "title": "Model archive search",
    "searchTerms": "Model archive search When enabled, the agent receives a bounded search tool for retained conversations in the current data scope. This is useful when an earlier discussion may answer the current request.  prior discussions retained conversations model search current scope lexical history"
  },
  "shared_archive": {
    "title": "Shared-household archive",
    "searchTerms": "Shared-household archive Allows eligible conversations assigned to the shared household scope to be retained. It does not make private user-scoped archives shared.  shared household eligible scope privacy retained conversations"
  },
  "archive_session_timeout": {
    "title": "Archive session timeout",
    "searchTerms": "Archive session timeout After this many minutes without activity, the next turn starts a new archive session instead of continuing the previous retained session.  inactivity new conversation archive session minutes split"
  },
  "voice_scope_policy": {
    "title": "When the speaker is not identified",
    "searchTerms": "When the speaker is not identified Do not retain personal data Uses no personal owner, so long-term memory and conversation archiving are unavailable for the request. Use shared household data Uses memories and history shared by the household. Use the default user Uses the configured Home Assistant user's memories and history. Use a device-to-user mapping Looks up the source satellite or device. If it has no assignment, the unmapped-device setting is used.  speaker unidentified owner unretained shared default user device mapping satellite scope"
  },
  "voice_unmapped_policy": {
    "title": "When a device has no mapping",
    "searchTerms": "When a device has no mapping This setting is used when device-to-user mapping is selected but the request comes from a device with no assignment. Do not retain personal data Uses no personal memories or archived conversation history. Use shared household data Uses memories and history shared by the household. Use the default user Uses the configured default user's memories and history. Device mapping (no retained data) A second device lookup cannot resolve the request, so this option uses no retained personal data.  speaker unresolved unknown device satellite fallback owner mapping unretained shared"
  },
  "shared_memory_mode": {
    "title": "Shared household memory",
    "searchTerms": "Shared household memory Disabled Persistent memory is unavailable in the shared household scope. Explicit Shared memories may be added only by an explicit request or through the memory UI. Automatic The agent may also save durable shared facts automatically when the agent's Memory mode permits it.  household persistent memory disabled explicit automatic shared facts"
  },
  "voice_device_mappings": {
    "title": "Voice device assignments",
    "searchTerms": "Voice device assignments Assign each Home Assistant source device or voice satellite ID to a user ID, user:<id>, shared, or unretained. Assignments are used only when the unidentified-speaker setting uses a device-to-user mapping.  voice satellite source device id user owner shared unretained json mapping"
  },
  "custom_replacements": {
    "title": "Custom spoken replacements",
    "searchTerms": "Custom spoken replacements Rules use Python regular expressions, run from top to bottom on the completed response, and change spoken output only. An empty replacement removes matching text; capture groups are supported. Because arbitrary regex can depend on future text, progressive TTS is disabled while custom rules are configured. Invalid rules are rejected during validation. \\bHA\\b → Home Assistant python regex regular expression capture groups ordered remove validation tts spoken"
  },
  "context_threshold": {
    "title": "Conversation history token limit",
    "searchTerms": "Conversation history token limit This is a context-size threshold based on provider-reported input tokens, not a message count. Higher values preserve more history but can use more context and tokens. What is retained depends on the truncation strategy.  tokens context size message count threshold history limit"
  },
  "context_truncation": {
    "title": "Conversation history trimming",
    "searchTerms": "Conversation history trimming Keep recent messages Drops the oldest complete turns while preserving the system prompt and newest conversation turns. Clear all messages Clears prior conversation content when the threshold is exceeded. Summarize older messages Makes one bounded model request to summarize older turns, keeps recent raw turns, and falls back to Keep recent if summarization fails.  truncate clear summarize recent older messages context history strategy"
  },
  "reasoning_effort": {
    "title": "Reasoning effort",
    "searchTerms": "Reasoning effort Choose the amount of reasoning requested from supported models. The practical difference depends on the selected model. Low Favours quicker, lighter reasoning for straightforward tasks. Medium Balances reasoning depth with response time. High Requests more reasoning for difficult tasks and may take longer or use more billed tokens.  thinking latency cost performance low medium high hard tasks"
  },
  "service_tier": {
    "title": "Processing tier",
    "searchTerms": "Processing tier This is called Service Tier in OpenAI and some provider documentation. It can affect request priority, availability, billing, and response time depending on your provider and account. Auto Lets the provider choose the tier. Default Requests the provider's standard tier. Flex Requests Flex processing. Priority Requests Priority processing.  provider processing billing latency auto default flex priority"
  },
  "function_tools": {
    "title": "Function tools and groups",
    "searchTerms": "Function tools and groups Function groups are optional. Existing ungrouped functions remain always available when enabled. An on-demand group sends only its compact name and description until the model decides the current task needs it. Built-in Functions are editable presets for native capabilities implemented directly by Extended OpenAI Conversation. Disabled tools remain configured and grouped, but their schemas are not sent to the model and calls are rejected. Always available Sends full definitions on every request, matching existing behaviour. Load when needed Withholds full definitions until the model requests the group; loaded groups remain available for the active conversation. Function YAML spec describes the model-facing schema and function tells Extended OpenAI how to execute it. Grouping does not change this format or its validation. Enabled Uses the same saved state in the Functions page and Home Assistant enable/disable actions. Disabled is a capability boundary, not a prompt preference.  group on demand load when needed tokens catalogue yaml spec schema validate execute built in enabled disabled"
  }
});

let contentModule = null;
let contentPromise = null;

function ensureHelpContentModule() {
  if (contentModule) return Promise.resolve(contentModule);
  if (!contentPromise) {
    contentPromise = import("./agent-config-help-content.js")
      .then((module) => {
        contentModule = module;
        return module;
      })
      .finally(() => { contentPromise = null; });
  }
  return contentPromise;
}

export function helpSearchTerms(key) {
  return HELP_INDEX[key]?.searchTerms || "";
}

export function helpButton(panel, key) {
  const entry = HELP_INDEX[key];
  if (!entry) throw new Error(`Unknown configuration help key: ${key}`);
  return `<button type="button" class="help-button" data-help="${panel._e(key)}" aria-label="More information about ${panel._e(entry.title)}" aria-haspopup="dialog" aria-expanded="false"><span aria-hidden="true">i</span></button>`;
}

export function helpPopover() {
  return `<dialog id="config-help-popover" class="help-popover" aria-labelledby="config-help-title"><div class="help-popover-header"><h2 id="config-help-title"></h2><button type="button" class="help-close" aria-label="Close help">&times;</button></div><div id="config-help-content" class="help-popover-content"></div></dialog>`;
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
  root.querySelectorAll("[data-help]").forEach((button) => button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const key = button.dataset.help;
    if (!HELP_INDEX[key]) return;
    closeHelp(panel, { restoreFocus: false });
    panel._helpTrigger = button;
    const token = (panel._eocHelpLoadToken || 0) + 1;
    panel._eocHelpLoadToken = token;
    try {
      const module = await ensureHelpContentModule();
      if (panel._eocHelpLoadToken !== token || panel._helpTrigger !== button || button.isConnected === false) return;
      const content = module.renderHelpContent(panel, key);
      if (!content) return;
      root.querySelector("#config-help-title").textContent = content.title;
      root.querySelector("#config-help-content").innerHTML = content.html;
      popover.showModal();
      button.setAttribute("aria-expanded", "true");
      positionPopover(popover, button);
      popover.querySelector(".help-close").focus();
    } catch (err) {
      if (panel._eocHelpLoadToken === token && panel._helpTrigger === button) {
        panel._helpTrigger = null;
        panel._toast?.(`Unable to load help: ${err.message || String(err)}`, true);
      }
    }
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
