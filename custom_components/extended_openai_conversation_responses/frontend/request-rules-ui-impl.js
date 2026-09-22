import {applyRequestRuleMutation, fuzzyThresholdValue, matchingControls} from "./request-rules-ui-core.js";

export function requestRulesDialog() {
  return `<dialog id="rule-dialog" class="editor-dialog wide request-rule-dialog" aria-labelledby="rule-dialog-title"><form id="rule-form"><div class="dialog-header"><h2 id="rule-dialog-title">Create Request Rule</h2><button type="button" class="icon rule-close" aria-label="Close">×</button></div><div class="dialog-body"><div class="form-grid"><label>Rule name<input id="rule-name" required maxlength="120" placeholder="Shopping list"></label><label class="toggle"><span>Enabled</span><input id="rule-enabled-edit" type="checkbox" checked></label></div><section><h3>1. What will you say?</h3><label>Trigger phrases or patterns<textarea id="rule-phrases" required placeholder="Add {item} to my shopping list"></textarea><small>Put each alternative on a new line. Alternatives must use the same variable names.</small></label><div id="sentence-pattern-builder" class="section-actions" hidden><span class="help">Insert pattern:</span><button type="button" class="secondary pattern-helper" data-pattern-helper="optional">Optional</button><button type="button" class="secondary pattern-helper" data-pattern-helper="choice">Choice</button><button type="button" class="secondary pattern-helper" data-pattern-helper="variable">Variable</button><button type="button" class="secondary pattern-helper" data-pattern-helper="range">Number range</button></div><div id="rule-slot-help" class="notice" hidden><strong>Variable values</strong><p>Variable values let part of the request change each time. You can use the captured value in actions or responses.</p><p id="rule-slot-list"></p></div><label>How should it match?<select id="rule-match"><option value="equals">Equals</option><option value="starts_with">Starts with</option><option value="ends_with">Ends with</option><option value="contains">Contains</option><option value="sentence_pattern">ExtendedOpenAI sentence pattern</option></select></label><div id="sentence-pattern-help" class="notice" hidden><strong>Sentence-pattern syntax</strong><p>Use <code>[optional words]</code>, <code>(one|two)</code>, free-text values such as <code>{room}</code>, constrained values such as <code>{room=kitchen|bedroom}</code>, and integer ranges such as <code>{level=0..100}</code>. Escape syntax characters with <code>\\</code>, including <code>\\|</code> inside choices. Sentence-ending punctuation is tolerated. This is ExtendedOpenAI syntax; named expansions and permutations are not supported.</p></div></section><section><h3>2. What should happen?</h3><label>Behaviour<select id="rule-action-type"><option value="local_action">Run actions locally</option><option value="model_routing">Route through AI with different settings</option></select></label><div id="rule-local-config"><p class="help">Build a native Home Assistant action sequence that runs locally without asking the AI model. Conditions, delays, choose, repeat, parallel, and templates use the same editor and syntax as scripts and automations.</p><div id="rule-action-sequence-host"></div><div id="rule-action-slot-help" class="notice" hidden><strong>Captured values in actions</strong><p id="rule-action-slot-list"></p><p>Use a captured value as a script variable, for example <code>{{ item }}</code>. The same values are also available under <code>request.slots</code>.</p><p>To call an enabled configured function, add <code>extended_openai_conversation_responses.call_function</code> and provide its name and arguments.</p></div></div><div id="rule-routing-config" hidden><p class="help"><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> are complete commands by default. Enable <strong>Continue to AI</strong> to send the original request to the provider unchanged after applying the route. Broader Starts/Ends/Contains matches continue to the provider by default.</p><p class="help" id="rule-routing-scope-help"></p><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Continue to AI</span><small>After applying these routing settings, send the original request to the AI provider.</small></span><input id="rule-continue-to-ai" type="checkbox" checked></label><div class="form-grid"><label>Model<input id="rule-model" placeholder="gpt-5-mini"></label><label>Reasoning effort<select id="rule-reasoning"><option value="">Keep current</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label>Scope<select id="rule-scope"><option value="request">This request only</option><option value="conversation">Rest of this conversation</option></select></label><label class="toggle"><span>Reset to configured defaults</span><input id="rule-reset" type="checkbox"></label></div></div></section><section><h3>3. What should the assistant say?</h3><div id="rule-local-responses" class="form-grid"><label>Success response<input id="rule-success" value="Done"><small>You can include a captured value such as <code>{item}</code>.</small></label><label>Failure response<input id="rule-failure" value="Sorry, that did not work"></label></div><p id="rule-routing-ai-response" class="help" hidden>The AI provider will generate the response.</p><label id="rule-routing-response" hidden>Acknowledgement<input id="rule-routing-success" value="Updated"></label></section><details id="rule-advanced" class="advanced-context-formatting eoc-details-base"><summary>Advanced matching and action configuration</summary><label>Matching behaviour<select id="rule-matching-behavior"><option value="defaults">Use default settings</option><option value="custom">Customize for this rule</option></select></label>${matchingControls("rule", {word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90}, true)}<p class="help">Sentence patterns use ExtendedOpenAI's bounded matcher, so fuzzy matching, wording alternatives, and word-form normalization do not apply. Advanced Home Assistant JSON can use <code>{slot}</code> in text values.</p></details><div id="rule-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary rule-close">Cancel</button><button type="submit" id="rule-save">Save</button></div></form></dialog>`;
}


