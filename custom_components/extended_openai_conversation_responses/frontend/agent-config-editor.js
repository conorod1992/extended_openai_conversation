import { renderConfiguration as renderBaseConfiguration } from "./agent-config-editor-base.js";

export * from "./agent-config-editor-base.js";

const DELAYED_CHOICE = (panel, enabled, checked) => `<label class="group-function-choice" data-local-intent-choice data-choice-search="delayed device commands scheduled deferred actions turn off later"><input type="checkbox" data-config="local_intent_delayed_commands_to_ai" data-type="boolean" ${checked ? "checked" : ""} ${enabled ? "" : "disabled"}><span><strong>Delayed device commands</strong><small>For example, “turn off the lights in 20 minutes”. Normal timers such as “set a 20 minute timer” can still stay local.</small></span></label>`;

function simplifyConfigurationMarkup(panel, html) {
  const config = panel._draft || panel._result?.config || {};
  const localEnabled = Boolean(config.local_intents_enabled);
  let result = html;

  // The page already has top-level navigation and an Assistant section picker.
  // Keep the content itself simple instead of adding a third navigation layer.
  result = result.replace(/\s*<nav class="config-jumps"[^>]*>[\s\S]*?<\/nav>\s*/, "\n    ");

  result = result.replace(
    /(<section id="config-local"[^>]*><div class="config-section-heading"><p class="eyebrow">Local handling<\/p><p>)[^<]*(<\/p><\/div>)/,
    "$1Let Extended OpenAI try Home Assistant's built-in commands after Request Rules, before using AI.$2"
  );

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
    .replace(
      "After Request Rules, simple commands Home Assistant already understands can run locally without an AI request. If Home Assistant cannot handle the request, Extended OpenAI continues as normal.",
      "After Request Rules, try Home Assistant's built-in commands first. Requests that do not match locally, or that you exclude below, continue to your Function Tools or AI model."
    )
    .replace(
      /\s*<div class="config-toggle setting" data-field="local_intent_delayed_commands_to_ai"[\s\S]*?<span class="switch-track" aria-hidden="true"><\/span><\/label><\/div>/,
      ""
    )
    .replace("Always send these command types to AI", "Send these command types to AI")
    .replace(
      "Select any Home Assistant command types that should skip local handling and continue to your Function Tools or AI model.",
      "Choose any commands that should skip local handling and continue to your Function Tools or AI model."
    );

  result = result.replace(
    '<div id="local-intent-list" class="group-function-choices">',
    `<div id="local-intent-list" class="group-function-choices">${DELAYED_CHOICE(panel, localEnabled, Boolean(config.local_intent_delayed_commands_to_ai))}`
  );

  return result;
}

export function renderConfiguration(panel) {
  return simplifyConfigurationMarkup(panel, renderBaseConfiguration(panel));
}
