// Component-host fixtures model HA's lazy registration, not its full editor UI.
// Genuine native controls are exercised separately in the authenticated HA shell.
const mode = new URLSearchParams(location.search).get("selector_load");
window.selectorFixture = {loads:0, fail:mode === "fail", initiallyUndefined:!customElements.get("ha-selector")};
const gate = mode === "hold" ? new Promise(resolve => {selectorFixture.release=resolve;}) : Promise.resolve();
if (!customElements.get("partial-panel-resolver")) customElements.define("partial-panel-resolver", class extends HTMLElement {
  getRoutes() {return {routes:{config:{load:async () => {selectorFixture.loads++; await gate;}}}};}
});
if (!customElements.get("ha-panel-config")) customElements.define("ha-panel-config", class extends HTMLElement {
  get routerOptions() {return {routes:{automation:{load:async () => {
    if (selectorFixture.fail) throw new Error("Controlled native selector load failure");
    if (!customElements.get("ha-selector-condition")) customElements.define("ha-selector-condition",class extends HTMLElement {});
    if (!customElements.get("ha-selector")) customElements.define("ha-selector",class extends HTMLElement {
      // A registered native control has a visible host box. Keep this stub's
      // footprint while genuine control rendering is checked in the HA shell.
      constructor() {super(); this.attachShadow({mode:"open"}).innerHTML='<style>:host{display:block;min-height:32px}:host([hidden]){display:none}</style>';}
      get updateComplete() {return Promise.resolve();}
      set hass(value) {this._hass=value;} get hass() {return this._hass;}
      set selector(value) {this._selector=value;} get selector() {return this._selector;}
      set value(value) {this._value=value;} get value() {return this._value;}
    });
  }}}};}
});
