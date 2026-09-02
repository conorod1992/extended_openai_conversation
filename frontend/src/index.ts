import {EocSection} from "./components/eoc-section";
import {EocStateView} from "./components/eoc-state-view";
import {EocStatusBadge} from "./components/eoc-status-badge";
import {defineCustomElement} from "./registration";

export * from "./base/extended-openai-element";
export * from "./components/eoc-section";
export * from "./components/eoc-state-view";
export * from "./components/eoc-status-badge";
export * from "./events";
export * from "./format";
export * from "./registration";
export * from "./types";

if (typeof customElements !== "undefined") {
  defineCustomElement("eoc-section", EocSection);
  defineCustomElement("eoc-status-badge", EocStatusBadge);
  defineCustomElement("eoc-state-view", EocStateView);
}
