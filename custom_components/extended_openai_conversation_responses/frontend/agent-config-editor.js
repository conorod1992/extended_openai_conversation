import "./management-bootstrap.js";
const {ensureAgentConfigModule, getAgentConfigModule} = await import("./agent-config-loader.js");

if (typeof document === "undefined") await ensureAgentConfigModule();

const DELAYED_CHOICE = (panel, enabled, checked) => `<label class="group-function-choice" data-local-intent-choice data-choice-search="delayed device commands scheduled deferred actions turn off later"><input type="checkbox" data-config="local_intent_delayed_commands_to_ai" data-type="boolean" ${checked ? "checked" : ""} ${enabled ? "" : "disabled"}><span><strong>Delayed device commands</strong><small>For example, “turn off the lights in 20 minutes”. Normal timers such as “set a 20 minute timer” can still stay local.</small></span></label>`;

function simplifyConfigurationMarkupLegacy(panel, html) {
  const config = panel._draft || panel._result?.config || {};
  const localEnabled = Boolean(config.local_intents_enabled);
  let result = html;
  result = result.replace(/\s*<nav class="config-jumps"[^>]*>[\s\S]*?<\/nav>\s*/, "\n    ");
  result = result.replace(/(<section id="config-local"[^>]*><div class="config-section-heading"><p class="eyebrow">Local handling<\/p><p>)[^<]*(<\/p><\/div>)/, "$1Let Extended OpenAI try Home Assistant's built-in commands after Request Rules, before using AI.$2");
  const localHeading = /(<section id="config-local"[^>]*><div class="config-section-heading">[\s\S]*?<\/div>)/;
  result = result.replace(localHeading, `$1
    <div class="notice local-handling-explainer">
      <strong>How this differs from Home Assistant's “Prefer local handling”</strong>
      <p>Home Assistant's own option runs before a request reaches Extended OpenAI. That is simple and fast, but it means Extended OpenAI cannot apply Request Rules or choose that particular command for a Function Tool instead.</p>
      <p><strong>Extended OpenAI local handling</strong> runs after Request Rules. It can still use Home Assistant's fast built-in commands, while letting you send selected command types on to your Function Tools or AI model.</p>
      <p><strong>Example:</strong> “Turn on the kitchen light” can stay local, while “turn off the kitchen light in 20 minutes” can be sent to a deferred-action Function Tool.</p>
    </div>`);
  result = result
    .replace("Try Home Assistant before AI", "Use Extended OpenAI local handling")
    .replace("After Request Rules, simple commands Home Assistant already understands can run locally without an AI request. If Home Assistant cannot handle the request, Extended OpenAI continues as normal.", "After Request Rules, try Home Assistant's built-in commands first. Requests that do not match locally, or that you exclude below, continue to your Function Tools or AI model.")
    .replace(/\s*<div class="config-toggle setting" data-field="local_intent_delayed_commands_to_ai"[\s\S]*?<span class="switch-track" aria-hidden="true"><\/span><\/label><\/div>/, "")
    .replace("Always send these command types to AI", "Send these command types to AI")
    .replace("Select any Home Assistant command types that should skip local handling and continue to your Function Tools or AI model.", "Choose any commands that should skip local handling and continue to your Function Tools or AI model.");
  return result.replace('<div id="local-intent-list" class="group-function-choices">', `<div id="local-intent-list" class="group-function-choices">${DELAYED_CHOICE(panel, localEnabled, Boolean(config.local_intent_delayed_commands_to_ai))}`);
}

