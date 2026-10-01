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
`)!==o?null:{spec:{name:s.spec.name,description:s.spec.description,parameters:{type:`object`,properties:{}}},function:{type:`native`,name:``}}}function d(e){let t=e?._repairToolIndex;if(!Number.isInteger(t))return null;let n=e?._result?.function_repair?.invalid_tools;if(!Array.isArray(n))return null;let r=n.find(e=>Number(e?.index)===t)?.tool;return!r||typeof r!=`object`||Array.isArray(r)?null:r}function f(e){let t=e.shadowRoot?.querySelector(`ha-code-editor`),n=t?.shadowRoot?.querySelector(`.cm-editor`);t&&n&&(t.style.height=`100%`,t.style.minHeight=`0`,n.style.height=`100%`,n.style.minHeight=`0`)}function p(e){if(!e||e.querySelector(`style[data-native-tool-yaml]`))return;let t=document.createElement(`style`);t.dataset.nativeToolYaml=``,t.textContent=c,e.append(t)}function m(a){let o=a?.shadowRoot,s=o?.querySelector(`#tool-yaml`),c=o?.querySelector(`#${i}`);if(!s||!c)return;p(o);let m=n(a);if(m?.nativeEditor===c)return;let h=m?.getYaml?.()??String(s.value??``),g=!1,_=!1,v=0,y=()=>!_&&a?._toolYamlEditorAdapter===E&&c.isConnected,b=()=>{g=!1,c.hidden=!0,s.hidden=!1},x=e=>{try{return c.setValue(e),f(c),!0}catch{return b(),!1}},S=async e=>{if(!g||typeof c.setValue!=`function`)return;let t=++v;if(!String(e||``).trim()){y()&&t===v&&x({})&&(c.isValid=!0);return}let n=d(a);if(n){y()&&t===v&&x(n);return}try{let n=await a._call(`tools`,`validate_yaml`,{yaml:e});if(!y()||t!==v)return;if(n?.valid){x(n.config);return}let r=u(e,a._toolOriginalName??null);r?x(r):b()}catch{y()&&t===v&&b()}},C=()=>{++v,h=String(s.value??``),t(a,h)},w=e=>{++v,h=String(c.yaml??``),s.value=h,t(a,h,e.detail||{})},T=()=>{let e=o.querySelector(`#tool-dialog`),t=o.querySelector(`#tool-save`);e?.open&&t&&!t.disabled&&t.click()};s.addEventListener?.(`input`,C),c.addEventListener(`value-changed`,w),c.addEventListener(`editor-save`,T);let E={textarea:s,nativeEditor:c,getYaml(){return h},setYaml(e){h=String(e??``),s.value=h,++v,g&&S(h)},focus(){g&&typeof c.focus==`function`?c.focus():s.focus()},destroy(){_||(_=!0,++v,s.removeEventListener?.(`input`,C),c.removeEventListener?.(`value-changed`,w),c.removeEventListener?.(`editor-save`,T))}};e(a,E);let D=()=>{if(y()&&typeof c.setValue==`function`)try{g=!0,s.hidden=!0,c.hidden=!1,c.inDialog=!0,f(c),S(h)}catch{b()}};customElements.get(r)?D():l().then(e=>{y()&&(e?D():b())}).catch(()=>{y()&&b()})}export{m as bindNativeToolYaml,l as ensureNativeYamlEditor,u as nativeStarterConfig,d as repairToolConfig};