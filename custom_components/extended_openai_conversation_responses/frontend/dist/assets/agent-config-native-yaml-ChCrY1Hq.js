import{a as e,o as t,s as n}from"./tool-yaml-editor-adapter-Dly2McRT.js";var r=`ha-yaml-editor`,i=`tool-yaml-native`;function a(e){return!e||typeof e!=`object`?!1:Object.keys(e).length===1&&typeof e.__eoai_management_yaml_v1__==`string`||Object.values(e).some(a)}var o=null,s=`spec:
  name: my_tool
  description: Describe what this tool does.
  parameters:
    type: object
    properties: {}
function:
  type: native
  name: ''
`,c=Object.freeze({spec:Object.freeze({name:`my_tool`,description:`Describe what this tool does.`,parameters:Object.freeze({type:`object`,properties:Object.freeze({})})}),function:Object.freeze({type:`native`,name:``})}),l=`
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
`;async function u(e=globalThis.customElements,t=globalThis.document){return e?.get?.(r)?!0:e?.whenDefined?(o||=(async()=>{if(t?.createElement)try{await(t.createElement(`partial-panel-resolver`)?.getRoutes?.([{component_name:`developer-tools`,url_path:`a`}]))?.routes?.a?.load?.(),e.get?.(r)||await t.createElement(`developer-tools-router`)?.routerOptions?.routes?.service?.load?.()}catch{}return e.get?.(r)||await e.whenDefined(r),!0})().finally(()=>{o=null}),o):!1}function d(e,t=null){return t!==null||String(e||``).replace(/\r\n/g,`
`)!==s?null:{spec:{name:c.spec.name,description:c.spec.description,parameters:{type:`object`,properties:{}}},function:{type:`native`,name:``}}}function f(e){let t=e?._repairToolIndex;if(!Number.isInteger(t))return null;let n=e?._result?.function_repair?.invalid_tools;if(!Array.isArray(n))return null;let r=n.find(e=>Number(e?.index)===t)?.tool;return!r||typeof r!=`object`||Array.isArray(r)?null:r}function p(e){let t=e.shadowRoot?.querySelector(`ha-code-editor`),n=t?.shadowRoot?.querySelector(`.cm-editor`);t&&n&&(t.style.height=`100%`,t.style.minHeight=`0`,n.style.height=`100%`,n.style.minHeight=`0`)}function m(e){if(!e||e.querySelector(`style[data-native-tool-yaml]`))return;let t=document.createElement(`style`);t.dataset.nativeToolYaml=``,t.textContent=l,e.append(t)}function h(o){let s=o?.shadowRoot,c=s?.querySelector(`#tool-yaml`),l=s?.querySelector(`#${i}`);if(!c||!l)return;m(s);let h=e(o);if(h?.nativeEditor===l){h.retryNative?.();return}let g=h?.getYaml?.()??String(c.value??``),_=!1,v=!1,y=0,b=new Set,x=()=>{if(v)return;let e=l.shadowRoot?.querySelector(`ha-code-editor`);for(let t of[l,l.shadowRoot,e?.shadowRoot])t&&!b.has(t)&&(S?.observe(t,{subtree:!0,childList:!0,attributes:!0,attributeFilter:[`aria-label`,`tabindex`]}),b.add(t));let t=e?.shadowRoot?.querySelector(`.cm-content`);if(!t)return;if(!e.shadowRoot.querySelector(`style[data-eoc-yaml-accessibility]`)){let t=document.createElement(`style`);t.dataset.eocYamlAccessibility=``,t.textContent=`.cm-editor .cm-tooltip{background:var(--card-background-color);color:var(--primary-text-color);border-color:var(--divider-color)}.cm-editor .cm-panel.cm-panel-lint ul li[aria-selected]{background:var(--secondary-background-color);color:var(--primary-text-color)}`,e.shadowRoot.append(t)}let n=l.getAttribute?.(`aria-label`)||`Function Tool YAML`;t.getAttribute(`aria-label`)!==n&&t.setAttribute(`aria-label`,n),t.tabIndex!==0&&(t.tabIndex=0),p(l)},S=globalThis.MutationObserver?new MutationObserver(x):null;x();let C=()=>!v&&o?._toolYamlEditorAdapter===A&&l.isConnected,w=()=>{_=!1,l.hidden=!0,c.hidden=!1},T=e=>{if(a(e))return w(),!1;try{return l.setValue(e),p(l),!0}catch{return w(),!1}},E=async e=>{if(!_||typeof l.setValue!=`function`)return;let t=++y;if(!String(e||``).trim()){C()&&t===y&&T({})&&(l.isValid=!0);return}let n=String(e??``)===String(o._toolInitialYaml??``)?f(o):null;if(n){C()&&t===y&&T(n);return}try{let n=await o._call(`tools`,`validate_yaml`,{yaml:e});if(!C()||t!==y)return;if(n?.valid){T(n.config);return}let r=d(e,o._toolOriginalName??null);r?T(r):w()}catch{C()&&t===y&&w()}},D=()=>{++y,g=String(c.value??``),n(o,g)},O=e=>{++y,g=String(l.yaml??``),c.value=g,n(o,g,e.detail||{})},k=()=>{let e=s.querySelector(`#tool-dialog`),t=s.querySelector(`#tool-save`);e?.open&&t&&!t.disabled&&t.click()};c.addEventListener?.(`input`,D),l.addEventListener(`value-changed`,O),l.addEventListener(`editor-save`,k);let A={textarea:c,nativeEditor:l,retryNative(){_||j()},getYaml(){return g},setYaml(e){g=String(e??``),c.value=g,++y,_&&E(g)},focus(){_&&typeof l.focus==`function`?l.focus():c.focus()},destroy(){v||(v=!0,S?.disconnect(),b.clear(),++y,c.removeEventListener?.(`input`,D),l.removeEventListener?.(`value-changed`,O),l.removeEventListener?.(`editor-save`,k))}};t(o,A);let j=()=>{if(C()&&typeof l.setValue==`function`)try{_=!0,c.hidden=!0,l.hidden=!1,l.inDialog=!0,p(l),E(g)}catch{w()}};customElements.get(r)?j():u().then(e=>{C()&&(e?j():w())}).catch(()=>{C()&&w()})}export{h as bindNativeToolYaml,u as ensureNativeYamlEditor,d as nativeStarterConfig,f as repairToolConfig};