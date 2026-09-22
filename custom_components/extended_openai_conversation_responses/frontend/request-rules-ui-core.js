import {adoptKeyedElements, elementFromMarkup, keyedElement, placeChildren, pruneKeys} from "./keyed-collection.js";

export const fuzzyThresholdValue = (value) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 70 && parsed <= 100 ? parsed : 90;
};
const matchLabel = (value) => ({equals:"Equals",starts_with:"Starts with",ends_with:"Ends with",contains:"Contains",sentence_pattern:"Sentence pattern"}[value] || value);
const titleCase = (value) => String(value || "").replaceAll("_"," ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export function requestRuleSummary(rule = {}, defaults = {}) {
  const phrases = Array.isArray(rule.phrases) ? rule.phrases : [];
  const match = matchLabel(rule.match_type || "match");
  const phraseLabel = `${phrases.length} trigger phrase${phrases.length === 1 ? "" : "s"}`;
  let matching;
  if (rule.match_type === "sentence_pattern") {
    matching = `${phraseLabel} · ${match} · Hassil grammar`;
  } else {
    const source = rule.matching_behavior === "defaults" ? "Default matching" : "Custom matching";
    const settings = rule.matching_behavior === "defaults" ? defaults : (rule.matching || {});
    const threshold = Number(settings.fuzzy_threshold ?? 90);
    const fuzzyLabel = threshold >= 93 ? "conservative" : threshold >= 88 ? "normal" : "tolerant";
    const fuzzy = settings.fuzzy ? ` · Fuzzy fallback: ${titleCase(fuzzyLabel)}` : "";
    matching = `${phraseLabel} · ${match} · ${source}${fuzzy}`;
  }
  if (rule.action_type === "local_action") {
    const actions = Array.isArray(rule.action?.actions) ? rule.action.actions : [];
    const response = String(rule.action?.success_response || "").trim();
    return {
      action: `Runs ${actions.length} local step${actions.length === 1 ? "" : "s"} without an AI request${response ? ` · replies “${response}”` : ""}`,
      matching,
      hiddenPhrases: Math.max(0, phrases.length - 4),
    };
  }
  if (rule.action?.reset) return {
    action: "Returns model and reasoning to the assistant's configured defaults for the active conversation.",
    matching,
    hiddenPhrases: Math.max(0, phrases.length - 4),
  };
  const model = rule.action?.model ? `Model: ${rule.action.model}` : "Keep current model";
  const reasoning = rule.action?.reasoning_effort ? `${titleCase(rule.action.reasoning_effort)} reasoning` : "Keep current reasoning";
  const scope = rule.action?.scope === "conversation" ? "rest of conversation" : "this request only";
  return {action: `${model} · ${reasoning} · ${scope}`, matching, hiddenPhrases: Math.max(0, phrases.length - 4)};
}

export async function recoverRequestRuleMutation(panel, error, label) {
  const message = error?.message || String(error || "Unknown error");
  panel._toast?.(`${label}: ${message}`, true);
  const cacheKey = panel._sectionCacheKey?.();
  if (cacheKey) panel._sectionCache?.delete(cacheKey);
  try { await panel._loadSection(true); }
  catch (refreshError) {
    panel._toast?.(`Unable to refresh Request Rules: ${refreshError?.message || String(refreshError || "Unknown error")}`, true);
  }
}

