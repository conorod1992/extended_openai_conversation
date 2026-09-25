import {applyRulePackImportMutation} from "./request-rules-ui-core.js";

const MAX_PACK_BYTES=2*1024*1024;

function downloadPack(pack) {
  const blob=new Blob([JSON.stringify(pack,null,2)],{type:"application/json"});
  const url=URL.createObjectURL(blob), link=document.createElement("a");
  link.href=url;link.download="request-rule-pack.json";link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function reviewMarkup(panel,review) {
  return `<div class="content-card rule-pack-review"><h3>Review Rule Pack</h3><p>${review.count} rules found · ${review.ready} ready · ${review.needs_attention} need attention</p><p class="help">All imported rules will be disabled and appended after existing rules. ${review.new_groups} new groups and ${review.new_wording_groups} wording groups will be added.</p><ul>${review.rules.map((rule)=>`<li><strong>${panel._e(rule.name)}</strong> · ${panel._e(rule.group)} · ${panel._e(rule.action_type === "local_action" ? "Local command" : "AI routing")}<br><span class="help">${panel._e(rule.triggers.join("; "))}</span><br><span class="help">${rule.conditions} conditions · Continue to AI: ${rule.continue_to_ai ? "On" : "Off"} · Continue matching: ${rule.continue_matching ? "On" : "Off"}${rule.ai_input_mode === "capture" ? ` · AI input: ${panel._e(rule.ai_input_capture)}` : ""}</span>${rule.function_tools.length ? `<br><span class="help">Function Tools: ${panel._e(rule.function_tools.join(", "))}</span>` : ""}${rule.entities.length ? `<br><span class="help">Entities: ${panel._e(rule.entities.join(", "))}</span>` : ""}${rule.services.length ? `<br><span class="help">Services: ${panel._e(rule.services.join(", "))}</span>` : ""}${rule.missing_dependencies.length ? `<br><span class="inline-error">Unavailable resources: ${panel._e(rule.missing_dependencies.join(", "))}</span>` : ""}</li>`).join("")}</ul><div class="section-actions"><button type="button" class="secondary" id="rule-pack-cancel">Cancel</button><button type="button" id="rule-pack-confirm">Import disabled rules</button></div></div>`;
}

export function bindRuleSharing(panel, host) {
  const content=host.querySelector("#rule-sharing-content");
  content.innerHTML=`<div class="rule-sharing-grid"><div><h3>Import rule pack</h3><p class="help">Review a rule pack before anything is added.</p><input id="rule-pack-file" type="file" accept=".json,application/json" aria-label="Choose rule pack"><button type="button" class="secondary" id="rule-pack-review-button">Import</button></div><div><h3>Export rule pack</h3><p class="help">Choose which rules to include.</p><label>Include<select id="rule-pack-selection"><option value="all">All rules</option><option value="group">A group</option><option value="selected">Selected rules</option></select></label><label id="rule-pack-group-label" hidden>Group<select id="rule-pack-group"></select></label><button type="button" class="secondary" id="rule-pack-export">Export</button></div></div><div id="rule-pack-message" role="status"></div><div id="rule-pack-review"></div><dialog id="rule-pack-select-dialog" class="editor-dialog" aria-label="Select rules to export"><div class="dialog-header"><h2>Selected rules</h2></div><div class="dialog-body" id="rule-pack-rule-list"></div><div class="dialog-actions"><button type="button" class="secondary" id="rule-pack-select-cancel">Cancel</button><button type="button" id="rule-pack-select-done">Export selected</button></div></dialog>`;
  const q=(selector)=>content.querySelector(selector), message=q("#rule-pack-message");
  const setMessage=(value)=>{message.textContent=value;};
  const groupSelect=q("#rule-pack-group");
  groupSelect.innerHTML=[{id:"",name:"Ungrouped"},...(panel._result?.groups||[])].map((group)=>`<option value="${panel._e(group.id)}">${panel._e(group.name)}</option>`).join("");
  q("#rule-pack-selection").addEventListener("change",()=>{q("#rule-pack-group-label").hidden=q("#rule-pack-selection").value!=="group";});
  const exportSelected=async(ruleIds=null)=>{
    const selection=q("#rule-pack-selection").value;
    try{
      const pack=await panel._call("request_rules","rule_pack_export",{selection,group_id:selection==="group"?(groupSelect.value||null):null,rule_ids:ruleIds});
      downloadPack(pack);setMessage(`${pack.rules.length} rules exported.`);
    }catch(err){setMessage(err.message||String(err));}
  };
  const selectDialog=q("#rule-pack-select-dialog");
  q("#rule-pack-export").addEventListener("click",()=>{
    if(q("#rule-pack-selection").value!=="selected"){void exportSelected();return;}
    q("#rule-pack-rule-list").innerHTML=(panel._result?.rules||[]).map((rule)=>`<label><input type="checkbox" value="${panel._e(rule.id)}"> ${panel._e(rule.name)}</label>`).join("");
    selectDialog.showModal();
  });
  q("#rule-pack-select-cancel").addEventListener("click",()=>selectDialog.close());
  q("#rule-pack-select-done").addEventListener("click",()=>{
    const ids=[...q("#rule-pack-rule-list").querySelectorAll("input:checked")].map((input)=>input.value);
    if(!ids.length){setMessage("Choose at least one rule.");return;}
    selectDialog.close();void exportSelected(ids);
  });
  let reviewed=null;
  q("#rule-pack-review-button").addEventListener("click",async()=>{
    const file=q("#rule-pack-file").files?.[0];
    if(!file){setMessage("Choose a rule pack first.");return;}
    if(file.size>MAX_PACK_BYTES){setMessage("Rule pack exceeds the 2 MB safety limit.");return;}
    try{
      const pack=await file.text();
      const review=await panel._call("request_rules","rule_pack_review",{pack});
      reviewed={pack,revision:review.revision,review_token:review.review_token};
      q("#rule-pack-review").innerHTML=reviewMarkup(panel,review);
      setMessage("");
    }catch(err){reviewed=null;q("#rule-pack-review").replaceChildren();setMessage(err.message||String(err));}
  });
  q("#rule-pack-file").addEventListener("change",()=>{reviewed=null;q("#rule-pack-review").replaceChildren();setMessage("");});
  q("#rule-pack-review").addEventListener("click",async(event)=>{
    if(event.target.closest("#rule-pack-cancel")){reviewed=null;q("#rule-pack-review").replaceChildren();return;}
    const button=event.target.closest("#rule-pack-confirm");
    if(!button||!reviewed)return;
    button.disabled=true;
    try{
      const result=await panel._call("request_rules","rule_pack_import",{pack:reviewed.pack,revision:reviewed.revision,review_token:reviewed.review_token,confirm:true});
      applyRulePackImportMutation(panel,result);
      reviewed=null;q("#rule-pack-review").replaceChildren();
      setMessage(`${result.rules.length} disabled rules imported after existing rules.`);
    }catch(err){setMessage(err.message||String(err));button.disabled=false;}
  });
}
