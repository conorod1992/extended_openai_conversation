const PATCHED = Symbol.for("extended-openai.usage-input-footprint");

const number = (value) => Number(value || 0).toLocaleString();
const approx = (value) => `~${number(value)}`;

function footprintMetric(title, primary, detail) {
  return `<div class="metric"><span>${title}</span><strong>${primary}</strong>${detail ? `<small>${detail}</small>` : ""}</div>`;
}

export function footprintMarkup(panel) {
  if (panel._inputFootprintLoading) {
    return `<section class="content-card"><div class="section-heading"><div><h2>Input footprint</h2><p>Measuring the locally assembled request content…</p></div></div></section>`;
  }
  if (panel._inputFootprintError) {
    return `<section class="content-card"><div class="section-heading"><div><h2>Input footprint</h2><p>Local footprint measurements could not be loaded. Provider-reported token usage below is unaffected.</p></div><button type="button" class="secondary" id="retry-input-footprint">Retry</button></div></section>`;
  }

  const result = panel._inputFootprint;
  if (!result) {
    return `<section class="content-card"><div class="section-heading"><div><h2>Input footprint</h2><p>Local footprint measurements will appear here when this page loads.</p></div></div></section>`;
  }

  const baseline = result.baseline || {};
  const savings = baseline.function_group_savings || {};
  const latest = result.latest;
  const provider = result.latest_provider_usage;
  const baselineDetail = `Without Function Groups: ${number(baseline.without_function_groups_characters)} characters · ${approx(baseline.without_function_groups_approx_tokens)} tokens`;
  const savingsDetail = `${number(savings.characters)} characters · ${approx(savings.approx_tokens)} tokens (${number(savings.percent)}%)`;
  const latestDetail = latest
    ? `Model input: ${number(latest.input_characters)} characters · tools: ${number(latest.tool_characters)} characters`
    : "Run the assistant once after Home Assistant starts to capture a live footprint.";
  const providerDetail = provider
    ? `${number(provider.cached_input_tokens)} cached · ${panel._e(provider.model || "Unknown model")}`
    : "Exact provider input tokens appear when retained request usage is available.";

  return `<section class="content-card input-footprint-card">
    <div class="section-heading"><div><h2>Input footprint</h2><p>How much locally assembled text and tool schema is being sent. Character counts are exact for the locally measured content; token figures prefixed with ~ are rough characters ÷ 4 estimates.</p></div></div>
    <div class="metric-grid compact input-footprint-grid">
      ${footprintMetric("Fresh baseline", `${number(baseline.characters)} characters`, `${approx(baseline.approx_tokens)} tokens · ${baselineDetail}`)}
      ${footprintMetric("Saved by Function Groups", `${number(savings.characters)} characters`, savingsDetail)}
      ${footprintMetric("Latest conversation request", latest ? `${number(latest.characters)} characters` : "—", latest ? `${approx(latest.approx_tokens)} tokens · ${latestDetail}` : latestDetail)}
      ${footprintMetric("Provider-reported input", provider ? `${number(provider.input_tokens)} tokens` : "—", providerDetail)}
    </div>
    <p class="chart-note">${panel._e(result.notice || "Approximate local token counts are not provider billing tokens.")} Image and PDF attachment payload bytes are not included in the local character footprint.</p>
  </section>`;
}

async function loadInputFootprint(panel) {
  const agentId = panel._agentId;
  if (!agentId || panel._viewKey() !== "usage-maintenance/usage") return;
  panel._inputFootprintLoading = true;
  panel._inputFootprintError = null;
  panel._render();
  try {
    const result = await panel._call("usage", "footprint");
    if (panel._agentId !== agentId || panel._viewKey() !== "usage-maintenance/usage") return;
    panel._inputFootprint = result;
    panel._inputFootprintAgentId = agentId;
  } catch (err) {
    if (panel._agentId !== agentId || panel._viewKey() !== "usage-maintenance/usage") return;
    panel._inputFootprint = null;
    panel._inputFootprintAgentId = agentId;
    panel._inputFootprintError = err?.message || String(err);
  } finally {
    if (panel._agentId === agentId && panel._viewKey() === "usage-maintenance/usage") {
      panel._inputFootprintLoading = false;
      panel._render();
    }
  }
}

function bindRetry(panel) {
  panel.shadowRoot?.querySelector("#retry-input-footprint")?.addEventListener("click", () => {
    void loadInputFootprint(panel);
  });
}

export function installUsageInputFootprint(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalLoadSection = prototype._loadSection;
    prototype._loadSection = async function(...args) {
      const result = await originalLoadSection.apply(this, args);
      if (this._viewKey() === "usage-maintenance/usage") {
        if (this._inputFootprintAgentId !== this._agentId) {
          this._inputFootprint = null;
          this._inputFootprintError = null;
        }
        await loadInputFootprint(this);
      }
      return result;
    };

    const originalUsage = prototype._usage;
    prototype._usage = function(...args) {
      return `${footprintMarkup(this)}${originalUsage.apply(this, args)}`;
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function(...args) {
      const result = originalBindActions.apply(this, args);
      if (this._viewKey() === "usage-maintenance/usage") bindRetry(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installUsageInputFootprint();
}