export const matchingControls = (prefix, values, hidden = false) => `<div id="${prefix}-matching-controls" class="matching-settings" ${hidden ? "hidden" : ""}><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Normalize word forms</span><small>Treats simple variations such as “light” and “lights” as the same.</small></span><input id="${prefix}-word-forms" type="checkbox" ${values.word_forms ? "checked" : ""}></label><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Wording alternatives</span><small>Uses your saved alternative phrases, such as “switch on” matching “turn on”.</small></span><input id="${prefix}-wording" type="checkbox" ${values.wording_alternatives ? "checked" : ""}></label><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Fuzzy matching</span><small>Allows small speech-recognition or typing mistakes when no normal match succeeds.</small></span><input id="${prefix}-fuzzy" type="checkbox" ${values.fuzzy ? "checked" : ""}></label><label class="matching-setting fuzzy-sensitivity ${values.fuzzy ? "" : "is-disabled"}"><span class="matching-copy"><span class="matching-title">Fuzzy sensitivity</span><small>Controls how close a phrase must be before fuzzy matching is accepted. Conservative is least likely to match the wrong rule.</small></span><span class="matching-control"><input id="${prefix}-threshold" type="number" min="70" max="100" step="1" value="${fuzzyThresholdValue(values.fuzzy_threshold)}" ${values.fuzzy ? "" : "disabled"}></span></label></div>`;

const wordingEditor = (panel, groups) => `<details class="wording-editor eoc-details-base"><summary>Wording alternatives</summary><p class="help">Add different ways of saying the same thing. Separate multiple alternatives with commas.</p><div id="wording-groups">${groups.map((group) => `<div class="wording-group"><label>Main phrase<input class="wording-canonical" maxlength="100" value="${panel._e(group.canonical)}"></label><label>Other ways to say it<input class="wording-alternatives" value="${panel._e(group.alternatives.join(", "))}" placeholder="Comma-separated alternatives"></label><button type="button" class="icon wording-remove" aria-label="Remove wording alternative">×</button></div>`).join("")}</div><div class="section-actions"><button type="button" class="secondary" id="wording-add">Add wording alternative</button></div></details>`;
const ROUTING_HELP = '<section class="notice"><strong>AI routing command behavior</strong><p><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> routing rules are complete commands by default: they are acknowledged locally and apply to the rest of the current conversation. Enable <strong>Continue to AI</strong> to send the original request to the provider unchanged after applying the route. <strong>Starts with</strong>, <strong>Ends with</strong>, and <strong>Contains</strong> continue to the provider by default; matched words are not stripped.</p><p>A request-only reset bypasses a conversation override for that one provider request; it does not clear the saved conversation route. For strict matches, rules are evaluated from top to bottom and the first matching rule wins.</p></section>';

