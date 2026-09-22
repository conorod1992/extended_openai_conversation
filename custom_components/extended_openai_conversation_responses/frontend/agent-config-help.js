const HELP_TITLES = Object.freeze({
  "api_mode": "Provider API format",
  "continue_conversation": "Listen for a follow-up",
  "conversation_continuity": "Remember recent conversation",
  "conversation_timeout": "Conversation timeout",
  "web_search_context": "Web search detail",
  "knowledge_library": "Knowledge, memory, and archive",
  "memory_mode": "Persistent memory modes",
  "temporary_memory": "Temporary memory",
  "archive_model_search": "Model archive search",
  "shared_archive": "Shared-household archive",
  "archive_session_timeout": "Archive session timeout",
  "voice_scope_policy": "When the speaker is not identified",
  "voice_unmapped_policy": "When a device has no mapping",
  "shared_memory_mode": "Shared household memory",
  "voice_device_mappings": "Voice device assignments",
  "custom_replacements": "Custom spoken replacements",
  "context_threshold": "Conversation history token limit",
  "context_truncation": "Conversation history trimming",
  "reasoning_effort": "Reasoning effort",
  "service_tier": "Processing tier",
  "function_tools": "Function tools and groups"
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

export function helpButton(panel, key) {
  const title = HELP_TITLES[key];
  if (!title) throw new Error(`Unknown configuration help key: ${key}`);
  return `<button type="button" class="help-button" data-help="${panel._e(key)}" aria-label="More information about ${panel._e(title)}" aria-haspopup="dialog" aria-expanded="false"><span aria-hidden="true">i</span></button>`;
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
    if (!HELP_TITLES[key]) return;
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
