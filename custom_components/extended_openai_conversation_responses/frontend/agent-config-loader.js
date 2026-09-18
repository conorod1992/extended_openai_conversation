const MANAGEMENT_COPY_PATCHED = Symbol.for("extended-openai.management-copy-polish");

let implementation = null;
let loadPromise = null;

function replaceText(html, from, to) {
  return String(html || "").replace(from, to);
}

function replaceRenderedText(root, from, to) {
  root?.querySelectorAll?.("h1,h2,h3,p,small,strong,span").forEach((node) => {
    if (node.childElementCount === 0 && node.textContent?.trim() === from) node.textContent = to;
  });
}

function polishRenderedCopy(panel) {
  const root = panel?.shadowRoot;
  if (!root) return;

  replaceRenderedText(root, "Usage detail maintenance", "Manage usage history");
  root.querySelectorAll("p").forEach((node) => {
    if (node.textContent?.trim() === "Retention is available in the local Retention & maintenance subsection.") node.remove();
  });
  replaceRenderedText(
    root,
    "Daily, monthly, and lifetime totals are never removed by detail pruning.",
    "Overall daily, monthly and lifetime totals are not cleared.",
  );
  replaceRenderedText(
    root,
    "Cached input is request content the provider has seen before and can reuse. It is included in the total token count, but cached input is usually cheaper than uncached input when the provider supports discounted caching.",
    "Cached input is input recognised as cached by the provider. It is included in total tokens and may be billed at a lower rate.",
  );

  const continuityHelp = [...root.querySelectorAll("p.help")].find((node) =>
    node.textContent?.trim() === "Continuity is recent context used for follow-ups. The archive is retained history; configure its behavior below."
  );
  if (continuityHelp) {
    continuityHelp.textContent = "Recent context lets conversations continue; saved history is the archive you can review or search.";
    const firstSection = continuityHelp.parentElement?.querySelector("section");
    if (firstSection) continuityHelp.parentElement.insertBefore(continuityHelp, firstSection);
  }

  root.querySelectorAll("section.notice").forEach((section) => {
    const heading = section.querySelector("strong")?.textContent?.trim() || "";
    if (heading === "Conversation archive enabled" || heading === "Conversation archive disabled") section.remove();
  });

  root.querySelectorAll("section.content-card").forEach((section) => {
    const heading = section.querySelector(".section-heading h2");
    if (heading?.textContent?.trim() !== "Knowledge Library") return;
    heading.textContent = "Sources";
    const summary = section.querySelector(".section-heading p");
    const match = summary?.textContent?.trim().match(/^(\d[\d,]*) source(s?) stored locally for on-demand search\.$/);
    if (match) summary.textContent = `${match[1]} source${match[2]}`;
  });

  const healthFootnote = root.querySelector(".setup-health-footnote");
  if (healthFootnote) {
    const icon = healthFootnote.querySelector("ha-icon");
    healthFootnote.textContent = "Connection tests only run when you start one from Diagnostics.";
    if (icon) healthFootnote.prepend(icon, " ");
  }

  const broadcastIntro = root.querySelector(".broadcast-heading p");
  if (broadcastIntro) {
    broadcastIntro.textContent = "Send a spoken message to selected Assist satellites or the whole home. Busy satellites wait until they are free.";
  }
  const broadcastState = root.querySelector(".broadcast-toggle-row p");
  if (broadcastState) {
    if (broadcastState.textContent?.trim().startsWith("Broadcast is available to")) broadcastState.remove();
    else if (broadcastState.textContent?.trim().startsWith("Broadcast is off.")) broadcastState.textContent = "Broadcast is currently off.";
  }
}

export function installManagementCopyPolish(Panel) {
  // A constructor is the production API; registry callers remain supported.
  if (typeof Panel !== "function") {
    const registry = Panel || globalThis.customElements;
    if (!registry?.whenDefined) return Promise.resolve(false);
    return registry.whenDefined("extended-openai-management-panel").then(() => installManagementCopyPolish(registry.get("extended-openai-management-panel")));
  }
  const constructor = Panel;
  const prototype = constructor?.prototype;
  if (!prototype || prototype[MANAGEMENT_COPY_PATCHED]) return false;
  const originalRender = prototype._renderContent;
  prototype._renderContent = function(...args) {
    const shellRevision = this._eocShellRevision;
    const result = originalRender.apply(this, args);
    // Preserve the existing shell-only decoration boundary. The native renderer
    // no longer bypasses early wrappers on routine route updates.
    if (shellRevision === undefined || shellRevision !== this._eocShellRevision) {
      queueMicrotask(() => polishRenderedCopy(this));
    }
    return result;
  };
  prototype[MANAGEMENT_COPY_PATCHED] = true;
  return true;
}

