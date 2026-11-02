import {UnsavedState, same, clone} from "./unsaved-state.js";
import {captureConfigurationDraftOwner, captureConfigurationSubmission, configurationSubmissionIsCurrent, completeConfigurationAcknowledgement} from "./configuration-acknowledgement.js";
import {bindOwnedEvent} from "./management-owned-events.js";

const identity = panel => captureConfigurationDraftOwner(panel);
const owns = (panel, draft) => same(identity(panel), draft.owner);
const values = draft => ({groups:draft.text, guests:draft.guests});
const dirty = draft => !same(values(draft), draft.baseline);
const escape = (panel,value) => panel._e(String(value ?? ""));

export function requiresGroupCollectionRepair(panel) {
  const repair = panel._result?.function_repair;
  if (panel._groupCollectionDraft && owns(panel,panel._groupCollectionDraft) && dirty(panel._groupCollectionDraft)) return true;
  if (!repair) return false;
  if (repair.group_collection_repair) return true;
  const groups = repair.persisted_groups;
  return groups != null && (!Array.isArray(groups) || groups.some(group => !group || typeof group.id !== "string"
    || !group.id || groups.filter(item => item?.id === group.id).length !== 1));
}

function ownDraft(panel, recovered) {
  const repair = panel._result?.function_repair || {};
  let draft = panel._groupCollectionDraft;
  if (recovered || !draft || !owns(panel,draft) || (!dirty(draft) && draft.revision !== panel._result?.revision)) {
    const text = typeof repair.groups_yaml === "string" ? repair.groups_yaml : JSON.stringify(repair.persisted_groups ?? [],null,2);
    const guests = JSON.stringify(repair.guest_allowed_group_ids ?? panel._result?.config?.guest_allowed_group_ids ?? [],null,2);
    draft = panel._groupCollectionDraft = recovered || {owner:identity(panel),text,guests,baseline:{groups:text,guests},revision:panel._result?.revision};
  }
  const scopes = panel._unsavedState ||= new UnsavedState();
  scopes.register("function-group-collection-repair", {
    dirty:() => owns(panel,draft) && dirty(draft), read:() => values(draft),
    owns:destination => Boolean(destination) && owns(panel,draft), destinations:() => ["capabilities/functions"],
    get pending() { return Boolean(draft.pending); },
    discard:() => {
      if (!owns(panel,draft) || draft.pending) return;
      draft.text=draft.baseline.groups;draft.guests=draft.baseline.guests;draft.error="";
      for(const [selector,value] of [["#group-collection-editor",draft.text],["#group-collection-guests",draft.guests]]) {
        const control=panel.shadowRoot?.querySelector(selector);if(control) control.value=value;
      }
      const error=panel.shadowRoot?.querySelector("#group-collection-error");if(error) error.textContent="";
    },
    restore:target => {
      if (!owns(panel,draft) || panel.isConnected !== false || target.isConnected === false || !target._data?.is_admin) return;
      const targetOwner=identity(target);
      if (["agentId","entryId","userId"].some(field => targetOwner[field] !== draft.owner[field])) return;
      if (target._groupCollectionDraft && owns(target,target._groupCollectionDraft) && dirty(target._groupCollectionDraft)) return;
      ownDraft(target,{...clone(draft),owner:targetOwner});
      draft.text=draft.baseline.groups;draft.guests=draft.baseline.guests;
      target._render();
    },
  });
  return draft;
}

