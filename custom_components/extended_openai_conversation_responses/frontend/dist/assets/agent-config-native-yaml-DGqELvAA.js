import{n as e,r as t,t as n}from"./tool-yaml-editor-adapter-BGSKTua7.js";var r=`ha-yaml-editor`,i=`tool-yaml-native`,a=null,o=`spec:
  name: my_tool
  description: Describe what this tool does.
  parameters:
    type: object
    properties: {}
function:
  type: native
  name: ''
`,s=Object.freeze({spec:Object.freeze({name:`my_tool`,description:`Describe what this tool does.`,parameters:Object.freeze({type:`object`,properties:Object.freeze({})})}),function:Object.freeze({type:`native`,name:``})}),c=`
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
  #${i} {
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
  #${i}[hidden] { display: none; }
  #tool-yaml[hidden] { display: none !important; }
`;async function l(e=globalThis.customElements,t=globalThis.document){return e?.get?.(r)?!0:e?.whenDefined?(a||=(async()=>{if(t?.createElement)try{await(t.createElement(`partial-panel-resolver`)?.getRoutes?.([{component_name:`developer-tools`,url_path:`a`}]))?.routes?.a?.load?.(),e.get?.(r)||await t.createElement(`developer-tools-router`)?.routerOptions?.routes?.service?.load?.()}catch{}return e.get?.(r)||await e.whenDefined(r),!0})().finally(()=>{a=null}),a):!1}function u(e,t=null){return t!==null||String(e||``).replace(/\r\n/g,`
`)!==o?null:{spec:{name:s.spec.name,description:s.spec.description,parameters:{type:`object`,properties:{}}},function:{type:`native`,name:``}}}function d(e){let t=e?._repairToolIndex;if(!Number.isInteger(t))return null;let n=e?._result?.function_repair?.invalid_tools;if(!Array.isArray(n))return null;let r=n.find(e=>Number(e?.index)===t)?.tool;return!r||typeof r!=`object`||Array.isArray(r)?null:r}function f(e){let t=e.shadowRoot?.querySelector(`ha-code-editor`),n=t?.shadowRoot?.querySelector(`.cm-editor`);t&&n&&(t.style.height=`100%`,t.style.minHeight=`0`,n.style.height=`100%`,n.style.minHeight=`0`)}function p(e){if(!e||e.querySelector(`style[data-native-tool-yaml]`))return;let t=document.createElement(`style`);t.dataset.nativeToolYaml=``,t.textContent=c,e.append(t)}function m(a){let o=a?.shadowRoot,s=o?.querySelector(`#tool-yaml`),c=o?.querySelector(`#${i}`);if(!s||!c)return;p(o);let m=n(a);if(m?.nativeEditor===c){m.retryNative?.();return}let h=m?.getYaml?.()??String(s.value??``),g=!1,_=!1,v=0,y=new Set,b=()=>{if(_)return;let e=c.shadowRoot?.querySelector(`ha-code-editor`);for(let t of[c,c.shadowRoot,e?.shadowRoot])t&&!y.has(t)&&(x?.observe(t,{subtree:!0,childList:!0,attributes:!0,attributeFilter:[`aria-label`,`tabindex`]}),y.add(t));let t=e?.shadowRoot?.querySelector(`.cm-content`);if(!t)return;if(!e.shadowRoot.querySelector(`style[data-eoc-yaml-accessibility]`)){let t=document.createElement(`style`);t.dataset.eocYamlAccessibility=``,t.textContent=`.cm-editor .cm-tooltip{background:var(--card-background-color);color:var(--primary-text-color);border-color:var(--divider-color)}.cm-editor .cm-panel.cm-panel-lint ul li[aria-selected]{background:var(--secondary-background-color);color:var(--primary-text-color)}`,e.shadowRoot.append(t)}let n=c.getAttribute?.(`aria-label`)||`Function Tool YAML`;t.getAttribute(`aria-label`)!==n&&t.setAttribute(`aria-label`,n),t.tabIndex!==0&&(t.tabIndex=0),f(c)},x=globalThis.MutationObserver?new MutationObserver(b):null;b();let S=()=>!_&&a?._toolYamlEditorAdapter===k&&c.isConnected,C=()=>{g=!1,c.hidden=!0,s.hidden=!1},w=e=>{try{return c.setValue(e),f(c),!0}catch{return C(),!1}},T=async e=>{if(!g||typeof c.setValue!=`function`)return;let t=++v;if(!String(e||``).trim()){S()&&t===v&&w({})&&(c.isValid=!0);return}let n=d(a);if(n){S()&&t===v&&w(n);return}try{let n=await a._call(`tools`,`validate_yaml`,{yaml:e});if(!S()||t!==v)return;if(n?.valid){w(n.config);return}let r=u(e,a._toolOriginalName??null);r?w(r):C()}catch{S()&&t===v&&C()}},E=()=>{++v,h=String(s.value??``),t(a,h)},D=e=>{++v,h=String(c.yaml??``),s.value=h,t(a,h,e.detail||{})},O=()=>{let e=o.querySelector(`#tool-dialog`),t=o.querySelector(`#tool-save`);e?.open&&t&&!t.disabled&&t.click()};s.addEventListener?.(`input`,E),c.addEventListener(`value-changed`,D),c.addEventListener(`editor-save`,O);let k={textarea:s,nativeEditor:c,retryNative(){g||A()},getYaml(){return h},setYaml(e){h=String(e??``),s.value=h,++v,g&&T(h)},focus(){g&&typeof c.focus==`function`?c.focus():s.focus()},destroy(){_||(_=!0,x?.disconnect(),y.clear(),++v,s.removeEventListener?.(`input`,E),c.removeEventListener?.(`value-changed`,D),c.removeEventListener?.(`editor-save`,O))}};e(a,k);let A=()=>{if(S()&&typeof c.setValue==`function`)try{g=!0,s.hidden=!0,c.hidden=!1,c.inDialog=!0,f(c),T(h)}catch{C()}};customElements.get(r)?A():l().then(e=>{S()&&(e?A():C())}).catch(()=>{S()&&C()})}export{m as bindNativeToolYaml,l as ensureNativeYamlEditor,u as nativeStarterConfig,d as repairToolConfig};