export function capturedSlotNames(text) {
  // Discover editor bindings without interpreting constrained values as syntax.
  // Backend validation remains authoritative for incomplete/invalid patterns.
  const source = String(text || ""), names = new Set();
  for (let index = 0; index < source.length; index += 1) {
    if (source[index] === "\\") { index += 1; continue; }
    if (source[index] !== "{") continue;
    let body = "", closed = false;
    while (++index < source.length) {
      if (source[index] === "\\") {
        body += source[index];
        if (++index < source.length) body += source[index];
      } else if (source[index] === "}") {
        closed = true;
        break;
      } else body += source[index];
    }
    const name = body.split("=", 1)[0].trim();
    if (closed && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(name)) names.add(name);
  }
  return [...names];
}

const setFuzzyState = (root, prefix) => { const toggle = root.querySelector(`#${prefix}-fuzzy`), select = root.querySelector(`#${prefix}-threshold`); if (!toggle || !select) return; select.disabled = !toggle.checked; select.closest(".fuzzy-sensitivity")?.classList.toggle("is-disabled", !toggle.checked); };


export function createRequestRuleActionSelector(panel, host) {
  const actionSelector = host.ownerDocument.createElement("ha-selector");
  actionSelector.hass = panel._hass;
  actionSelector.selector = {action:{}};
  actionSelector.value = [];
  actionSelector.addEventListener("value-changed", (event) => {
    actionSelector.value = event.detail.value || [];
  });
  host.replaceChildren(actionSelector);
  return actionSelector;
}

export function loadRequestRuleActions(actionSelector, rule) {
  actionSelector.value = rule?.action?.actions || [{action:"script.turn_on", target:{}}];
}

export function readRequestRuleActions(actionSelector) {
  return actionSelector.value || [];
}



let modelCatalogModule = null;
let modelCatalogPromise = null;
function ensureModelCatalog() {
  if (modelCatalogModule) return Promise.resolve(modelCatalogModule);
  if (!modelCatalogPromise) {
    modelCatalogPromise = import("./model-catalog.js").then((module) => {
      modelCatalogModule = module;
      return module;
    }).finally(() => { modelCatalogPromise = null; });
  }
  return modelCatalogPromise;
}

function setReasoningOptions(root, efforts, selected = "") {
  const select = root?.querySelector("#rule-reasoning");
  if (!select) return;
  const values = Array.isArray(efforts) ? efforts : [];
  const keepCurrent = select.ownerDocument.createElement("option");
  keepCurrent.value = "";
  keepCurrent.textContent = "Keep current";
  const options = [keepCurrent, ...values.map((value) => {
    const option = select.ownerDocument.createElement("option");
    option.value = value;
    option.textContent = value.charAt(0).toUpperCase() + value.slice(1);
    return option;
  })];
  select.replaceChildren(...options);
  select.value = selected && values.includes(selected) ? selected : "";
}

export function syncRequestRuleRoutingControls(root, efforts = null, selectedEffort = null) {
  if (!root) return;
  const actionType = root.querySelector("#rule-action-type");
  const matchType = root.querySelector("#rule-match");
  const scope = root.querySelector("#rule-scope");
  const continueToAi = root.querySelector("#rule-continue-to-ai");
  const reasoning = root.querySelector("#rule-reasoning");
  const help = root.querySelector("#rule-routing-scope-help");
  if (!actionType || !matchType || !scope) return;
  const modelRouting = actionType.value === "model_routing";
  const consumed = modelRouting && ["equals", "sentence_pattern"].includes(matchType.value) && !(continueToAi?.checked ?? false);
  const requestOption = scope.querySelector('option[value="request"]');
  if (requestOption) requestOption.disabled = consumed;
  if (consumed) scope.value = "conversation";
  scope.disabled = consumed;
  if (reasoning && efforts) {
    const desired = selectedEffort ?? reasoning.value;
    setReasoningOptions(root, efforts, desired);
  }
  if (help) help.textContent = consumed
    ? "This is a complete routing command. It is acknowledged locally and is not sent to the AI provider, so it must change or reset the rest of this conversation."
    : "This sends the original request to the AI unchanged after applying the route. This request only affects that provider call; Rest of this conversation also changes later requests.";
}

