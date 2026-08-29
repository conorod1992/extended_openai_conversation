const tokenCount = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
};

export function tokenBreakdown(totalTokens, cachedInputTokens) {
  const total = tokenCount(totalTokens);
  const cached = Math.min(total, tokenCount(cachedInputTokens));
  return { total, cached, uncached: total - cached };
}

export function formatUsageNumber(value, locales = undefined) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value ?? "");
  return Math.trunc(parsed).toLocaleString(locales);
}

export function formatUsageTimestamp(value, locales = undefined, timeZone = undefined) {
  const exact = typeof value === "string" ? value : "";
  const date = new Date(exact);
  if (!exact || Number.isNaN(date.getTime())) {
    return {display: exact || "Unknown", datetime: exact};
  }
  try {
    return {
      display: new Intl.DateTimeFormat(locales, {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", timeZone,
      }).format(date),
      datetime: exact,
    };
  } catch (_) {
    return {display: date.toLocaleString(), datetime: exact};
  }
}

const diagnosticCounters = [
  "run_count", "successful_run_count", "failed_run_count", "api_request_count",
  "successful_request_count", "failed_request_count", "input_tokens", "output_tokens",
  "total_tokens", "cached_input_tokens", "reasoning_tokens", "tool_call_count",
  "web_search_run_count", "total_run_duration_ms",
];

function mergeBreakdown(target, source) {
  if (!source || typeof source !== "object") return;
  for (const [name, value] of Object.entries(source)) {
    target[name] = (target[name] || 0) + tokenCount(value);
  }
}

export function summarizeUsageDiagnostics(days = []) {
  const summary = Object.fromEntries(diagnosticCounters.map((key) => [key, 0]));
  summary.provider_breakdown = {};
  summary.model_breakdown = {};
  summary.api_mode_breakdown = {};
  for (const day of Array.isArray(days) ? days : []) {
    for (const key of diagnosticCounters) summary[key] += tokenCount(day?.[key]);
    mergeBreakdown(summary.provider_breakdown, day?.provider_breakdown);
    mergeBreakdown(summary.model_breakdown, day?.model_breakdown);
    mergeBreakdown(summary.api_mode_breakdown, day?.api_mode_breakdown);
  }
  summary.cache_percent = summary.input_tokens ? Math.min(100, summary.cached_input_tokens / summary.input_tokens * 100) : null;
  summary.run_success_percent = summary.run_count ? summary.successful_run_count / summary.run_count * 100 : null;
  summary.request_success_percent = summary.api_request_count ? summary.successful_request_count / summary.api_request_count * 100 : null;
  summary.average_tokens_per_run = summary.run_count ? summary.total_tokens / summary.run_count : 0;
  summary.average_requests_per_run = summary.run_count ? summary.api_request_count / summary.run_count : 0;
  summary.average_duration_ms = summary.run_count ? summary.total_run_duration_ms / summary.run_count : 0;
  return summary;
}

