import{a as e,o as t,r as n,s as r}from"./tool-yaml-editor-adapter-Deng4k2X.js";var i=`ha-yaml-editor`,a=`tool-yaml-native`;function o(e){return!e||typeof e!=`object`?!1:Object.keys(e).length===1&&typeof e.__eoai_management_yaml_v1__==`string`||Object.values(e).some(o)}var s=null,c=`spec:
  name: my_tool
  description: Describe what this tool does.
  parameters:
    type: object
    properties: {}
function:
  type: native
  name: ''
`,l=Object.freeze({spec:Object.freeze({name:`my_tool`,description:`Describe what this tool does.`,parameters:Object.freeze({type:`object`,properties:Object.freeze({})})}),function:Object.freeze({type:`native`,name:``})}),u=`
  #tool-dialog.tool-dialog {
    width: min(1100px, calc(100vw - 32px));
    max-height: calc(100dvh - 32px);
    overflow: hidden;
  }
  #tool-dialog.tool-dialog[open] {
    display: flex;
    flex-direction: column;
  }
  #tool-dialog .tool-dialog-body {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }
  #tool-dialog .tool-editor-label {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    height: auto;
    min-height: 0;
    overflow: hidden;
  }
  #${a} {
    display: block;
    flex: 1 1 auto;
    width: 100%;
    min-height: 0;
    height: 100%;
    cursor: text;
  }
  #tool-dialog .dialog-actions {
    flex: 0 0 auto;
  }
  #${a}[hidden] { display: none; }
  #tool-yaml[hidden] { display: none !important; }
`;async function d(e=globalThis.customElements,t=globalThis.document){return e?.get?.(i)?!0:e?.whenDefined?(s||=(async()=>{if(t?.createElement)try{await(t.createElement(`partial-panel-resolver`)?.getRoutes?.([{component_name:`developer-tools`,url_path:`a`}]))?.routes?.a?.load?.(),e.get?.(i)||await t.createElement(`developer-tools-router`)?.routerOptions?.routes?.service?.load?.()}catch{}return e.get?.(i)||await e.whenDefined(i),!0})().finally(()=>{s=null}),s):!1}function f(e,t=null){return t!==null||String(e||``).replace(/\r\n/g,`
`)!==c?null:{spec:{name:l.spec.name,description:l.spec.description,parameters:{type:`object`,properties:{}}},function:{type:`native`,name:``}}}function p(e){let t=e?._repairToolIndex;if(!Number.isInteger(t))return null;let n=e?._result?.function_repair?.invalid_tools;if(!Array.isArray(n))return null;let r=n.find(e=>Number(e?.index)===t)?.tool;return!r||typeof r!=`object`||Array.isArray(r)?null:r}function m(e){let t=e.shadowRoot?.querySelector(`ha-code-editor`),n=t?.shadowRoot?.querySelector(`.cm-editor`);t&&n&&(t.style.height=`100%`,t.style.minHeight=`0`,n.style.height=`100%`,n.style.minHeight=`0`)}function h(e){if(!e||e.querySelector(`style[data-native-tool-yaml]`))return;let t=document.createElement(`style`);t.dataset.nativeToolYaml=``,t.textContent=u,e.append(t)}function g(s){let c=s?.shadowRoot,l=c?.querySelector(`#tool-yaml`),u=c?.querySelector(`#${a}`);if(!l||!u)return;h(c);let g=e(s);if(g?.nativeEditor===u){g.retryNative?.();return}let _=g?.getYaml?.()??String(l.value??``),v=!1,y=!1,b=0,x=n(s),S=!1,C=new Set,w=()=>{if(y)return;let e=u.shadowRoot?.querySelector(`ha-code-editor`);for(let t of[u,u.shadowRoot,e?.shadowRoot])t&&!C.has(t)&&(T?.observe(t,{subtree:!0,childList:!0,attributes:!0,attributeFilter:[`aria-label`,`tabindex`]}),C.add(t));let t=e?.shadowRoot?.querySelector(`.cm-content`);if(!t)return;if(!e.shadowRoot.querySelector(`style[data-eoc-yaml-accessibility]`)){let t=document.createElement(`style`);t.dataset.eocYamlAccessibility=``,t.textContent=`.cm-editor .cm-tooltip{background:var(--card-background-color);color:var(--primary-text-color);border-color:var(--divider-color)}.cm-editor .cm-panel.cm-panel-lint ul li[aria-selected]{background:var(--secondary-background-color);color:var(--primary-text-color)}`,e.shadowRoot.append(t)}let n=u.getAttribute?.(`aria-label`)||`Function Tool YAML`;t.getAttribute(`aria-label`)!==n&&t.setAttribute(`aria-label`,n),t.tabIndex!==0&&(t.tabIndex=0),m(u)},T=globalThis.MutationObserver?new MutationObserver(w):null;w();let E=()=>!y&&s?._toolYamlEditorAdapter===F&&u.isConnected,D=()=>{v=!1,u.hidden=!0,l.hidden=!1},O=async(e,t)=>{if(o(e))return D(),!1;try{u.setValue(e);let n=u.yaml;await u.updateComplete;let r=u.shadowRoot?.querySelector(`ha-code-editor`);if(await r?.updateComplete,!t())return!1;if(typeof n!=`string`||r?.codemirror?.state.doc.toString()!==n||u.yaml!==n)return D(),!1;let i=c.activeElement===l;return v=!0,l.hidden=!0,u.hidden=!1,m(u),i&&u.focus?.(),!0}catch{return t()&&D(),!1}},k,A=async(e,t=k)=>{D();let n=++b;if(typeof u.setValue!=`function`)return;let r=x,i=()=>E()&&!S&&n===b&&r.isCurrent();if(!i())return;try{u.inDialog=!0}catch{return}let a=async()=>{if(!String(e||``).trim()){i()&&await O({},i)&&(u.isValid=!0);return}let n=String(e??``)===String(s._toolInitialYaml??``)?p(s):null;if(n){i()&&await O(n,i);return}let r=f(e,s._toolOriginalName??null);if(t||r){i()&&await O(t||r,i);return}try{let t=await s._call(`tools`,`validate_yaml`,{yaml:e});if(!i())return;if(t?.valid){await O(t.config,i);return}D()}catch{i()&&D()}},o;try{await Promise.race([a(),new Promise(e=>{o=setTimeout(()=>{i()&&(++b,D()),e()},5e3)})])}finally{clearTimeout(o)}},j=()=>{M(),_=String(l.value??``),k=void 0,r(s,_)},M=()=>{S=!0,++b},N=e=>{v&&(++b,_=String(u.yaml??``),k=void 0,l.value=_,r(s,_,e.detail||{}))},P=()=>{let e=c.querySelector(`#tool-dialog`),t=c.querySelector(`#tool-save`);e?.open&&t&&!t.disabled&&t.click()};l.addEventListener?.(`input`,j),l.addEventListener?.(`pointerdown`,M),l.addEventListener?.(`keydown`,M),u.addEventListener(`value-changed`,N),u.addEventListener(`editor-save`,P);let F={textarea:l,nativeEditor:u,retryNative(){v||I()},getYaml(){return _},setYaml(e,t){return _=String(e??``),k=t?.spec&&t?.function?t:void 0,l.value=_,++b,S=!1,x=n(s),A(_)},focus(){v&&typeof u.focus==`function`?u.focus():l.focus()},destroy(){y||(y=!0,T?.disconnect(),C.clear(),++b,l.removeEventListener?.(`input`,j),l.removeEventListener?.(`pointerdown`,M),l.removeEventListener?.(`keydown`,M),u.removeEventListener?.(`value-changed`,N),u.removeEventListener?.(`editor-save`,P))}};t(s,F);let I=()=>{E()&&typeof u.setValue==`function`&&A(_)};customElements.get(i)?I():d().then(e=>{E()&&(e?I():D())}).catch(()=>{E()&&D()})}export{g as bindNativeToolYaml,d as ensureNativeYamlEditor,f as nativeStarterConfig,p as repairToolConfig};