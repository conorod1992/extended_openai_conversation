const WS_TYPE = "extended_openai_conversation_responses/intercom";

class ExtendedOpenAIIntercomPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._snapshot = null;
    this._selected = new Set();
    this._wholeHome = false;
    this._busy = false;
    this._error = null;
    this._message = "";
  }

  set hass(value) {
    const first = !this._hass;
    this._hass = value;
    if (first) this._load();
  }

  async _load() {
    if (!this._hass) return;
    try {
      this._snapshot = await this._hass.callWS({type: WS_TYPE, action: "snapshot"});
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._render();
  }

  async _send() {
    const message = this._message.trim();
    if (!message) {
      this._error = "Enter a message to broadcast.";
      this._render();
      return;
    }
    if (!this._wholeHome && !this._selected.size) {
      this._error = "Choose at least one Assist satellite or Whole home.";
      this._render();
      return;
    }
    this._busy = true;
    this._error = null;
    this._render();
    try {
      await this._hass.callWS({
        type: WS_TYPE,
        action: "send",
        message,
        whole_home: this._wholeHome,
        entity_ids: [...this._selected],
      });
      this._message = "";
      this._snapshot = await this._hass.callWS({type: WS_TYPE, action: "snapshot"});
    } catch (err) {
      this._error = err.message || String(err);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  _statusLabel(status) {
    return {
      pending: "Pending",
      queued_idle: "Queued",
      queued_busy: "Waiting for idle",
      waiting_idle: "Waiting for idle",
      delivering: "Delivering",
      delivered: "Delivered",
      failed: "Failed",
      expired: "Expired",
    }[status] || status;
  }

  _renderHistory() {
    const rows = this._snapshot?.history || [];
    if (!rows.length) return `<p class="muted">No broadcasts sent yet.</p>`;
    return rows.slice(0, 20).map((item) => {
      const deliveryRows = Object.entries(item.deliveries || {}).map(([entityId, delivery]) => {
        const state = this._snapshot?.catalog?.satellites?.find((sat) => sat.id === entityId);
        return `<li><span>${this._escape(state?.name || entityId)}</span><strong class="status ${this._escape(delivery.status)}">${this._escape(this._statusLabel(delivery.status))}</strong></li>`;
      }).join("");
      const created = new Date(item.created_at).toLocaleString();
      return `<article class="history-item"><div class="history-head"><strong>${this._escape(item.message)}</strong><small>${this._escape(created)}</small></div><ul>${deliveryRows}</ul></article>`;
    }).join("");
  }

  _render() {
    const satellites = this._snapshot?.catalog?.satellites || [];
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; color:var(--primary-text-color); background:var(--primary-background-color); min-height:100vh; }
        .shell { max-width:900px; margin:0 auto; padding:28px 18px 48px; }
        h1 { margin:0; font-size:30px; }
        .intro { margin:6px 0 24px; color:var(--secondary-text-color); }
        .card { background:var(--card-background-color); border-radius:14px; padding:20px; box-shadow:var(--ha-card-box-shadow); margin-bottom:18px; }
        label.title { display:block; font-weight:600; margin-bottom:8px; }
        textarea { box-sizing:border-box; width:100%; min-height:110px; resize:vertical; border:1px solid var(--divider-color); border-radius:10px; background:var(--card-background-color); color:var(--primary-text-color); padding:12px; font:inherit; }
        .targets { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin-top:14px; }
        .target { border:1px solid var(--divider-color); border-radius:10px; padding:11px 12px; display:flex; gap:10px; align-items:flex-start; }
        .target span { display:flex; flex-direction:column; gap:3px; }
        .target small, .muted, .history-head small { color:var(--secondary-text-color); }
        .whole { margin-top:14px; display:flex; gap:10px; align-items:center; font-weight:600; }
        .actions { display:flex; justify-content:flex-end; gap:10px; margin-top:18px; }
        button { border:0; border-radius:10px; padding:10px 18px; font:inherit; font-weight:600; cursor:pointer; background:var(--primary-color); color:var(--text-primary-color, white); }
        button.secondary { background:transparent; color:var(--primary-color); border:1px solid var(--primary-color); }
        button:disabled { opacity:.55; cursor:default; }
        .error { margin-top:12px; background:var(--error-color); color:white; border-radius:8px; padding:10px 12px; }
        .history-item { padding:14px 0; border-bottom:1px solid var(--divider-color); }
        .history-item:last-child { border-bottom:0; }
        .history-head { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }
        .history-head strong { font-weight:600; }
        ul { list-style:none; padding:0; margin:10px 0 0; display:grid; gap:6px; }
        li { display:flex; justify-content:space-between; gap:12px; }
        .status { font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
        .status.delivered { color:var(--success-color); }
        .status.failed, .status.expired { color:var(--error-color); }
        .status.queued_busy, .status.waiting_idle { color:var(--warning-color); }
      </style>
      <div class="shell">
        <h1>Intercom</h1>
        <p class="intro">Type a message and send it to one or more Assist satellites. Busy satellites wait until they are idle instead of having their current conversation interrupted.</p>
        <section class="card">
          <label class="title" for="message">Message</label>
          <textarea id="message" placeholder="Dinner is ready">${this._escape(this._message)}</textarea>
          <label class="whole"><input id="whole" type="checkbox" ${this._wholeHome ? "checked" : ""}> Whole home</label>
          <div class="targets">
            ${satellites.map((sat) => `<label class="target"><input type="checkbox" data-entity="${this._escape(sat.id)}" ${this._selected.has(sat.id) ? "checked" : ""} ${this._wholeHome ? "disabled" : ""}><span><strong>${this._escape(sat.name)}</strong><small>${this._escape(sat.state)}</small></span></label>`).join("") || `<p class="muted">No announcement-capable Assist satellites are available.</p>`}
          </div>
          ${this._error ? `<div class="error" role="alert">${this._escape(this._error)}</div>` : ""}
          <div class="actions"><button id="refresh" class="secondary" type="button" ${this._busy ? "disabled" : ""}>Refresh</button><button id="send" type="button" ${this._busy || !satellites.length ? "disabled" : ""}>${this._busy ? "Sending…" : "Send broadcast"}</button></div>
        </section>
        <section class="card"><h2>Recent broadcasts</h2>${this._renderHistory()}</section>
      </div>`;

    const message = this.shadowRoot.querySelector("#message");
    message?.addEventListener("input", (event) => { this._message = event.target.value; });
    this.shadowRoot.querySelector("#whole")?.addEventListener("change", (event) => { this._wholeHome = event.target.checked; this._render(); });
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((box) => box.addEventListener("change", (event) => {
      if (event.target.checked) this._selected.add(event.target.dataset.entity);
      else this._selected.delete(event.target.dataset.entity);
    }));
    this.shadowRoot.querySelector("#send")?.addEventListener("click", () => this._send());
    this.shadowRoot.querySelector("#refresh")?.addEventListener("click", () => this._load());
  }
}

customElements.define("extended-openai-intercom-panel", ExtendedOpenAIIntercomPanel);
