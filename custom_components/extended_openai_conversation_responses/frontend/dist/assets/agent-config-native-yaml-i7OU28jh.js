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
  #tool-dialog #tool-editor-label {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    min-height: 0;
  }
  #${i} {
    display: block;
    flex: 1 1 auto;
    width: 100%;
    min-height: 240px;
    height: 100%;
    cursor: text;
  }
  #tool-dialog .dialog-actions {
    flex: 0 0 auto;
  }
  #${i}[hidden] { display: none; }
  #tool-yaml[hidden] { display: none !important; }
`;async function l(e=globalThis.customElements,t=globalThis.document){return e?.get?.(r)?!0:e?.whenDefined?(a||=(async()=>{if(t?.createElement)try{await(t.createElement(`partial-panel-resolver`)?.getRoutes?.([{component_name:`developer-tools`,url_path:`a`}]))?.routes?.a?.load?.(),e.get?.(r)||await t.createElement(`developer-tools-router`)?.routerOptions?.routes?.service?.load?.()}catch{}return e.get?.(r)||await e.whenDefined(r),!0})().finally(()=>{a=null}),a):!1}function u(e,t=null){return t!==null||String(e||``).replace(/\r\n/g,`
`)!==o?null:{spec:{name:s.spec.name,description:s.spec.description,parameters:{type:`object`,properties:{}}},function:{type:`native`,name:``}}}function d(e){let t=e?._repairToolIndex;if(!Number.isInteger(t))return null;let n=e?._result?.function_repair?.invalid_tools;if(!Array.isArray(n))return null;let r=n.find(e=>Number(e?.index)===t)?.tool;return!r||typeof r!=`object`||Array.isArray(r)?null:r}function f(e){if(!e||e.querySelector(`style[data-native-tool-yaml]`))return;let t=document.createElement(`style`);t.dataset.nativeToolYaml=``,t.textContent=c,e.append(t)}function p(a){let o=a?.shadowRoot,s=o?.querySelector(`#tool-yaml`),c=o?.querySelector(`#${i}`);if(!s||!c)return;f(o);let p=n(a);if(p?.nativeEditor===c)return;let m=p?.getYaml?.()??String(s.value??``),h=!1,g=!1,_=0,v=()=>!g&&a?._toolYamlEditorAdapter===T&&c.isConnected,y=()=>{h=!1,c.hidden=!0,s.hidden=!1},b=e=>{try{return c.setValue(e),!0}catch{return y(),!1}},x=async e=>{if(!h||typeof c.setValue!=`function`)return;let t=++_;if(!String(e||``).trim()){v()&&t===_&&b({})&&(c.isValid=!0);return}let n=d(a);if(n){v()&&t===_&&b(n);return}try{let n=await a._call(`tools`,`validate_yaml`,{yaml:e});if(!v()||t!==_)return;if(n?.valid){b(n.config);return}let r=u(e,a._toolOriginalName??null);r?b(r):y()}catch{v()&&t===_&&y()}},S=()=>{++_,m=String(s.value??``),t(a,m)},C=e=>{++_,m=String(c.yaml??``),s.value=m,t(a,m,e.detail||{})},w=()=>{let e=o.querySelector(`#tool-dialog`),t=o.querySelector(`#tool-save`);e?.open&&t&&!t.disabled&&t.click()};s.addEventListener?.(`input`,S),c.addEventListener(`value-changed`,C),c.addEventListener(`editor-save`,w);let T={textarea:s,nativeEditor:c,getYaml(){return m},setYaml(e){m=String(e??``),s.value=m,++_,h&&x(m)},focus(){h&&typeof c.focus==`function`?c.focus():s.focus()},destroy(){g||(g=!0,++_,s.removeEventListener?.(`input`,S),c.removeEventListener?.(`value-changed`,C),c.removeEventListener?.(`editor-save`,w))}};e(a,T);let E=()=>{if(v()&&typeof c.setValue==`function`)try{h=!0,s.hidden=!0,c.hidden=!1,c.inDialog=!0,x(m)}catch{y()}};customElements.get(r)?E():l().then(e=>{v()&&(e?E():y())}).catch(()=>{v()&&y()})}export{p as bindNativeToolYaml,l as ensureNativeYamlEditor,u as nativeStarterConfig,d as repairToolConfig};