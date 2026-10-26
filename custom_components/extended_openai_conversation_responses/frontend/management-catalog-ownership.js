import {captureConfigurationOwner, configurationSubmissionIsCurrent} from "./configuration-acknowledgement.js";
import {clone, same} from "./unsaved-state.js";

function authoring(panel) {
  const scopes=[...(panel._unsavedState?.scopes?.entries?.() || [])];
  const states=scopes.map(([id,scope]) => ({id,dirty:Boolean(scope.dirty?.()),pending:Boolean(scope.pending),
    value:scope.read ? clone(scope.read()) : undefined})).filter(scope => scope.dirty || scope.pending);
  const dialogs=[...(panel.shadowRoot?.querySelectorAll?.("dialog[open]") || [])].map(dialog => ({dialog,
    values:dialog.__eocReadDraft ? clone(dialog.__eocReadDraft()) : [...(dialog.querySelectorAll?.("input,select,textarea,ha-selector") || [])].map(control => ({
      id:control.id,value:clone(control.value),checked:control.checked,
      yaml:control.__eocYamlDraft ? clone(control.__eocYamlDraft()) : undefined,
    })),
  }));
  const pending=Boolean(panel._configurationSaving || panel._eocAgentMutations || panel._pendingMutations?.size
    || states.some(scope => scope.pending));
  return {draft:clone(panel._draft),title:panel._draftTitle,draftAgent:panel._draftAgentId,
    states,dialogs,pending,dirty:Boolean(panel._configDirty || states.some(scope => scope.dirty)),
    model:panel._eocModelIntent || null,preparation:panel._eocPendingModelPreparation || null,
    reasoning:panel._eocReasoningChoiceRevision || 0};
}

function unchanged(left,right) {
  return left.model === right.model && left.preparation === right.preparation && left.reasoning === right.reasoning
    && left.draftAgent === right.draftAgent && same(left.draft,right.draft) && left.title === right.title
    && left.pending === right.pending && left.dirty === right.dirty && same(left.states,right.states)
    && left.dialogs.length === right.dialogs.length && left.dialogs.every((dialog,index) =>
      dialog.dialog === right.dialogs[index].dialog && same(dialog.values,right.dialogs[index].values));
}

const hasWork = state => state.dirty || state.pending || state.dialogs.length > 0 || Boolean(state.preparation);

export function captureCatalogSelectionIntent(panel) {
  return {owner:captureConfigurationOwner(panel),view:panel._viewKey?.(),visit:panel._loadToken,state:authoring(panel)};
}

export function captureCatalogLoad(panel,owner = null,selectionIntent = null) {
  const intent=selectionIntent || captureCatalogSelectionIntent(panel);
  return {owner,intent,user:panel.hass?.user?.id,sequence:panel._eocCatalogReadSequence=(panel._eocCatalogReadSequence || 0)+1,
    startup:!panel._agentId && !panel._draftAgentId,continuation:null};
}

export function catalogMayPublish(panel,load) {
  return panel.isConnected !== false && panel._eocCatalogReadSequence === load.sequence
    && panel.hass?.user?.id === load.user && load.intent.owner.userId === load.user
    && (!load.owner || configurationSubmissionIsCurrent(panel,load.continuation?.owner || load.owner));
}

export function catalogIntentIsCurrent(panel,intent) {
  return configurationSubmissionIsCurrent(panel,intent.owner) && panel._viewKey?.() === intent.view
    && panel._loadToken === intent.visit && unchanged(intent.state,authoring(panel));
}

export function catalogMaySelect(panel,load) {
  if(!catalogMayPublish(panel,load)) return false;
  const current=authoring(panel);
  if(hasWork(load.intent.state) || hasWork(current)) return false;
  // Before the first catalog there is no editable assistant to supersede.
  if(load.startup && !panel._agentId && !panel._draftAgentId) return true;
  return catalogIntentIsCurrent(panel,load.intent);
}

export function catalogHasAuthoredWork(panel) { return hasWork(authoring(panel)); }

export function captureCatalogContinuation(panel,load) {
  load.continuation=captureCatalogSelectionIntent(panel);
}

export function catalogContinuationIsCurrent(panel,load) {
  return catalogMayPublish(panel,load) && Boolean(load.continuation) && catalogIntentIsCurrent(panel,load.continuation);
}

export function catalogFailureIsCurrent(panel,load) {
  return catalogMayPublish(panel,load) && (load.startup && !panel._agentId
    || catalogIntentIsCurrent(panel,load.continuation || load.intent));
}

// Both selection routes retire the same authoring session and keep the panel's
// lifetime editor guard. Only callers which already passed admission may use it.
export function retireAssistantDraft(panel,nextAgent) {
  if(panel._agentId === nextAgent) return false;
  panel._unsavedState?.scopes.forEach((_scope,key,scopes) => {if(key !== "editors") scopes.delete(key);});
  panel._agentId=nextAgent;
  panel._ruleGroupFilter="all";
  panel._clearConfigDraft?.();
  panel._scopeId=null;
  return true;
}