export function renderGroupCollectionRepair(panel) {
  if (!requiresGroupCollectionRepair(panel)) return "";
  const draft = ownDraft(panel);
  return `<section class="card function-group-collection-repair" data-group-search="needs repair groups collection" aria-labelledby="group-collection-repair-title">
    <h2 id="group-collection-repair-title">Function Group collection needs repair</h2>
    <p>The saved collection contains Group identifiers that cannot be edited individually. Review the complete collection and its Guest references. Existing Function Tools are preserved.</p>
    <label class="field"><span>Complete Function Groups (YAML or JSON)</span><textarea id="group-collection-editor" rows="14" spellcheck="false">${escape(panel,draft.text)}</textarea></label>
    <label class="field"><span>Guest allowed Group IDs (JSON array)</span><textarea id="group-collection-guests" rows="3" spellcheck="false">${escape(panel,draft.guests)}</textarea><small>Explicitly review these references when repairing duplicate, renamed or removed Group IDs. Use [] to allow none.</small></label>
    <div id="group-collection-error" class="validation invalid" role="alert">${escape(panel,draft.error || "")}</div>
    <div class="actions"><button type="button" id="group-collection-save" ${draft.pending ? "disabled" : ""}>Validate and save Group collection</button><button type="button" id="group-collection-discard" class="secondary" ${draft.pending ? "disabled" : ""}>Discard unsaved Group repair</button></div>
  </section>`;
}

export function bindGroupCollectionRepair(panel) {
  const root=panel.shadowRoot, button=root?.querySelector("#group-collection-save");
  if (!button) return;
  const draft=ownDraft(panel), editor=root.querySelector("#group-collection-editor"), guests=root.querySelector("#group-collection-guests");
  const current=() => owns(panel,draft) && panel._groupCollectionDraft === draft && button.isConnected !== false;
  bindOwnedEvent(editor,"input",panel,() => { if (current()) draft.text=editor.value; });
  bindOwnedEvent(guests,"input",panel,() => { if (current()) draft.guests=guests.value; });
  bindOwnedEvent(root.querySelector("#group-collection-discard"),"click",panel,() => {
    if (!current() || draft.pending) return;
    panel._unsavedState.scopes.get("function-group-collection-repair").discard();draft.error="";panel._render();
  });
  bindOwnedEvent(button,"click",panel,async () => {
    if (!current() || draft.pending) return;
    const submission=captureConfigurationSubmission(panel), submitted={groups:editor.value,guests:guests.value};
    draft.text=submitted.groups;draft.guests=submitted.guests;draft.error="";
    let selected;
    try { selected=JSON.parse(submitted.guests); if (!Array.isArray(selected) || selected.some(id => typeof id !== "string")) throw Error(); }
    catch { draft.error="Guest Group references must be a JSON array of Group IDs.";root.querySelector("#group-collection-error").textContent=draft.error;return; }
    draft.pending=true;button.disabled=true;root.querySelector("#group-collection-discard").disabled=true;
    try {
      const saved=await panel._call("function_repair","save_groups",{groups:submitted.groups,guest_allowed_group_ids:selected,revision:draft.revision});
      if (!configurationSubmissionIsCurrent(panel,submission)) return;
      if (saved?.valid === false) throw Error(Object.values(saved.errors || {})[0] || "Function Groups are still invalid.");
      draft.baseline=submitted;draft.revision=saved.revision;
      await completeConfigurationAcknowledgement(panel,saved,submission,{
        fields:["function_groups","guest_allowed_group_ids"],metadata:["function_repair"],message:"Function Groups repaired",
        refresh:async () => { if(panel._viewKey?.() === "capabilities/functions") {panel._result=panel._configData;panel._render();} },
      });
    } catch(error) {
      if (!configurationSubmissionIsCurrent(panel,submission)) return;
      draft.error=error?.mutationOutcome === "uncertain" ? "The save outcome is unknown. Refresh the saved configuration before trying another Group change." : error.message || String(error);
      const errorNode=root.querySelector("#group-collection-error");
      if(errorNode) errorNode.textContent=draft.error;
      panel._toast?.(`Unable to repair Function Groups: ${draft.error}`,true);
    } finally {
      draft.pending=false;
      if (owns(panel,draft) && panel._groupCollectionDraft === draft) {
        for(const id of ["group-collection-save","group-collection-discard"]) {
          const control=root.querySelector(`#${id}`);if(control) control.disabled=false;
        }
      }
    }
  });
}
