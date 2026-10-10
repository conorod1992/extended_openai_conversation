import{p as e,u as t}from"./management-actions-Sn7Q-_VG.js";import{t as n}from"./management-state-safety-BAzc83hV.js";var r=[[`usage_request_retention_days`,`Keep request details for`],[`usage_run_retention_days`,`Keep run details for`]];function i(e,t,n){let r=t?.value??t,i=t?.label??String(r);return`<option value="${e._e(String(r))}" ${String(r)===String(n)?`selected`:``}>${e._e(i)}</option>`}function a(e,t,n){let r=e._draft?.[t]??e._result?.config?.[t],a=e._result?.options?.[t]||[];return`<div class="setting" data-field="${t}" data-setting data-search="${e._e(`${n} usage history retention details ${t}`.toLowerCase())}">
    <label for="config-${t}">${e._e(n)}</label>
    <select id="config-${t}" data-config="${t}" data-retention-config="${t}" data-type="number">${a.map(t=>i(e,t,r)).join(``)}</select>
    <span class="field-error" data-error="${t}"></span>
  </div>`}function o(t){return t._configDirty?e({configuration:!0,pending:!!t._configurationSaving}):``}function s(e){return`<section class="page-intro"><h1>Usage data retention</h1><p>Choose how much detailed usage history to keep. Overall totals are maintained separately.</p></section><div class="content-card retention-surface">
    <section id="config-retention" data-config-section data-search="usage history retention request run details totals">
      <div class="card-heading"><div>
        <h2>Retention periods</h2>
        <p>Set separate periods for request and run details. Saving Disabled immediately deletes existing detailed history of that type. Overall usage totals are kept.</p>
      </div></div>
      <div class="form-grid">
        ${r.map(([t,n])=>a(e,t,n)).join(``)}
      </div>
    </section>
    ${o(e)}
    <span id="save-bar-anchor" class="sr-only"></span>
  </div>`}function c(t){let n=t.shadowRoot,r=n?.querySelector?.(`.save-bar`);if(!t._configDirty){r?.remove?.();return}r||(n?.querySelector?.(`#save-bar-anchor`)?.insertAdjacentHTML(`beforebegin`,e({configuration:!0,pending:!!t._configurationSaving})),u(t))}function l(e){let t=e.shadowRoot;t&&(t.querySelectorAll(`[data-retention-config]`).forEach(t=>{t.addEventListener(`change`,()=>{let r=t.dataset.retentionConfig;r&&e._draft&&(e._draft[r]=Number(t.value),n(e,[r],t,!1),c(e))})}),u(e))}function u(e){let n=e.shadowRoot.querySelector(`#revert-config`);n&&!n.dataset.eocDiscardBound&&(n.dataset.eocDiscardBound=`true`,n.addEventListener(`click`,()=>{!e._configurationSaving&&e._configData?.config&&(e._draft=t(e._configData.config),e._draftTitle=e._configData.title,e._setConfigDirty(!1),e._eocMainMarkup=null,e._render())}))}export{l as bindRetentionSettings,s as renderRetentionSettings};