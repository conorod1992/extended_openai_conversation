import {getConfigurationEditor} from "./management-route.js";

function renderConfiguration(panel, presentation) {
  const module = getConfigurationEditor();
  if (!module) return panel._loading?.() || '<div class="loading">Loading configuration…</div>';
  return module.renderConfiguration(panel, presentation);
}

function bindConfiguration(panel) {
  return getConfigurationEditor()?.bindConfiguration(panel);
}

export function bindCapabilities(panel) {
  const view = panel._viewKey();
  if (["capabilities/home-assistant", "capabilities/web-skills"].includes(view)) {
    bindConfiguration(panel);
  }
}

export {renderConfiguration};
