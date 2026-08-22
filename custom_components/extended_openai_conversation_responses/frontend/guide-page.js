import {GUIDE_QUICK_TASKS, GUIDE_TOPICS, MEMORY_COMPARISON} from "./guide-content.js";

function blockSearchText(block) {
  if (typeof block === "string") return block;
  if (!block || typeof block !== "object") return "";
  return [block.title, block.text, ...(block.items || [])].filter(Boolean).join(" ");
}

function topicSearchText(topic) {
  return [
    topic.title,
    topic.summary,
    topic.terms,
    ...(topic.body || []).map(blockSearchText),
  ].filter(Boolean).join(" ").toLowerCase();
}

function renderGuideBlock(panel, block) {
  if (typeof block === "string") return `<p>${panel._e(block)}</p>`;
  if (!block || typeof block !== "object") return "";

  if (block.type === "heading") {
    return `<h3>${panel._e(block.text || "")}</h3>`;
  }
  if (block.type === "list" || block.type === "steps") {
    const tag = block.type === "steps" ? "ol" : "ul";
    return `<${tag}>${(block.items || []).map((item) => `<li>${panel._e(item)}</li>`).join("")}</${tag}>`;
  }
  if (block.type === "note") {
    return `<div class="notice"><strong>${panel._e(block.title || "Good to know")}</strong><p>${panel._e(block.text || "")}</p></div>`;
  }
  return `<p>${panel._e(block.text || "")}</p>`;
}

function openGuideTopic(panel, topicId) {
  const root = panel.shadowRoot;
  const target = root.querySelector(`#guide-${CSS.escape(topicId)}`);
  if (!target) return;
  root.querySelectorAll(".guide-topic[open]").forEach((topic) => {
    if (topic !== target) topic.open = false;
  });
  target.open = true;
  panel._guideTopic = topicId;
  target.scrollIntoView({behavior: "smooth", block: "start"});
}

export function renderGuide(panel) {
  const query = String(panel._guideQuery || "").trim().toLowerCase();
  const topics = GUIDE_TOPICS.filter((topic) => !query || topicSearchText(topic).includes(query));

  return `<style>
      .guide-quick-start{display:grid;gap:12px}
      .guide-quick-start h2{margin:0;font-size:18px}
      .guide-quick-start>p{margin:0;color:var(--secondary-text-color);line-height:1.5}
      .guide-quick-tasks{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:4px}
      .guide-quick-card{display:grid;align-content:start;gap:5px;min-height:0;padding:15px 16px;text-align:left;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:10px}
      .guide-quick-card:hover{border-color:var(--primary-color);background:var(--secondary-background-color)}
      .guide-quick-card strong{font-size:14px}
      .guide-quick-card small{font-weight:400;line-height:1.45;color:var(--secondary-text-color)}
      .guide-topics{display:grid!important;grid-template-columns:1fr!important;gap:12px}
      .guide-topic{margin:0!important;padding:0 22px!important;border-top:1px solid var(--divider-color)!important;scroll-margin-top:18px}
      .guide-topic summary{padding:17px 0!important}
      .guide-topic summary>span{display:grid;gap:5px}
      .guide-topic summary small{font-weight:400;line-height:1.45}
      .guide-topic-body{max-width:920px;padding:0 0 22px;line-height:1.6}
      .guide-topic-body>p{margin:10px 0}
      .guide-topic-body h3{margin:22px 0 8px;font-size:16px}
      .guide-topic-body ul,.guide-topic-body ol{margin:10px 0;padding-left:26px}
      .guide-topic-body li{margin:6px 0}
      .guide-topic-body .notice{margin:16px 0}
      .guide-topic-body .guide-action{margin-top:10px}
      @media(max-width:950px){.guide-quick-tasks{grid-template-columns:repeat(2,minmax(0,1fr))}}
      @media(max-width:600px){.guide-quick-tasks{grid-template-columns:1fr}.guide-topic{padding:0 17px!important}}
    </style>
    <section class="page-intro"><h1>Guide</h1><p>New to Extended OpenAI, or unsure which feature you need? Start here. This Guide explains the main features in plain language and links directly to the relevant settings.</p></section>
    <section class="content-card guide-quick-start">
      <h2>Common things you may want to do</h2>
      <p>Choose a goal to jump straight to the explanation.</p>
      <div class="guide-quick-tasks">${GUIDE_QUICK_TASKS.map((item) => `<button type="button" class="guide-quick-card" data-guide-topic="${panel._e(item.topic)}"><strong>${panel._e(item.title)}</strong><small>${panel._e(item.text)}</small></button>`).join("")}</div>
    </section>
    <label class="guide-search"><span class="sr-only">Search Guide topics</span><input id="guide-search" type="search" value="${panel._e(panel._guideQuery || "")}" placeholder="Search the Guide" aria-label="Search Guide topics"></label>
    <section class="guide-topics" aria-live="polite">${topics.map((topic) => `<details class="content-card guide-topic" id="guide-${topic.id}" ${panel._guideTopic === topic.id ? "open" : ""}><summary><span><strong>${panel._e(topic.title)}</strong><small>${panel._e(topic.summary)}</small></span></summary><div class="guide-topic-body">${(topic.body?.length ? topic.body : [{type:"p", text:topic.summary}]).map((block) => renderGuideBlock(panel, block)).join("")}<button type="button" class="secondary guide-action" data-page="${topic.action.page}" data-subsection="${topic.action.section}">${panel._e(topic.action.label)}</button></div></details>`).join("") || panel._empty("No Guide topics match your search.")}</section>
    <section class="content-card"><div class="section-heading"><div><h2>How the memory and history features differ</h2><p>These features can look similar at first. Use the smallest kind of retained data that fits what you want the assistant to remember or retrieve.</p></div></div><div class="comparison-table"><table><thead><tr><th>Feature</th><th>Best used for</th><th>How long it lasts</th><th>When the model uses it</th></tr></thead><tbody>${MEMORY_COMPARISON.map((row) => `<tr>${row.map((cell) => `<td>${panel._e(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;
}

export function bindGuide(panel) {
  const root = panel.shadowRoot;
  root.querySelector("#guide-search")?.addEventListener("input", (event) => {
    panel._guideQuery = event.target.value;
    panel._render();
    requestAnimationFrame(() => {
      const input = panel.shadowRoot.querySelector("#guide-search");
      input?.focus();
      if (input) input.setSelectionRange(input.value.length, input.value.length);
    });
  });

  root.querySelectorAll(".guide-topic").forEach((topic) => {
    topic.addEventListener("toggle", () => {
      const topicId = topic.id.replace(/^guide-/, "");
      if (topic.open) {
        panel._guideTopic = topicId;
        root.querySelectorAll(".guide-topic[open]").forEach((other) => {
          if (other !== topic) other.open = false;
        });
      } else if (panel._guideTopic === topicId) {
        panel._guideTopic = null;
      }
    });
  });

  root.querySelectorAll("[data-guide-topic]").forEach((button) => {
    button.addEventListener("click", () => openGuideTopic(panel, button.dataset.guideTopic));
  });

  root.querySelectorAll(".guide-action").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
}