function requestRuleCard(panel, rule, index) {
  const result = panel._result || {}, rules = result.rules || [], summary = requestRuleSummary(rule, result.defaults || {});
  return `<article data-rule-key="${panel._e(rule.id)}" class="request-rule-card ${rule.enabled ? "" : "disabled"}"><div class="rule-card-heading"><div><span class="type-badge ${rule.action_type === "local_action" ? "local" : "routing"}">${rule.action_type === "local_action" ? "Local command" : "AI routing"}</span><h2>${panel._e(rule.name)}</h2></div><label class="switch-label"><span class="sr-only">Enable ${panel._e(rule.name)}</span><input class="rule-enabled" data-id="${panel._e(rule.id)}" type="checkbox" ${rule.enabled ? "checked" : ""}></label></div><div class="phrase-chips">${rule.phrases.slice(0,4).map((phrase) => `<span><b>${matchLabel(rule.match_type)}</b> ${panel._e(phrase)}</span>`).join("")}${summary.hiddenPhrases ? `<span class="eoc-more-phrases">+${summary.hiddenPhrases} more</span>` : ""}</div><p>${panel._e(summary.action)}</p><p class="meta">${panel._e(summary.matching)}</p>${rule.sensitive_matching_warning && rule.match_type !== "sentence_pattern" ? '<p class="sensitive-warning">Review tolerant matching carefully: this rule controls a potentially sensitive Home Assistant domain.</p>' : ""}<div class="actions">${result.diagnostics?.[rule.id] ? `<p class="sensitive-warning"><strong>Rule inactive:</strong> ${panel._e(result.diagnostics[rule.id])} Edit and save this rule to use the current sentence-pattern syntax.</p>` : ""}<button type="button" class="secondary rule-move" data-id="${panel._e(rule.id)}" data-direction="up" ${index === 0 ? "disabled" : ""}>Move up</button><button type="button" class="secondary rule-move" data-id="${panel._e(rule.id)}" data-direction="down" ${index === rules.length - 1 ? "disabled" : ""}>Move down</button><button type="button" class="secondary rule-edit" data-id="${panel._e(rule.id)}">Edit</button><button type="button" class="secondary rule-duplicate" data-id="${panel._e(rule.id)}">Duplicate</button><button type="button" class="danger secondary-danger rule-delete" data-id="${panel._e(rule.id)}">Delete</button></div></article>`;
}
const EMPTY_RULES_MARKUP = '<section class="content-card empty-state"><h2>Create your first Request Rule</h2><p>Add a fast local command such as “good night”.</p><button type="button" id="rule-empty-add">Create rule</button></section>';
const NO_RULES_MATCH_CONTENT = '<h2>No rules match your search</h2><p>Try a different phrase or rule name.</p>';
const SAFE_TESTER = '<section class="content-card" id="rule-match-tester"><h2>Preview rule match (safe)</h2><p>Checks which enabled Request Rule would win using the real matcher, without running the resulting action.</p><div class="notice on"><strong>Safe preview — nothing executes</strong><p>No Home Assistant action runs, conversation routing is not changed, and the AI provider is not called.</p></div><div class="search-row"><input id="rule-match-test-text" type="text" maxlength="2048" placeholder="Turn off the kitchen light" aria-label="Request text to test against Request Rules"><button type="button" id="rule-match-test">Preview match</button></div><p class="help">Preview and live Request Rule matching inspect at most 2048 characters (and 256 words).</p><div id="rule-match-test-result" aria-live="polite"></div></section>';
const LIVE_TESTER = '<details id="eoc-rule-live-test" class="content-card eoc-live-request-test eoc-details-base"><summary><span>Run full request (live)</span><span class="eoc-live-label">Live</span></summary><div class="eoc-live-request-body"><p>Runs text through the same full processing path as a real request to this assistant.</p><div class="notice"><strong>This can have real effects</strong><p>Unlike the safe preview above, this may execute Home Assistant actions, change conversation routing, or call the AI provider. A confirmation is shown before it runs.</p></div><div class="search-row"><input id="eoc-rule-live-text" type="text" placeholder="Turn off the kitchen light" aria-label="Live request text"><button type="button" id="eoc-rule-live-run">Run live request</button></div><pre id="eoc-rule-live-result" class="eoc-live-request-result" aria-live="polite"></pre></div></details>';

function renderRulesPage(panel, {query = panel._query || "", inPlaceSearch = false} = {}) {
  const result = panel._result || {}, rules = result.rules || [];
  const defaults = {...{word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90}, ...((panel._rulesSettingsDraft || result).defaults || {})};
  const search = inPlaceSearch ? "" : String(query).trim().toLowerCase();
  const filtered = rules.map((rule,index)=>({rule,index})).filter(({rule}) => !search || `${rule.name} ${rule.phrases.join(" ")} ${rule.action_type}`.toLowerCase().includes(search));
  return `<section class="page-intro"><div><h1>Request Rules</h1><p>Create fast voice shortcuts and route AI requests before they reach OpenAI.</p></div><button type="button" id="rule-add">Create rule</button></section><section class="notice on"><strong>Local commands skip the AI/API call</strong><p>They normally respond faster. AI routing rules keep using the AI but can change the model or reasoning for one request or the active conversation.</p></section>${ROUTING_HELP}<section class="content-card rule-settings"><details class="eoc-details-base"><summary>Default matching settings</summary><p class="help">These settings apply to rules unless a rule has its own custom matching settings. Normal matches are always preferred before fuzzy matching is tried.</p>${matchingControls("rules-default",defaults)}</details>${wordingEditor(panel,(panel._rulesSettingsDraft || result).wording_groups || [])}</section><div class="search-row"><input id="rule-search" type="search" value="${panel._e(query)}" placeholder="Search rules" aria-label="Search Request Rules"><span class="count">${rules.length} rule${rules.length === 1 ? "" : "s"}</span></div><section class="rule-list">${filtered.map(({rule,index})=>requestRuleCard(panel,rule,index)).join("") || (rules.length ? "" : EMPTY_RULES_MARKUP)}<section class="content-card empty-state" data-eoc-rule-search-empty ${inPlaceSearch || filtered.length || !rules.length ? "hidden" : ""}>${NO_RULES_MATCH_CONTENT}</section></section>${SAFE_TESTER}${LIVE_TESTER}`;
}
export function renderRequestRules(panel, presentation = {query:panel._query || "",inPlaceSearch:Boolean(panel._eocInPlaceRequestRuleSearch)}) { return renderRulesPage(panel,presentation); }