function ensureDialog(panel) {
  const root = panel.shadowRoot;
  let dialog = root.querySelector("#rule-dialog");
  if (dialog) return dialog;
  const template = document.createElement("template");
  template.innerHTML = requestRulesDialog();
  dialog = template.content.firstElementChild;
  (root.querySelector("#eoc-dialog-host") || root).append(dialog);
  return dialog;
}

function editorState(panel) {
  panel._eocRuleEditorState ||= {bound:false, actionSelector:null, revision:null, modelRevision:0};
  return panel._eocRuleEditorState;
}

function refreshEditor(panel) {
  const root=panel.shadowRoot, q=(selector)=>root.querySelector(selector);
  const local=q("#rule-action-type").value==="local_action";
  const grammar=q("#rule-match").value==="sentence_pattern";
  const slots=capturedSlotNames(q("#rule-phrases").value);
  q("#rule-local-config").hidden=!local;
  q("#rule-routing-config").hidden=local;
  q("#rule-local-responses").hidden=!local;
  const continueToAi=q("#rule-continue-to-ai")?.checked ?? true;
  q("#rule-routing-response").hidden=local||continueToAi;
  q("#rule-routing-ai-response").hidden=local||!continueToAi;
  q("#sentence-pattern-help").hidden=!grammar;
  q("#sentence-pattern-builder").hidden=!grammar;
  q("#rule-slot-help").hidden=!slots.length;
  q("#rule-slot-list").textContent=slots.length?`Captured values: ${slots.join(", ")}`:"";
  q("#rule-action-slot-help").hidden=!slots.length;
  q("#rule-action-slot-list").textContent=slots.length?slots.map((name)=>`{{ ${name} }}`).join(", "):"";
  q("#rule-matching-behavior").disabled=grammar;
  q("#rule-matching-controls").hidden=grammar||q("#rule-matching-behavior").value!=="custom";
  if(slots.length)q("#rule-match").value="sentence_pattern";
  syncRequestRuleRoutingControls(root);
  if(local)return;
  const model=q("#rule-model")?.value.trim();
  if(!model)return;
  const state=editorState(panel), current=++state.modelRevision;
  const selected=(panel._result?.rules||[]).find((item)=>item.id===panel._editingRuleId)?.action?.reasoning_effort || q("#rule-reasoning")?.value || "";
  void ensureModelCatalog().then((module)=>module.lookupModelData(panel,model)).then((data)=>{
    if(current===state.modelRevision && root.querySelector("#rule-dialog")?.open) syncRequestRuleRoutingControls(root,data.reasoning_effort_options,selected);
  }).catch((err)=>panel._toast(`Unable to load model choices: ${err.message || String(err)}`,true));
}

