from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise SystemExit(
            f"{path}: expected {count} matches, found {actual}\n--- OLD ---\n{old[:1200]}"
        )
    file_path.write_text(text.replace(old, new), encoding="utf-8")


path = "custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js"
replace(
    path,
    'const bool = (value) => value ? "checked" : "";\n',
    '''const bool = (value) => value ? "checked" : "";
export const skillNamesFromText = (value) => String(value || "").split(/\\r?\\n/).map((item) => item.trim()).filter(Boolean);
export function invalidateImportPreview(panel, applyButton, summary) {
  panel._importDocument = null;
  if (applyButton) applyButton.disabled = true;
  if (summary) summary.textContent = "Validate the document to preview it.";
}
''',
)
replace(
    path,
    '${field(panel,"skills","Skills",(config.skills || []).join(", "),"text","Enter comma-separated names of installed instruction sets the assistant may load when needed.")}',
    '''<label class="setting" data-field="skills" data-setting data-search="${panel._e(settingSearch("Skills", "Installed instruction sets the assistant may load when needed.", "skills"))}">${labelRow(panel,"Skills","skills")}<textarea id="config-skills" data-config="skills" class="short-textarea" spellcheck="false">${panel._e((config.skills || []).join("\\n"))}</textarea><small>Enter one installed skill name per line. Commas are preserved as part of a skill name.</small><span class="field-error" data-error="skills"></span></label>''',
)
replace(
    path,
    '    if (key === "skills" || key.startsWith("guest_readable_") || key.startsWith("guest_controllable_")) value = String(value).split(",").map(item => item.trim()).filter(Boolean);\n',
    '''    if (key === "skills") value = skillNamesFromText(value);
    else if (key.startsWith("guest_readable_") || key.startsWith("guest_controllable_")) value = String(value).split(",").map(item => item.trim()).filter(Boolean);
''',
)
replace(
    path,
    'const saved=await panel._call("configuration","update",{config,title:panel._draftTitle});',
    'const saved=await panel._call("configuration","update",{config,title:panel._draftTitle,revision:panel._configData?.revision});',
)
replace(
    path,
    '''  root.querySelector("#import-agent")?.addEventListener("click", () => root.querySelector("#import-dialog")?.showModal());
  root.querySelector("#import-preview")?.addEventListener("click", async () => { try { const result=await panel._call("configuration","import_preview",{document:root.querySelector("#import-document").value}); panel._importDocument=root.querySelector("#import-document").value; root.querySelector("#import-summary").textContent=`${result.title} / ${result.summary.model} / ${result.summary.tools} tools / ${result.summary.function_groups} function groups / ${result.summary.speech_rules} speech rules`; root.querySelector("#import-apply").disabled=false; } catch(err){root.querySelector("#import-summary").textContent=err.message||String(err);root.querySelector("#import-apply").disabled=true;} });
  root.querySelector("#import-apply")?.addEventListener("click", async () => { const mode=root.querySelector('input[name="import-mode"]:checked').value; if(mode==="current"&&!await panel._confirm("Overwrite this agent?",`The saved configuration will be replaced.${panel._configDirty ? " Your unsaved shared draft will be discarded." : ""} Retained history and parent-entry credentials are not affected.`,"Overwrite"))return; try { await panel._call("configuration","import",{document:panel._importDocument,mode,confirm:mode==="current"}); root.querySelector("#import-dialog").close(); if(mode==="current")panel._clearConfigDraft(); await panel._loadAgents(panel._agentId); panel._toast(mode==="current"?"Configuration imported":"Agent created from import; your current draft is preserved"); } catch(err){panel._toast(`Unable to import: ${err.message||String(err)}`,true);} });
''',
    '''  const importDocument = root.querySelector("#import-document"), importApply = root.querySelector("#import-apply"), importSummary = root.querySelector("#import-summary");
  root.querySelector("#import-agent")?.addEventListener("click", () => { invalidateImportPreview(panel, importApply, importSummary); root.querySelector("#import-dialog")?.showModal(); });
  importDocument?.addEventListener("input", () => invalidateImportPreview(panel, importApply, importSummary));
  root.querySelector("#import-preview")?.addEventListener("click", async () => { try { const result=await panel._call("configuration","import_preview",{document:importDocument.value}); panel._importDocument=importDocument.value; importSummary.textContent=`${result.title} / ${result.summary.model} / ${result.summary.tools} tools / ${result.summary.function_groups} function groups / ${result.summary.speech_rules} speech rules`; importApply.disabled=false; } catch(err){panel._importDocument=null;importSummary.textContent=err.message||String(err);importApply.disabled=true;} });
  root.querySelector("#import-apply")?.addEventListener("click", async () => { const mode=root.querySelector('input[name="import-mode"]:checked').value, source=importDocument.value; if(!panel._importDocument||panel._importDocument!==source){invalidateImportPreview(panel,importApply,importSummary);return;} if(mode==="current"&&!await panel._confirm("Overwrite this agent?",`The saved configuration will be replaced.${panel._configDirty ? " Your unsaved shared draft will be discarded." : ""} Retained history and parent-entry credentials are not affected.`,"Overwrite"))return; try { await panel._call("configuration","import",{document:panel._importDocument,mode,confirm:mode==="current",...(mode==="current"?{revision:panel._configData?.revision}:{})}); root.querySelector("#import-dialog").close(); if(mode==="current")panel._clearConfigDraft(); await panel._loadAgents(panel._agentId); panel._toast(mode==="current"?"Configuration imported":"Agent created from import; your current draft is preserved"); } catch(err){panel._toast(`Unable to import: ${err.message||String(err)}`,true);} });
''',
)