const ruleCollections = new WeakMap();
const pendingRuleButtons = new WeakSet();
const moveDisabled = (index,length,direction) => index < 0 || (direction === "up" ? index === 0 : index === length - 1);
const ruleSettingsSignature = panel => JSON.stringify([(panel._rulesSettingsDraft || panel._result)?.defaults,(panel._rulesSettingsDraft || panel._result)?.wording_groups]);
function prepareRequestRulesCollection(panel) {
  const list = panel.shadowRoot.querySelector(".rule-list");
  if (!list || ruleCollections.has(list)) return;
  ruleCollections.set(list,{cards:adoptKeyedElements(list,"[data-rule-key]","ruleKey"),settings:ruleSettingsSignature(panel),empty:list.querySelector(".empty-state")});
  reconcileRequestRules(panel);
}
export function reconcileRequestRules(panel) {
  const list=panel.shadowRoot.querySelector(".rule-list"), state=ruleCollections.get(list);
  if (!state || state.settings !== ruleSettingsSignature(panel)) return false;
  const result=panel._result || {}, rules=result.rules || [];
  const nodes=rules.map((rule,index)=>{
    const presentation={...rule}; delete presentation.order;
    const record=keyedElement(state.cards,rule.id,JSON.stringify([presentation,result.defaults,result.diagnostics?.[rule.id]]),()=>requestRuleCard(panel,rule,index));
    record.moves ||= [...record.node.querySelectorAll(".rule-move")];
    for(const button of record.moves) button.disabled = pendingRuleButtons.has(button) || moveDisabled(index,rules.length,button.dataset.direction);
    return record.node;
  });
  if(!rules.length){state.empty ||= elementFromMarkup(EMPTY_RULES_MARKUP);nodes.push(state.empty);}
  const searchEmpty=list.querySelector("[data-eoc-rule-search-empty]");if(searchEmpty)nodes.push(searchEmpty);
  placeChildren(list,nodes);pruneKeys(state.cards,new Set(rules.map(rule=>rule.id)));
  panel._eocRequestRuleCollectionRevision=(panel._eocRequestRuleCollectionRevision||0)+1;panel._eocRequestRuleSearchCache=null;return true;
}
export function requestRulesDialog(){ return ""; }