function simplifyConfigurationMarkup(panel, html) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return simplifyConfigurationMarkupLegacy(panel, html);
  const config = panel._draft || panel._result?.config || {};
  const template = document.createElement("template");
  template.innerHTML = html;
  const root = template.content;
  root.querySelector(".config-jumps")?.remove();

  const local = root.querySelector("#config-local");
  if (!local) return template.innerHTML;
  const heading = local.querySelector(".config-section-heading");
  const description = heading?.querySelector("p:last-child");
  if (description) description.textContent = "Let Extended OpenAI try Home Assistant's built-in commands after Request Rules, before using AI.";
  heading?.insertAdjacentHTML("afterend", `<div class="notice local-handling-explainer"><strong>How this differs from Home Assistant's “Prefer local handling”</strong><p>Home Assistant's own option runs before a request reaches Extended OpenAI. That is simple and fast, but it means Extended OpenAI cannot apply Request Rules or choose that particular command for a Function Tool instead.</p><p><strong>Extended OpenAI local handling</strong> runs after Request Rules. It can still use Home Assistant's fast built-in commands, while letting you send selected command types on to your Function Tools or AI model.</p><p><strong>Example:</strong> “Turn on the kitchen light” can stay local, while “turn off the kitchen light in 20 minutes” can be sent to a deferred-action Function Tool.</p></div>`);

  const enabledSetting = local.querySelector('[data-field="local_intents_enabled"]');
  const enabledLabel = enabledSetting?.querySelector("strong");
  const enabledDescription = enabledSetting?.querySelector("small");
  if (enabledLabel) enabledLabel.textContent = "Use Extended OpenAI local handling";
  if (enabledDescription) enabledDescription.textContent = "After Request Rules, try Home Assistant's built-in commands first. Requests that do not match locally, or that you exclude below, continue to your Function Tools or AI model.";
  local.querySelector('[data-field="local_intent_delayed_commands_to_ai"]')?.remove();

  const exceptions = local.querySelector('[data-search*="local handling exceptions"] .subheading');
  const exceptionsHeading = exceptions?.querySelector("h3");
  const exceptionsDescription = exceptions?.querySelector("p");
  if (exceptionsHeading) exceptionsHeading.textContent = "Send these command types to AI";
  if (exceptionsDescription) exceptionsDescription.textContent = "Choose any commands that should skip local handling and continue to your Function Tools or AI model.";

  local.querySelector("#local-intent-list")?.insertAdjacentHTML("afterbegin", DELAYED_CHOICE(panel, Boolean(config.local_intents_enabled), Boolean(config.local_intent_delayed_commands_to_ai)));
  return template.innerHTML;
}

function requiredImplementation(name) {
  const module = getAgentConfigModule();
  if (!module) throw new Error(`Agent configuration module is not loaded before ${name}`);
  return module;
}

function queueRender(panel) {
  void ensureAgentConfigModule()
    .then(() => panel?._render?.())
    .catch((err) => {
      if (!panel) return;
      panel._error = `Unable to load configuration editor: ${err.message || String(err)}`;
      panel._render?.();
    });
}

export function renderConfiguration(panel) {
  const module = getAgentConfigModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading configuration…</div>';
  }
  return simplifyConfigurationMarkup(panel, module.renderConfiguration(panel));
}

export function bindConfiguration(panel) {
  const module = getAgentConfigModule();
  if (!module) return queueRender(panel);
  return module.bindConfiguration(panel);
}

export function renderTools(panel) {
  const module = getAgentConfigModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading Functions…</div>';
  }
  return module.renderTools(panel);
}

export function bindTools(panel) {
  const module = getAgentConfigModule();
  if (!module) return queueRender(panel);
  return module.bindTools(panel);
}

export function configurationDialogs(...args) {
  return getAgentConfigModule()?.configurationDialogs(...args) || "";
}

export function restoreDialog(...args) {
  return getAgentConfigModule()?.restoreDialog(...args) || "";
}

export function configurationChoiceLabel(...args) {
  return requiredImplementation("configurationChoiceLabel").configurationChoiceLabel(...args);
}

export function copyTextToClipboard(...args) {
  return requiredImplementation("copyTextToClipboard").copyTextToClipboard(...args);
}

export function functionGroupIdFromName(...args) {
  return requiredImplementation("functionGroupIdFromName").functionGroupIdFromName(...args);
}

export function isFunctionToolEnabled(...args) {
  return requiredImplementation("isFunctionToolEnabled").isFunctionToolEnabled(...args);
}

export function functionToolCountLabel(...args) {
  return requiredImplementation("functionToolCountLabel").functionToolCountLabel(...args);
}

export function backupSummaryLines(...args) {
  return requiredImplementation("backupSummaryLines").backupSummaryLines(...args);
}

export function canReplaceToolYamlWithoutConfirmation(...args) {
  return requiredImplementation("canReplaceToolYamlWithoutConfirmation").canReplaceToolYamlWithoutConfirmation(...args);
}

export function matchesFunctionSearch(...args) {
  return requiredImplementation("matchesFunctionSearch").matchesFunctionSearch(...args);
}

export function deleteFunctionGroup(...args) {
  return requiredImplementation("deleteFunctionGroup").deleteFunctionGroup(...args);
}

export function categorizeFunctionTools(...args) {
  return requiredImplementation("categorizeFunctionTools").categorizeFunctionTools(...args);
}

export function saveBar(...args) {
  return requiredImplementation("saveBar").saveBar(...args);
}

export function synchronizePersistedFunctions(...args) {
  return requiredImplementation("synchronizePersistedFunctions").synchronizePersistedFunctions(...args);
}
