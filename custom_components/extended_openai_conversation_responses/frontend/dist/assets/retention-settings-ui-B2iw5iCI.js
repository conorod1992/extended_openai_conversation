import{I as e,yt as t}from"./management-core-B74i9Wcp.js";import{d as n}from"./management-page-drafts-YvVFf7Nm.js";var r=[[`usage_request_retention_days`,`Keep request details for`],[`usage_run_retention_days`,`Keep run details for`]];function i(e){let t=r.flatMap(([t],n)=>{let r=e?.[t],i=n===0?`request details`:`run details`;return!Number.isInteger(r)||r<0?[]:[r===0?`all ${i}`:`${i} older than ${r} days`]});return t.length?`Shorter Usage retention will delete ${t.join(` and `)}. Usage totals will be kept.`:``}function a(e,t,n){let r=e._draft?.[t]??e._result?.config?.[t],i=e._result?.options?.[t]||[],a=!i.some(e=>(e?.value??e)===r),o=i.map(t=>{let n=t?.value??t;return`<option value="${e._e(String(n))}" ${n===r?`selected`:``}>${e._e(t?.label??String(n))}</option>`});return`<div class="setting" data-field="${t}" data-setting data-search="${e._e(`${n} usage history retention details ${t}`.toLowerCase())}">
    <label for="config-${t}">${e._e(n)}</label>
    <select id="config-${t}" data-config="${t}" data-retention-config="${t}" data-type="number">${a?`<option value="" disabled selected>Suspended · History kept</option>`:``}${o.join(``)}</select>
    <small>${t===`usage_request_retention_days`?`Individual provider calls.`:`Completed conversation turns.`}</small>
    <span class="field-error" data-error="${t}"></span>
  </div>`}function o(e){return e._configDirty?t({configuration:!0,pending:!!e._configurationSaving}):``}function s(e){return`<section class="page-intro"><h1>Usage data retention</h1></section><div class="content-card retention-surface">
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
  </div>`}function c(e){let n=e.shadowRoot,r=n?.querySelector?.(`.save-bar`);if(!e._configDirty){r?.remove?.();return}r||(n?.querySelector?.(`#save-bar-anchor`)?.insertAdjacentHTML(`beforebegin`,t({configuration:!0,pending:!!e._configurationSaving})),u(e))}function l(e){let t=e.shadowRoot;t&&(t.querySelectorAll(`[data-retention-config]`).forEach(t=>{t.addEventListener(`change`,()=>{let r=t.dataset.retentionConfig;r&&e._draft&&t.value!==``&&(e._draft[r]=Number(t.value),n(e,[r],t,!1),c(e))})}),u(e))}function u(t){let n=t.shadowRoot.querySelector(`#revert-config`);n&&!n.dataset.eocDiscardBound&&(n.dataset.eocDiscardBound=`true`,n.addEventListener(`click`,()=>{!t._configurationSaving&&t._configData?.config&&(e(t),t._eocMainMarkup=null,t._render())}))}export{s as n,i as r,l as t};