function syncScopeRevision(panel, result) {
  const scope=panel._unsavedState?.scopes?.get?.("capabilities/request-rules");
  if (scope && typeof result?.revision === "string") {
    scope.revision=result.revision;
    scope.result=panel._result;
  }
}
function finishMutation(panel,result,rules){
  panel._result={...(panel._result||{}),rules,revision:result.revision};
  const cacheKey=panel._sectionCacheKey?.(); if(cacheKey) panel._sectionCache?.delete(cacheKey);
  syncScopeRevision(panel,result);
  const reconciled = panel.shadowRoot ? reconcileRequestRules(panel) : false;
  if (reconciled) {
    const count = panel.shadowRoot?.querySelector?.(".search-row .count");
    if (count) count.textContent = `${rules.length} rule${rules.length === 1 ? "" : "s"}`;
    const query = String(panel._query || "").trim().toLocaleLowerCase();
    const byId = new Map(rules.map((rule) => [String(rule.id), rule]));
    let visible = 0;
    panel.shadowRoot?.querySelectorAll?.("[data-rule-key]").forEach((card) => {
      const rule = byId.get(String(card.dataset.ruleKey));
      const show = Boolean(rule) && (!query || `${rule.name || ""} ${(rule.phrases || []).join(" ")} ${rule.action_type || ""}`.toLocaleLowerCase().includes(query));
      card.hidden = !show;
      if (show) visible++;
    });
    const empty = panel.shadowRoot?.querySelector?.("[data-eoc-rule-search-empty]");
    if (empty) empty.hidden = !query || visible > 0 || rules.length === 0;
  }
  panel._render();
}
export function applyRequestRuleMutation(panel, action, result, context={}) {
  if (!result || result.revision == null) return false;
  let rules=[...(panel._result?.rules || [])];
  const presentedRule = result.rule
    ? {
        ...result.rule,
        ...(Object.prototype.hasOwnProperty.call(result, "sensitive_matching_warning")
          ? {sensitive_matching_warning: Boolean(result.sensitive_matching_warning)}
          : {}),
      }
    : null;
  const ruleId=context.ruleId || presentedRule?.id;
  if (action === "delete") {
    rules=rules.filter(rule=>rule.id!==ruleId);
  } else if (action === "move") {
    const index=rules.findIndex(rule=>rule.id===ruleId);
    const target=context.direction==="up" ? index-1 : index+1;
    if(index>=0 && target>=0 && target<rules.length) [rules[index],rules[target]]=[rules[target],rules[index]];
    if(presentedRule){const moved=rules.findIndex(rule=>rule.id===presentedRule.id);if(moved>=0)rules[moved]={...rules[moved],...presentedRule};}
  } else if (action === "create" || action === "duplicate") {
    if (!presentedRule) return false;
    const existing=rules.findIndex(rule=>rule.id===presentedRule.id);
    if(existing>=0) rules[existing]=presentedRule;
    else {
      const order=Number.isInteger(presentedRule.order) ? Math.max(0,Math.min(presentedRule.order,rules.length)) : rules.length;
      rules.splice(order,0,presentedRule);
    }
  } else if (action === "update") {
    if (!presentedRule) return false;
    const index=rules.findIndex(rule=>rule.id===presentedRule.id);
    if(index<0) return false;
    rules[index]=presentedRule;
  } else return false;
  rules=rules.map((rule,index)=>({...rule,order:index}));
  finishMutation(panel,result,rules);
  return true;
}

