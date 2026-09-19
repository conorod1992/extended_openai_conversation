let implementation = null;
let loadPromise = null;

function replaceRenderedText(root, from, to) {
  root?.querySelectorAll?.("h1,h2,h3,p,small,strong,span").forEach((node) => {
    if (node.childElementCount === 0 && node.textContent?.trim() === from) node.textContent = to;
  });
}

export function polishRenderedCopy(panel) {
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

export function getAgentConfigModule() {
  return implementation;
}

export async function ensureAgentConfigModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./agent-config-native-yaml.js")
      .then((module) => {
        implementation = module;
        return implementation;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}
