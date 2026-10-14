import{s as e,u as t}from"./configuration-acknowledgement-sqF0CFBc.js";import{t as n}from"./management-state-safety-BSCYtFVQ.js";var r=[[`usage_request_retention_days`,`Keep request details for`],[`usage_run_retention_days`,`Keep run details for`]];function i(e,t,n){let r=t?.value??t,i=t?.label??String(r);return`<option value="${e._e(String(r))}" ${String(r)===String(n)?`selected`:``}>${e._e(i)}</option>`}function a(e,t,n){let r=e._draft?.[t]??e._result?.config?.[t],a=e._result?.options?.[t]||[];return`<div class="setting" data-field="${t}" data-setting data-search="${e._e(`${n} usage history retention details ${t}`.toLowerCase())}">
    <label for="config-${t}">${e._e(n)}</label>
    <select id="config-${t}" data-config="${t}" data-retention-config="${t}" data-type="number">${a.map(t=>i(e,t,r)).join(``)}</select>
    <span class="field-error" data-error="${t}"></span>
  </div>`}function o(e){return e._configDirty?t({configuration:!0,pending:!!e._configurationSaving}):``}function s(e){return`<section class="page-intro"><h1>Usage data retention</h1><p>Choose how much detailed usage history to keep. Overall totals are maintained separately.</p></section><div class="content-card retention-surface">
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
  </div>`}function c(e){let n=e.shadowRoot,r=n?.querySelector?.(`.save-bar`);if(!e._configDirty){r?.remove?.();return}r||(n?.querySelector?.(`#save-bar-anchor`)?.insertAdjacentHTML(`beforebegin`,t({configuration:!0,pending:!!e._configurationSaving})),u(e))}function l(e){let t=e.shadowRoot;t&&(t.querySelectorAll(`[data-retention-config]`).forEach(t=>{t.addEventListener(`change`,()=>{let r=t.dataset.retentionConfig;r&&e._draft&&(e._draft[r]=Number(t.value),n(e,[r],t,!1),c(e))})}),u(e))}function u(t){let n=t.shadowRoot.querySelector(`#revert-config`);n&&!n.dataset.eocDiscardBound&&(n.dataset.eocDiscardBound=`true`,n.addEventListener(`click`,()=>{!t._configurationSaving&&t._configData?.config&&(t._draft=e(t._configData.config),t._draftTitle=t._configData.title,t._setConfigDirty(!1),t._eocMainMarkup=null,t._render())}))}export{l as bindRetentionSettings,s as renderRetentionSettings};