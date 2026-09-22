import {
  applyRequestRuleMutation,
  bindRequestRulesCore,
  reconcileRequestRules,
  renderRequestRules,
  requestRulesDialog,
} from "./request-rules-ui-core.js";

let editorModule = null;
let editorPromise = null;
function ensureEditorModule() {
  if (editorModule) return Promise.resolve(editorModule);
  if (!editorPromise) {
    editorPromise = import("./request-rules-ui-impl.js")
      .then((module) => {
        editorModule = module;
        return module;
      })
      .finally(() => { editorPromise = null; });
  }
  return editorPromise;
}

let safeTesterModule = null;
let safeTesterPromise = null;
function ensureSafeTester(panel) {
  if (safeTesterModule) {
    safeTesterModule.bindRequestRuleMatchTester(panel);
    return Promise.resolve(safeTesterModule);
  }
  if (!safeTesterPromise) {
    safeTesterPromise = import("./request-rules-match-test-ui.js")
      .then((module) => {
        safeTesterModule = module;
        return module;
      })
      .finally(() => { safeTesterPromise = null; });
  }
  return safeTesterPromise.then((module) => {
    if (panel._viewKey?.() === "capabilities/request-rules") module.bindRequestRuleMatchTester(panel);
    return module;
  });
}

let liveTesterModule = null;
let liveTesterPromise = null;
function ensureLiveTester(panel) {
  if (liveTesterModule) {
    liveTesterModule.bindDecisionRequestRules(panel);
    return Promise.resolve(liveTesterModule);
  }
  if (!liveTesterPromise) {
    liveTesterPromise = import("./management-decision-guidance.js")
      .then((module) => {
        liveTesterModule = module;
        return module;
      })
      .finally(() => { liveTesterPromise = null; });
  }
  return liveTesterPromise.then((module) => {
    if (panel._viewKey?.() === "capabilities/request-rules") module.bindDecisionRequestRules(panel);
    return module;
  });
}

function warmEditorAfterPaint() {
  const warm = () => { void ensureEditorModule().catch(() => {}); };
  if (typeof globalThis.requestIdleCallback === "function") {
    globalThis.requestIdleCallback(warm, {timeout: 1500});
  } else {
    setTimeout(warm, 0);
  }
}

export function syncRequestRuleRoutingControls(root, efforts = null, selectedEffort = null) {
  const actionType = root?.querySelector?.("#rule-action-type");
  const matchType = root?.querySelector?.("#rule-match");
  const scope = root?.querySelector?.("#rule-scope");
  const continueToAi = root?.querySelector?.("#rule-continue-to-ai");
  const reasoning = root?.querySelector?.("#rule-reasoning");
  const help = root?.querySelector?.("#rule-routing-scope-help");
  if (!actionType || !matchType || !scope) return;
  const consumed = actionType.value === "model_routing"
    && ["equals", "sentence_pattern"].includes(matchType.value)
    && !(continueToAi?.checked ?? false);
  const requestOption = scope.querySelector('option[value="request"]');
  if (requestOption) requestOption.disabled = consumed;
  if (consumed) scope.value = "conversation";
  scope.disabled = consumed;
  if (reasoning && Array.isArray(efforts)) {
    const desired = selectedEffort ?? reasoning.value;
    reasoning.replaceChildren(
      ...["", ...efforts].map((value) => {
        const option = reasoning.ownerDocument.createElement("option");
        option.value = value;
        option.textContent = value ? value.charAt(0).toUpperCase() + value.slice(1) : "Keep current";
        return option;
      }),
    );
    reasoning.value = desired && efforts.includes(desired) ? desired : "";
  }
  if (help) help.textContent = consumed
    ? "This is a complete routing command. It is acknowledged locally and is not sent to the AI provider, so it must change or reset the rest of this conversation."
    : "This sends the original request to the AI unchanged after applying the route. This request only affects that provider call; Rest of this conversation also changes later requests.";
}

export function bindRequestRules(panel) {
  bindRequestRulesCore(panel, {
    openEditor: async (id) => {
      const module = await ensureEditorModule();
      if (panel._viewKey?.() === "capabilities/request-rules") module.openRequestRuleEditor(panel, id);
    },
    activateSafeTester: () => ensureSafeTester(panel),
    activateLiveTester: () => ensureLiveTester(panel),
  });
  warmEditorAfterPaint();
}

export async function formatRequestRuleMatchResult(panel, response) {
  const module = await import("./request-rules-match-test-ui.js");
  return module.formatRequestRuleMatchResult(panel, response);
}

export {
  applyRequestRuleMutation,
  reconcileRequestRules,
  renderRequestRules,
  requestRulesDialog,
};