export function polishConfigurationCopy(panel, html) {
  let result = String(html || "");

  result = replaceText(
    result,
    "Choose whether the assistant remembers the recent conversation when you speak to it again.",
    "Control how conversations continue between separate Assist requests.",
  );
  result = replaceText(
    result,
    '<small>Lets a new Assist request continue from your recent conversation instead of starting from scratch.</small>',
    "",
  );

  result = replaceText(result, '<small>Gives the assistant the current Home Assistant-local date and time.</small>', "");
  result = replaceText(result, '<small>Provides the current names and states of entities exposed to Assist.</small>', "");
  result = replaceText(
    result,
    "Leave an override blank to use the integration-maintained default. Custom values use the prompt-template environment.",
    "Customize how date, time and device information is added to the prompt. Leave a field blank to use the default.",
  );

  result = replaceText(result, '<small>Lets the assistant search the web for current information.</small>', "");
  result = replaceText(
    result,
    "Choose how much supporting material the provider returns with each web search.",
    "Choose how much information is returned with search results.",
  );
  result = replaceText(
    result,
    "Lets the assistant search reference material you maintain locally; it is separate from memories and conversation history.",
    "Allow the assistant to search your Knowledge Library when useful.",
  );

  result = replaceText(
    result,
    "Keep conversations locally so you can review them or let the assistant find earlier discussions.",
    "Manage saved conversation history and search.",
  );
  result = replaceText(result, '<small>Stores conversations locally for later review and optional search by the assistant.</small>', "");
  result = replaceText(result, "Start a new archive after (minutes)", "New conversation after inactivity (minutes)");
  result = replaceText(
    result,
    "After this much inactivity, the next message is saved as a new archived conversation.",
    "After this much inactivity, the next message starts a new saved conversation.",
  );

  result = replaceText(
    result,
    '<small>Choose whether the request uses no retained personal data, shared household data, a default user, or a device mapping.</small>',
    "",
  );
  result = replaceText(result, '<small>Choose whose data to use when a voice device is not assigned to a user.</small>', "");

  result = replaceText(
    result,
    "Clean text before it is spoken while retaining the original assistant response for history and context.",
    "Adjust text before it is spoken without changing the saved assistant response.",
  );
  result = replaceText(result, "Speech post-processing", "Clean responses for speech");
  result = replaceText(result, '<small>Cleans text before it is spoken without changing the retained original assistant response.</small>', "");
  result = replaceText(result, "Remove Markdown links and formatting", "Remove Markdown formatting");
  result = replaceText(
    result,
    "Removes Markdown links and formatting from progressive and completed spoken output.",
    "Stops formatting and Markdown links from being read aloud.",
  );
  result = replaceText(
    result,
    "Rules run on the completed response. Progressive TTS is disabled when custom rules are configured.",
    "Replace words or patterns in spoken responses. Streaming speech is disabled while custom replacements are active.",
  );

  result = replaceText(
    result,
    "Choose what happens when the conversation becomes too large for the model's context window.",
    "Choose how older conversation history is reduced when it becomes too large.",
  );
  result = replaceText(
    result,
    "Older conversation content is reduced when the provider reports more than this many input tokens.",
    "When history reaches this size, use the trimming method below.",
  );

  result = replaceText(result, "Maximum tool calls per conversation", "Tool-call limit per conversation");
  result = replaceText(
    result,
    "Stops the assistant after this many tool calls in one conversation to prevent runaway actions.",
    "Stops additional tool calls when this limit is reached.",
  );
  result = replaceText(result, "Recover from correctable tool errors", "Correct safe tool-call errors automatically");
  result = replaceText(
    result,
    "Lets the model correct a tool call only when Extended OpenAI can prove the failure happened before any action could start. It does not retry arbitrary failed actions.",
    "Lets the assistant correct and retry mistakes only when no action could already have started. Other failed actions are never retried automatically.",
  );

  result = replaceText(
    result,
    "Lightweight lexical is local and dependency-free. Hybrid semantic uses embeddings and falls back to lexical if unavailable.",
    "Semantic matching can find memories with related meaning, not just similar words. It falls back to local matching if unavailable.",
  );
  result = replaceText(result, ">Lightweight lexical<", ">Fast local matching<");
  result = replaceText(result, ">Hybrid semantic<", ">Semantic matching<");

  result = replaceText(result, "Usage detail retention", "Usage history retention");
  result = replaceText(
    result,
    "Aggregate and lifetime totals remain separate from these detailed records.",
    "Choose how long detailed usage records are kept. Overall totals are kept separately.",
  );
  result = replaceText(result, ">Request details<", ">Keep request details for<");
  result = replaceText(result, ">Run details<", ">Keep run details for<");
  result = replaceText(result, '<small>Controls how long detailed per-request usage records are retained.</small>', "");
  result = replaceText(result, '<small>Controls how long detailed conversation-run records are retained.</small>', "");

  return result;
}

function wrappedImplementation(module) {
  return {
    ...module,
    renderConfiguration(panel) {
      return polishConfigurationCopy(panel, module.renderConfiguration(panel));
    },
  };
}

export function getAgentConfigModule() {
  return implementation;
}

export async function ensureAgentConfigModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./agent-config-native-yaml.js")
      .then((module) => {
        implementation = wrappedImplementation(module);
        return implementation;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}
