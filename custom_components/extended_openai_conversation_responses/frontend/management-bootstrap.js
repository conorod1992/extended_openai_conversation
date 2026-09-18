import {installManagementCopyPolish} from "./agent-config-loader.js";
import {installManagementBrowser} from "./guest-mode-ui.js";
import {installManagementStateSafety} from "./management-state-safety.js";
import {installManagementFeatureStatus} from "./management-feature-status.js";
import {installManagementTemporaryMemory} from "./management-temporary-memory.js";
import {installManagementMemorySettings} from "./management-memory-settings.js";
import {installManagementCapabilitiesIA} from "./management-capabilities-ia.js";
import {installManagementVoiceIdentity} from "./management-voice-identity.js";
import {installManagementNavigationSearch} from "./management-navigation-search.js";
import {installManagementToolbarLayout} from "./management-toolbar-layout.js";
import {installManagementConfigurationClarity} from "./management-configuration-clarity.js";
import {installManagementConfigurationGuidance} from "./management-configuration-guidance.js";
import {installManagementDecisionGuidance} from "./management-decision-guidance.js";
import {installManagementConversationDefaultLabel} from "./management-conversation-default-label.js";
import {installManagementSettingsPolish} from "./management-settings-polish.js";
import {installManagementOverviewHealthClarity} from "./management-overview-health-clarity.js";

// Remaining feature decorators are intentionally temporary. Core lifecycle,
// request/mutation safety, permissions and performance now belong to the panel.
export function initializeManagementPanel(Panel) {
  installManagementCopyPolish(Panel);
  installManagementBrowser(Panel);
  installManagementStateSafety(Panel);
  installManagementFeatureStatus(Panel);
  installManagementTemporaryMemory(Panel);
  installManagementMemorySettings(Panel);
  installManagementCapabilitiesIA(Panel);
  installManagementVoiceIdentity(Panel);
  installManagementNavigationSearch(Panel);
  installManagementToolbarLayout(Panel);
  installManagementConfigurationClarity(Panel);
  installManagementConfigurationGuidance(Panel);
  installManagementDecisionGuidance(Panel);
  installManagementConversationDefaultLabel(Panel);
  installManagementSettingsPolish(Panel);
  installManagementOverviewHealthClarity(Panel);
}
