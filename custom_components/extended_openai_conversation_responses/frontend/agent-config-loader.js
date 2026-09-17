import "./quiet-hours-ui.js";
import {polishConfigurationCopy} from "./agent-config-ux-copy.js";

let implementation = null;
let loadPromise = null;

export function getAgentConfigModule() {
  return implementation;
}

export async function ensureAgentConfigModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./agent-config-native-yaml.js")
      .then((module) => {
        implementation = {
          ...module,
          renderConfiguration(panel) {
            return polishConfigurationCopy(panel, module.renderConfiguration(panel));
          },
        };
        return implementation;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}
