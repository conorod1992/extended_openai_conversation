export function storeRuntimeGuidance(panel, result, agentId = panel._agentId) {
  if (!result?.configuration_guidance || panel._agentId !== agentId) return;
  panel._configurationGuidance = result.configuration_guidance;
  panel._configurationGuidanceAgentId = agentId;
  if (panel._result && typeof panel._result === "object") {
    panel._result.configuration_guidance = result.configuration_guidance;
  }
}
