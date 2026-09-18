import {installManagementCopyPolish} from "./agent-config-loader.js";
import {installManagementNavigationSearch} from "./management-navigation-search.js";
import {installManagementToolbarLayout} from "./management-toolbar-layout.js";
import {installManagementConfigurationClarity} from "./management-configuration-clarity.js";
import {installManagementConfigurationGuidance} from "./management-configuration-guidance.js";
import {installManagementDecisionGuidance} from "./management-decision-guidance.js";
import {installManagementSettingsPolish} from "./management-settings-polish.js";
import {installManagementOverviewHealthClarity} from "./management-overview-health-clarity.js";

// Remaining visual and state-safety decorators are intentionally temporary.
// Core lifecycle and Data/Memory/Capabilities features are explicitly composed.
export function initializeManagementPanel(Panel) {
  installManagementCopyPolish(Panel);
  installManagementNavigationSearch(Panel);
  installManagementToolbarLayout(Panel);
  installManagementConfigurationClarity(Panel);
  installManagementConfigurationGuidance(Panel);
  installManagementDecisionGuidance(Panel);
  installManagementSettingsPolish(Panel);
  installManagementOverviewHealthClarity(Panel);
}
