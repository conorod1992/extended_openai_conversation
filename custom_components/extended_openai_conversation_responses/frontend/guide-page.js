import {GUIDE_TOPICS, MEMORY_COMPARISON} from "./guide-content.js";

export function renderGuide(panel) {
  const query = String(panel._guideQuery || "").trim().toLowerCase();
  const topics = GUIDE_TOPICS.filter((topic) => `${topic.title} ${topic.summary} ${topic.terms}`.toLowerCase().includes(query));
  return `<section class="page-intro"><h1>Guide</h1><p>A quick map of how the assistant, its capabilities, and retained data fit together.</p></section>
    <label class="guide-search"><span class="sr-only">Search Guide topics</span><input id="guide-search" type="search" value="${panel._e(panel._guideQuery || "")}" placeholder="Search the Guide" aria-label="Search Guide topics"></label>
    <section class="guide-topics" aria-live="polite">${topics.map((topic) => `<details class="content-card guide-topic" id="guide-${topic.id}" ${panel._guideTopic === topic.id ? "open" : ""}><summary><span><strong>${panel._e(topic.title)}</strong><small>${panel._e(topic.summary)}</small></span></summary><div class="guide-topic-body"><p>${panel._e(topic.summary)}</p><button type="button" class="secondary guide-action" data-page="${topic.action.page}" data-subsection="${topic.action.section}">${panel._e(topic.action.label)}</button></div></details>`).join("") || panel._empty("No Guide topics match your search.")}</section>
    <section class="content-card"><div class="section-heading"><div><h2>How these features differ</h2><p>Choose the smallest kind of retained data that fits the job.</p></div></div><div class="comparison-table"><table><thead><tr><th>Feature</th><th>What it is for</th><th>How long it lasts</th><th>When the model uses it</th></tr></thead><tbody>${MEMORY_COMPARISON.map((row) => `<tr>${row.map((cell) => `<td>${panel._e(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;
}

export function bindGuide(panel) {
  const root = panel.shadowRoot;
  root.querySelector("#guide-search")?.addEventListener("input", (event) => { panel._guideQuery = event.target.value; panel._render(); requestAnimationFrame(() => panel.shadowRoot.querySelector("#guide-search")?.focus()); });
  root.querySelectorAll(".guide-action").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
}