export function sortedUsageBreakdown(values = {}) {
  return Object.entries(values || {})
    .map(([name, tokens]) => ({name, tokens: tokenCount(tokens)}))
    .sort((a, b) => b.tokens - a.tokens || a.name.localeCompare(b.name));
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${value.toFixed(value >= 99.95 ? 0 : 1)}%` : "—";
}

function formatDuration(value) {
  const milliseconds = Number(value) || 0;
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  const seconds = milliseconds / 1000;
  return seconds < 60 ? `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s` : `${(seconds / 60).toFixed(1)} min`;
}

function diagnosticMetric(panel, title, value, detail = "") {
  return `<article class="usage-diagnostic-metric"><span>${panel._e(title)}</span><strong>${panel._e(value)}</strong>${detail ? `<small>${panel._e(detail)}</small>` : ""}</article>`;
}

function breakdownList(panel, title, values) {
  const rows = sortedUsageBreakdown(values);
  const total = rows.reduce((sum, row) => sum + row.tokens, 0);
  return `<section class="usage-breakdown"><h3>${panel._e(title)}</h3>${rows.length ? `<div class="usage-breakdown-list">${rows.map((row) => {
    const share = total ? row.tokens / total * 100 : 0;
    return `<div class="usage-breakdown-row"><span title="${panel._e(row.name)}">${panel._e(row.name)}</span><strong>${formatUsageNumber(row.tokens)}</strong><small>${panel._e(formatPercent(share))}</small></div>`;
  }).join("")}</div>` : `<p class="usage-diagnostic-empty">No recorded data in this window.</p>`}</section>`;
}

export function renderUsageDiagnostics(panel, result = {}) {
  const days = result.days?.days || [];
  const visibleDays = days.slice(-31);
  const summary = summarizeUsageDiagnostics(visibleDays);
  const recentRuns = result.runs?.runs || [];
  const failedRuns = recentRuns.filter((run) => run.successful === false).slice(0, 5);
  const recentLocalRuns = recentRuns.filter((run) => tokenCount(run.request_count) === 0).length;
  const windowLabel = visibleDays.length
    ? `${visibleDays[0].date} to ${visibleDays[visibleDays.length - 1].date}`
    : "No daily usage recorded yet";
  const cacheDetail = summary.input_tokens
    ? `${formatUsageNumber(summary.cached_input_tokens)} of ${formatUsageNumber(summary.input_tokens)} input tokens`
    : "No provider-reported input tokens";

  return `<style>
      .usage-diagnostics{display:grid;gap:22px}
      .usage-diagnostics-heading{display:flex;justify-content:space-between;align-items:start;gap:20px}
      .usage-diagnostics-heading h2,.usage-diagnostics-heading p{margin:0}.usage-diagnostics-heading p{margin-top:6px;color:var(--secondary-text-color);line-height:1.5}
      .usage-window{white-space:nowrap;color:var(--secondary-text-color);font-size:13px}
      .usage-diagnostic-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
      .usage-diagnostic-metric{display:grid;gap:5px;padding:15px;border:1px solid var(--divider-color);border-radius:10px;background:var(--secondary-background-color)}
      .usage-diagnostic-metric span,.usage-diagnostic-metric small{color:var(--secondary-text-color);font-size:13px}.usage-diagnostic-metric strong{font-size:20px;font-weight:600}
      .usage-diagnostic-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
      .usage-diagnostic-panel{padding:18px;border:1px solid var(--divider-color);border-radius:11px}.usage-diagnostic-panel h3{margin:0 0 13px;font-size:16px}
      .usage-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.usage-fact{display:grid;gap:3px}.usage-fact span{color:var(--secondary-text-color);font-size:13px}.usage-fact strong{font-size:16px}
      .usage-breakdowns{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.usage-breakdown{min-width:0;padding:17px;border:1px solid var(--divider-color);border-radius:11px}.usage-breakdown h3{margin:0 0 12px;font-size:15px}
      .usage-breakdown-list{display:grid;gap:9px}.usage-breakdown-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:9px;align-items:baseline}.usage-breakdown-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.usage-breakdown-row small{min-width:48px;text-align:right}.usage-diagnostic-empty{margin:0;color:var(--secondary-text-color);font-size:13px}
      .usage-failures{display:grid;gap:9px}.usage-failure{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;padding:11px 0;border-top:1px solid var(--divider-color)}.usage-failure:first-child{border-top:0;padding-top:0}.usage-failure p{margin:0}.usage-failure small{display:block;margin-top:4px}.usage-run-details{cursor:pointer;color:var(--primary-color);background:transparent;border:1px solid var(--primary-color);min-height:36px;padding:6px 10px}
      .usage-request-list{display:grid;gap:12px}.usage-request-card{padding:14px;border:1px solid var(--divider-color);border-radius:10px}.usage-request-card h3,.usage-request-card p{margin:0}.usage-request-card p{margin-top:6px;color:var(--secondary-text-color)}.usage-request-meta{display:flex;gap:8px 14px;flex-wrap:wrap;margin-top:9px;color:var(--secondary-text-color);font-size:13px}
      @media(max-width:950px){.usage-diagnostic-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.usage-breakdowns{grid-template-columns:1fr}}
      @media(max-width:680px){.usage-diagnostics-heading{display:grid}.usage-window{white-space:normal}.usage-diagnostic-grid,.usage-diagnostic-columns,.usage-facts{grid-template-columns:1fr}.usage-failure{grid-template-columns:1fr}.usage-run-details{width:100%}}
    </style>
    <section class="content-card usage-diagnostics" aria-label="Usage diagnostics">
      <div class="usage-diagnostics-heading"><div><h2>Usage diagnostics</h2><p>Use these figures to spot repeated provider calls, slow turns, failures, and how much input is being served from cache.</p></div><span class="usage-window">${panel._e(windowLabel)}</span></div>
      <div class="usage-diagnostic-grid">
        ${diagnosticMetric(panel, "Cached input", formatPercent(summary.cache_percent), cacheDetail)}
        ${diagnosticMetric(panel, "Run success", formatPercent(summary.run_success_percent), `${formatUsageNumber(summary.failed_run_count)} failed run${summary.failed_run_count === 1 ? "" : "s"}`)}
        ${diagnosticMetric(panel, "Requests per run", summary.average_requests_per_run.toFixed(summary.average_requests_per_run >= 10 ? 1 : 2), `${formatUsageNumber(summary.api_request_count)} provider requests`)}
        ${diagnosticMetric(panel, "Average run time", formatDuration(summary.average_duration_ms), `${formatUsageNumber(summary.run_count)} completed runs`)}
      </div>
      <div class="usage-diagnostic-columns">
        <section class="usage-diagnostic-panel"><h3>Token mix</h3><div class="usage-facts">
          <div class="usage-fact"><span>Input</span><strong>${formatUsageNumber(summary.input_tokens)}</strong></div>
          <div class="usage-fact"><span>Output</span><strong>${formatUsageNumber(summary.output_tokens)}</strong></div>
          <div class="usage-fact"><span>Cached input</span><strong>${formatUsageNumber(summary.cached_input_tokens)}</strong></div>
          <div class="usage-fact"><span>Reasoning</span><strong>${formatUsageNumber(summary.reasoning_tokens)}</strong></div>
          <div class="usage-fact"><span>Average total / run</span><strong>${formatUsageNumber(Math.round(summary.average_tokens_per_run))}</strong></div>
          <div class="usage-fact"><span>Request success</span><strong>${panel._e(formatPercent(summary.request_success_percent))}</strong></div>
        </div></section>
        <section class="usage-diagnostic-panel"><h3>Activity</h3><div class="usage-facts">
          <div class="usage-fact"><span>Conversation runs</span><strong>${formatUsageNumber(summary.run_count)}</strong></div>
          <div class="usage-fact"><span>Provider requests</span><strong>${formatUsageNumber(summary.api_request_count)}</strong></div>
          <div class="usage-fact"><span>Tool calls</span><strong>${formatUsageNumber(summary.tool_call_count)}</strong></div>
          <div class="usage-fact"><span>Web-search runs</span><strong>${formatUsageNumber(summary.web_search_run_count)}</strong></div>
          <div class="usage-fact"><span>Recent zero-request runs</span><strong>${formatUsageNumber(recentLocalRuns)}</strong></div>
          <div class="usage-fact"><span>Failed provider requests</span><strong>${formatUsageNumber(summary.failed_request_count)}</strong></div>
        </div></section>
      </div>
      <div class="usage-breakdowns">
        ${breakdownList(panel, "Models", summary.model_breakdown)}
        ${breakdownList(panel, "Providers", summary.provider_breakdown)}
        ${breakdownList(panel, "API modes", summary.api_mode_breakdown)}
      </div>
      <section class="usage-diagnostic-panel"><h3>Recent failed runs</h3>${failedRuns.length ? `<div class="usage-failures">${failedRuns.map((run) => {
        const completed = formatUsageTimestamp(run.completed_at, undefined, panel._hass?.config?.time_zone);
        return `<div class="usage-failure"><div><p><strong>${panel._e(run.error_type || "Failed")}</strong></p><small>${panel._e(completed.display)} · ${formatUsageNumber(run.request_count)} request${run.request_count === 1 ? "" : "s"} · ${panel._e(formatDuration(run.duration_ms))}</small></div><button type="button" class="usage-run-details" data-usage-run-id="${panel._e(run.run_id)}">View requests</button></div>`;
      }).join("")}</div>` : `<p class="usage-diagnostic-empty">No failed runs are present in the retained recent-run details.</p>`}</section>
      <p class="help">The diagnostics use the same visible daily window as the chart. “Recent zero-request runs” uses the retained recent-run list and can include requests handled locally without a provider call. Figures show provider-reported usage only; they do not estimate API cost or expose prompt, response, tool-argument, or reasoning content.</p>
    </section>`;
}

function requestDetailsDialog() {
  return `<dialog id="usage-request-dialog" class="editor-dialog wide" aria-labelledby="usage-request-title"><div class="dialog-header"><h2 id="usage-request-title">Provider requests</h2><button type="button" class="icon close-usage-requests" aria-label="Close">×</button></div><div id="usage-request-body" class="dialog-body"></div><div class="dialog-actions"><button type="button" class="secondary close-usage-requests">Close</button></div></dialog>`;
}

function renderRequestDetails(panel, requests) {
  if (!requests.length) return `<div class="empty">No retained provider-request details are available for this run.</div>`;
  return `<div class="usage-request-list">${requests.map((request, index) => {
    const tokens = tokenBreakdown(request.total_tokens, request.cached_input_tokens);
    const timestamp = formatUsageTimestamp(request.timestamp, undefined, panel._hass?.config?.time_zone);
    return `<article class="usage-request-card"><h3>Request ${index + 1} · ${panel._e(request.successful ? "Success" : request.error_type || "Failed")}</h3><p>${panel._e(request.provider || "Unknown provider")} · ${panel._e(request.model || "Unknown model")} · ${panel._e(request.api_mode || "Unknown API mode")}</p><div class="usage-request-meta"><span>${panel._e(timestamp.display)}</span><span>${panel._e(request.request_stage || "other")}</span><span>${formatUsageNumber(tokens.total)} tokens</span><span>${formatUsageNumber(tokens.cached)} cached</span><span>${formatUsageNumber(request.reasoning_tokens || 0)} reasoning</span><span>${panel._e(formatDuration(request.duration_ms))}</span>${request.tool_calls_requested ? `<span>${formatUsageNumber(request.tool_calls_requested)} tool call${request.tool_calls_requested === 1 ? "" : "s"}</span>` : ""}${request.web_search_used ? `<span>Web search</span>` : ""}</div></article>`;
  }).join("")}</div>`;
}

function installUsageDiagnostics() {
  if (typeof customElements === "undefined") return;
  customElements.whenDefined("extended-openai-management-panel").then(() => {
    const Panel = customElements.get("extended-openai-management-panel");
    const prototype = Panel?.prototype;
    if (!prototype || prototype.__usageDiagnosticsInstalled) return;
    prototype.__usageDiagnosticsInstalled = true;

    const originalUsage = prototype._usage;
    prototype._usage = function() {
      const html = originalUsage.call(this);
      const diagnostics = renderUsageDiagnostics(this, this._result || {});
      const maintenance = '<section class="content-card"><h2>Usage detail maintenance</h2>';
      return html.includes(maintenance) ? html.replace(maintenance, `${diagnostics}${maintenance}`) : `${html}${diagnostics}`;
    };

    const originalDialogs = prototype._dialogs;
    prototype._dialogs = function() {
      return `${originalDialogs.call(this)}${requestDetailsDialog()}`;
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function() {
      originalBindActions.call(this);
      const root = this.shadowRoot;
      root.querySelectorAll(".close-usage-requests").forEach((button) => button.addEventListener("click", () => root.querySelector("#usage-request-dialog")?.close()));
      root.querySelector("#usage-request-dialog")?.addEventListener("cancel", (event) => { event.preventDefault(); root.querySelector("#usage-request-dialog")?.close(); });
      root.querySelectorAll(".usage-run-details").forEach((button) => button.addEventListener("click", async () => {
        const dialog = root.querySelector("#usage-request-dialog");
        const body = root.querySelector("#usage-request-body");
        if (!dialog || !body) return;
        body.innerHTML = this._loading();
        dialog.showModal();
        try {
          const response = await this._call("usage", "requests", {run_id: button.dataset.usageRunId, limit: 100});
          if (!dialog.open) return;
          body.innerHTML = renderRequestDetails(this, response?.requests || []);
        } catch (err) {
          if (dialog.open) body.innerHTML = `<div class="error" role="alert">${this._e(err.message || String(err))}</div>`;
        }
      }));
    };
  });
}

installUsageDiagnostics();