export function bindRequestRuleEditor(panel) {
  const root=panel.shadowRoot, dialog=ensureDialog(panel), state=editorState(panel);
  if(state.bound)return dialog;
  state.bound=true;
  const q=(selector)=>root.querySelector(selector);
  state.actionSelector=createRequestRuleActionSelector(panel,q("#rule-action-sequence-host"));
  q("#rule-fuzzy")?.addEventListener("change",()=>setFuzzyState(root,"rule"));
  q("#rule-action-type")?.addEventListener("change",()=>refreshEditor(panel));
  q("#rule-match")?.addEventListener("change",()=>refreshEditor(panel));
  q("#rule-continue-to-ai")?.addEventListener("change",()=>refreshEditor(panel));
  q("#rule-model")?.addEventListener("input",()=>refreshEditor(panel));
  q("#rule-scope")?.addEventListener("change",()=>syncRequestRuleRoutingControls(root));
  q("#rule-reset")?.addEventListener("change",()=>syncRequestRuleRoutingControls(root));
  root.querySelectorAll(".pattern-helper").forEach((button)=>button.addEventListener("click",()=>{
    const textarea=q("#rule-phrases");
    const result=applySentencePatternHelper(textarea.value,textarea.selectionStart,textarea.selectionEnd,button.dataset.patternHelper);
    textarea.value=result.value;textarea.focus();textarea.setSelectionRange(result.selectionStart,result.selectionEnd);refreshEditor(panel);
  }));
  q("#rule-phrases")?.addEventListener("input",()=>refreshEditor(panel));
  q("#rule-matching-behavior")?.addEventListener("change",()=>refreshEditor(panel));
  root.querySelectorAll(".rule-close").forEach((button)=>button.addEventListener("click",()=>dialog.close()));
  q("#rule-form")?.addEventListener("submit",async(event)=>{
    event.preventDefault();
    const save=q("#rule-save");if(save.disabled)return;
    panel._setSaving(save,true);
    try{
      const actionType=q("#rule-action-type").value;
      const actions=readRequestRuleActions(state.actionSelector);
      const rules=panel._result?.rules||[];
      const rule={name:q("#rule-name").value,enabled:q("#rule-enabled-edit").checked,phrases:q("#rule-phrases").value.split("\n").map((item)=>item.trim()).filter(Boolean),match_type:q("#rule-match").value,action_type:actionType,action:actionType==="local_action"?{actions,success_response:q("#rule-success").value,failure_response:q("#rule-failure").value}:{model:q("#rule-model").value,reasoning_effort:q("#rule-reasoning").value,scope:q("#rule-scope").value,reset:q("#rule-reset").checked,continue_to_ai:q("#rule-continue-to-ai").checked,success_response:q("#rule-routing-success").value},matching_behavior:q("#rule-matching-behavior").value,matching:{word_forms:q("#rule-word-forms").checked,wording_alternatives:q("#rule-wording").checked,fuzzy:q("#rule-fuzzy").checked,fuzzy_threshold:fuzzyThresholdValue(q("#rule-threshold").value)},order:rules.find((item)=>item.id===panel._editingRuleId)?.order??rules.length};
      const action=panel._editingRuleId?"update":"create";
      const result=await panel._call("request_rules",action,{...(panel._editingRuleId?{rule_id:panel._editingRuleId}:{}),rule,revision:state.revision});
      applyRequestRuleMutation(panel,action,result,{ruleId:panel._editingRuleId});
      dialog.close();
    }catch(err){q("#rule-error").textContent=err.message||String(err);}
    finally{panel._setSaving(save,false);}
  });
  return dialog;
}

export function openRequestRuleEditor(panel,id=null) {
  const dialog=bindRequestRuleEditor(panel), root=panel.shadowRoot, q=(selector)=>root.querySelector(selector), state=editorState(panel);
  state.revision=panel._result?.revision;
  const rule=(panel._result?.rules||[]).find((item)=>item.id===id);
  panel._editingRuleId=id;
  q("#rule-dialog-title").textContent=rule?"Edit Request Rule":"Create Request Rule";
  q("#rule-name").value=rule?.name||"";
  q("#rule-enabled-edit").checked=rule?.enabled??true;
  q("#rule-phrases").value=(rule?.phrases||[]).join("\n");
  q("#rule-match").value=rule?.match_type||"equals";
  q("#rule-action-type").value=rule?.action_type||"local_action";
  q("#rule-success").value=rule?.action?.success_response||"Done";
  q("#rule-failure").value=rule?.action?.failure_response||"Sorry, that did not work";
  q("#rule-model").value=rule?.action?.model||"";
  q("#rule-reasoning").value=rule?.action?.reasoning_effort||"";
  q("#rule-scope").value=rule?.action?.scope||"request";
  q("#rule-reset").checked=rule?.action?.reset||false;
  q("#rule-continue-to-ai").checked=rule?.action_type==="model_routing"?(rule?.action?.continue_to_ai??!["equals","sentence_pattern"].includes(rule?.match_type)):true;
  q("#rule-routing-success").value=rule?.action?.success_response||"Updated";
  q("#rule-matching-behavior").value=rule?.matching_behavior||"defaults";
  q("#rule-word-forms").checked=rule?.matching?.word_forms??true;
  q("#rule-wording").checked=rule?.matching?.wording_alternatives??true;
  q("#rule-fuzzy").checked=rule?.matching?.fuzzy??false;
  q("#rule-threshold").value=String(fuzzyThresholdValue(rule?.matching?.fuzzy_threshold??90));
  loadRequestRuleActions(state.actionSelector,rule);
  setFuzzyState(root,"rule");q("#rule-error").textContent="";
  dialog.showModal();
  refreshEditor(panel);
  panel._captureDialogBaseline?.(dialog);
}