path = "custom_components/extended_openai_conversation_responses/frontend/management-panel.js"
replace(
    path,
    '''  _invalidateAfterMutation(agentId, section, action) {
    const mutations = {
''',
    '''  _invalidateAfterMutation(agentId, section, action) {
    if (agentId && section === "backup" && action === "restore") {
      const prefix = `${agentId}|`;
      for (const key of this._sectionCache.keys()) if (key.startsWith(prefix)) this._sectionCache.delete(key);
      for (const key of this._scopeCatalogCache.keys()) if (key.startsWith(prefix)) this._scopeCatalogCache.delete(key);
      if (this._scopeCatalogVisitKey?.startsWith(prefix)) this._scopeCatalogVisitKey = null;
      return;
    }
    const mutations = {
''',
)

path = "custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js"
replace(
    path,
    '''  const result = panel._result || {};
  const rules = result.rules || [];
  const actionSelectorHost = q("#rule-action-sequence-host");
''',
    '''  const result = panel._result || {};
  const rules = result.rules || [];
  const revision = result.revision;
  const actionSelectorHost = q("#rule-action-sequence-host");
''',
)
replace(
    path,
    'try { await panel._call("request_rules", "duplicate", {rule_id:button.dataset.id}); await panel._loadSection(); }',
    'try { await panel._call("request_rules", "duplicate", {rule_id:button.dataset.id,revision}); await panel._loadSection(); }',
)
replace(
    path,
    'try { await panel._call("request_rules", "delete", {rule_id:button.dataset.id, confirm:true}); await panel._loadSection(); }',
    'try { await panel._call("request_rules", "delete", {rule_id:button.dataset.id,confirm:true,revision}); await panel._loadSection(); }',
)
replace(
    path,
    'try { await panel._call("request_rules", "update", {rule_id:rule.id, rule:{...rule, enabled:input.checked, sensitive_matching_warning:undefined}}); await panel._loadSection(true); }',
    'try { await panel._call("request_rules", "update", {rule_id:rule.id,rule:{...rule,enabled:input.checked,sensitive_matching_warning:undefined},revision}); await panel._loadSection(true); }',
)
replace(
    path,
    'try { await panel._call("request_rules", "defaults", {defaults:{word_forms:q("#rules-default-word-forms").checked, wording_alternatives:q("#rules-default-wording").checked, fuzzy:q("#rules-default-fuzzy").checked, fuzzy_threshold:fuzzyThresholdValue(q("#rules-default-threshold").value)}}); await panel._loadSection(); }',
    'try { await panel._call("request_rules", "defaults", {defaults:{word_forms:q("#rules-default-word-forms").checked,wording_alternatives:q("#rules-default-wording").checked,fuzzy:q("#rules-default-fuzzy").checked,fuzzy_threshold:fuzzyThresholdValue(q("#rules-default-threshold").value)},revision}); await panel._loadSection(); }',
)
replace(
    path,
    'try { await panel._call("request_rules", "wording_groups", {wording_groups}); await panel._loadSection(); }',
    'try { await panel._call("request_rules", "wording_groups", {wording_groups,revision}); await panel._loadSection(); }',
)
replace(
    path,
    '''      await panel._call("request_rules", panel._editingRuleId ? "update" : "create", {...(panel._editingRuleId ? {rule_id:panel._editingRuleId} : {}), rule});
''',
    '''      await panel._call("request_rules", panel._editingRuleId ? "update" : "create", {...(panel._editingRuleId ? {rule_id:panel._editingRuleId} : {}),rule,revision});
''',
)

Path(".github/pr19_frontend_patch.py").unlink()
Path(".github/workflows/pr19-frontend-apply.yml").unlink()