const setFuzzyState=(root,prefix)=>{const toggle=root.querySelector(`#${prefix}-fuzzy`),select=root.querySelector(`#${prefix}-threshold`);if(!toggle||!select)return;select.disabled=!toggle.checked;select.closest(".fuzzy-sensitivity")?.classList.toggle("is-disabled",!toggle.checked);};
function bindWordingEditor(panel){
  const root=panel.shadowRoot;
  const bindRemove=()=>root.querySelectorAll(".wording-remove").forEach(button=>{if(button.dataset.eocBound)return;button.dataset.eocBound="";button.addEventListener("click",()=>button.closest(".wording-group")?.remove());});
  bindRemove();
  const add=root.querySelector("#wording-add");
  if(add && !add.dataset.eocBound){add.dataset.eocBound="";add.addEventListener("click",()=>{const wrapper=document.createElement("div");wrapper.className="wording-group";wrapper.innerHTML='<label>Main phrase<input class="wording-canonical" maxlength="100"></label><label>Other ways to say it<input class="wording-alternatives" placeholder="Comma-separated alternatives"></label><button type="button" class="icon wording-remove" aria-label="Remove wording alternative">×</button>';root.querySelector("#wording-groups")?.append(wrapper);bindRemove();});}
}
export function bindRequestRulesCore(panel,{openEditor,activateSafeTester,activateLiveTester}={}) {
  const root=panel.shadowRoot,list=root.querySelector(".rule-list"); prepareRequestRulesCollection(panel);
  setFuzzyState(root,"rules-default"); root.querySelector("#rules-default-fuzzy")?.addEventListener("change",()=>setFuzzyState(root,"rules-default"),{once:false});
  bindWordingEditor(panel);
  if(list && !list.dataset.eocRuleCoreBound){
    list.dataset.eocRuleCoreBound="";
    list.addEventListener("click",async(event)=>{
      const button=event.target.closest?.("button");if(!button||button.disabled)return;
      if(button.id==="rule-empty-add"||button.matches(".rule-edit")){void openEditor?.(button.dataset.id||null);return;}
      const id=button.dataset.id;
      if(button.matches(".rule-move")){
        pendingRuleButtons.add(button);button.disabled=true;
        try{const result=await panel._call("request_rules","move",{rule_id:id,direction:button.dataset.direction,revision:panel._result?.revision});applyRequestRuleMutation(panel,"move",result,{ruleId:id,direction:button.dataset.direction});}
        catch(err){await recoverRequestRuleMutation(panel,err,"Unable to move Request Rule");}
        finally{pendingRuleButtons.delete(button);} return;
      }
      if(button.matches(".rule-duplicate")){
        button.disabled=true;
        try{const result=await panel._call("request_rules","duplicate",{rule_id:id,revision:panel._result?.revision});applyRequestRuleMutation(panel,"duplicate",result,{ruleId:id});panel._toast("Request Rule duplicated");}
        catch(err){await recoverRequestRuleMutation(panel,err,"Unable to duplicate Request Rule");} finally{button.disabled=false;} return;
      }
      if(button.matches(".rule-delete")){
        const deleting=(panel._result?.rules||[]).find((rule)=>rule.id===id);
        panel._eocDecisionConfirmSubject=deleting?.name?`Request Rule “${deleting.name}”`:"Request Rule";
        if(!await panel._confirm("Delete Request Rule?","This cannot be undone.","Delete"))return;
        button.disabled=true;
        try{const result=await panel._call("request_rules","delete",{rule_id:id,confirm:true,revision:panel._result?.revision});applyRequestRuleMutation(panel,"delete",result,{ruleId:id});panel._toast("Request Rule deleted");}
        catch(err){await recoverRequestRuleMutation(panel,err,"Unable to delete Request Rule");} finally{button.disabled=false;}
      }
    });
    list.addEventListener("change",async(event)=>{
      const input=event.target;if(!input.matches?.(".rule-enabled")||input.disabled)return;
      const rule=(panel._result?.rules||[]).find(item=>item.id===input.dataset.id);if(!rule)return;
      const previous=!input.checked;input.disabled=true;
      try{const result=await panel._call("request_rules","update",{rule_id:rule.id,revision:panel._result?.revision,rule:{...rule,enabled:input.checked,sensitive_matching_warning:undefined}});applyRequestRuleMutation(panel,"update",result,{ruleId:rule.id});panel._toast("Changes saved");}
      catch(err){input.checked=previous;await recoverRequestRuleMutation(panel,err,"Unable to update Request Rule");}
      finally{input.disabled=false;}
    });
  }
  const add=root.querySelector("#rule-add");if(add&&!add.dataset.eocRuleCoreBound){add.dataset.eocRuleCoreBound="";add.addEventListener("click",()=>{void openEditor?.(null);});}
  const safe=root.querySelector("#rule-match-tester");if(safe&&!safe.dataset.eocLazyBound){safe.dataset.eocLazyBound="";for(const type of ["focusin","pointerdown"])safe.addEventListener(type,()=>{void activateSafeTester?.();},{once:true,capture:true});}
  const live=root.querySelector("#eoc-rule-live-test");if(live&&!live.dataset.eocLazyBound){live.dataset.eocLazyBound="";live.addEventListener("toggle",()=>{if(live.open)void activateLiveTester?.();},{once:true});}
}
