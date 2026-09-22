import {GUIDE_TOPICS, MEMORY_COMPARISON} from "./guide-content.js";

const GUIDE_QUICK_TASKS = [
  {title: "Keep a conversation going", text: "Make follow-up questions remember what you were just discussing.", topic: "continuity", icon: "mdi:message-text-clock-outline"},
  {title: "Broadcast a message", text: "Send a one-way spoken message to selected Assist satellites or the whole home.", topic: "broadcast", icon: "mdi:bullhorn-outline"},
  {title: "Remember facts and preferences", text: "Store useful information so you do not have to repeat it in later conversations.", topic: "persistent-memory", icon: "mdi:brain"},
  {title: "Give it reference material", text: "Add manuals, notes, policies or other larger information to the Knowledge Library.", topic: "knowledge", icon: "mdi:bookshelf"},
  {title: "Make satellites quieter at night", text: "Use Quiet Hours to lower Assist satellite volume and optionally suppress wake-word sounds.", topic: "quiet-hours", icon: "mdi:volume-low"},
  {title: "Let visitors use it safely", text: "Use Guest Mode to limit what visitors can see, control and remember.", topic: "guest-mode", icon: "mdi:account-lock-outline"},
];

const GUIDE_GROUPS = [
  {
    id: "getting-started",
    title: "Getting started",
    description: "Set up the assistant and choose the core model and provider behaviour.",
    topicIds: ["getting-started", "models"],
  },
  {
    id: "conversation-memory",
    title: "Conversation & memory",
    description: "Choose how recent discussions, saved facts, history, and reference material are retained and reused.",
    topicIds: ["continuity", "context-management", "memory", "persistent-memory", "temporary-memory", "archive", "knowledge"],
  },
  {
    id: "capabilities",
    title: "Capabilities & Home Assistant",
    description: "Control what the assistant can do, what stays local, and how extra capabilities are exposed.",
    topicIds: ["home-assistant", "functions", "request-rules", "local-handling", "broadcast"],
  },
  {
    id: "voice-speech",
    title: "Voice & speech",
    description: "Control voice identity, spoken-output cleanup, and how voice devices behave.",
    topicIds: ["quiet-hours", "voice", "speech-processing"],
  },
  {
    id: "privacy-access",
    title: "Privacy & access",
    description: "Understand data boundaries and restrict what visitors or signed-in users can access.",
    topicIds: ["guest-mode", "privacy"],
  },
  {
    id: "operations",
    title: "Operations & troubleshooting",
    description: "Understand diagnostics, request debugging, backups, model data, and maintenance behaviour.",
    topicIds: ["backup-restore", "request-debugging", "model-data", "usage"],
  },
];

function blockSearchText(block) {
  if (typeof block === "string") return block;
  if (!block || typeof block !== "object") return "";
  return [block.title, block.text, ...(block.items || [])].filter(Boolean).join(" ");
}

function topicSearchText(topic) {
  if (topic.searchTermsOnly) return topic.terms;
  return [
    topic.title,
    topic.summary,
    topic.terms,
    ...(topic.body || []).map(blockSearchText),
  ].filter(Boolean).join(" ").toLowerCase();
}

const GUIDE_TOPIC_SEARCH = new Map(
  GUIDE_TOPICS.map((topic) => [topic.id, topicSearchText(topic)]),
);


function groupGuideTopics(topics) {
  const assigned = new Set();
  const groups = GUIDE_GROUPS.map((group) => {
    const matching = group.topicIds.map((id) => topics.find((topic) => topic.id === id)).filter(Boolean);
    matching.forEach((topic) => assigned.add(topic.id));
    const fallback = group.id === "operations" && !matching.some((topic) => topic.id === "usage")
      ? "Inspect requests, backups, model data, diagnostics, and maintenance when you need them."
      : group.id === "voice-speech" && !matching.some((topic) => ["voice", "speech-processing"].includes(topic.id))
        ? "Control voice identity, spoken-output cleanup, Quiet Hours, and how voice devices behave."
        : null;
    return {...group, description: fallback || group.description, topics: matching, fallback: Boolean(fallback)};
  }).filter((group) => group.topics.length);
  // Search historically appended a destination group when none of its original
  // topics matched. Keep that order and copy, but decide it before rendering.
  groups.sort((a, b) => Number(a.fallback) - Number(b.fallback)
    || (a.fallback && b.fallback ? Number(a.id === "voice-speech") - Number(b.id === "voice-speech") : 0));
  const remaining = topics.filter((topic) => !assigned.has(topic.id));
  if (remaining.length) {
    groups.push({
      id: "more-features",
      title: "More features",
      description: "Other settings and features available in Extended OpenAI.",
      topics: remaining,
    });
  }
  return groups;
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

function renderGuideTopic(panel, topic) {
  return `<details class="content-card guide-topic eoc-details-base" id="guide-${topic.id}" ${panel._guideTopic === topic.id ? "open" : ""}><summary><span><strong>${panel._e(topic.title)}</strong><small>${panel._e(topic.summary)}</small></span></summary><div class="guide-topic-body">${(topic.body?.length ? topic.body : [{type:"p", text:topic.summary}]).map((block) => renderGuideBlock(panel, block)).join("")}<button type="button" class="secondary guide-action" data-page="${topic.action.page}" data-subsection="${topic.action.section}">${panel._e(topic.action.label)}</button></div></details>`;
}

function openGuideTopic(panel, topicId) {
  const root = panel.shadowRoot;
  const target = root.querySelector(`#guide-${CSS.escape(topicId)}`);
  if (!target || target.hidden) return;
  const previous = panel._openGuideTopicElement;
  if (previous && previous !== target) previous.open = false;
  target.open = true;
  panel._openGuideTopicElement = target;
  panel._guideTopic = topicId;
  target.scrollIntoView({behavior: "smooth", block: "start"});
}

function applyGuideSearch(panel, value = "") {
  const root = panel.shadowRoot;
  const host = root.querySelector(".guide-groups");
  const noResults = root.querySelector(".guide-no-results");
  if (!host || !noResults) return;
  const query = String(value || "").trim().toLowerCase();
  const topics = GUIDE_TOPICS.filter((topic) =>
    !query || GUIDE_TOPIC_SEARCH.get(topic.id)?.includes(query)
  );
  const matchingIds = new Set(topics.map((topic) => topic.id));
  for (const topic of GUIDE_TOPICS) {
    const node = root.querySelector(`#guide-${CSS.escape(topic.id)}`);
    if (node) node.hidden = !matchingIds.has(topic.id);
  }

  const groups = groupGuideTopics(topics);
  const visibleGroups = new Set(groups.map((group) => group.id));
  root.querySelectorAll(".guide-group").forEach((group) => {
    group.hidden = !visibleGroups.has(group.dataset.guideGroup);
  });
  for (const group of groups) {
    const node = root.querySelector(`[data-guide-group="${CSS.escape(group.id)}"]`);
    if (!node) continue;
    const description = node.querySelector(".guide-group-heading p");
    if (description) description.textContent = group.description;
    host.insertBefore(node, noResults);
  }
  noResults.hidden = topics.length > 0;
}

export function renderGuide(panel) {
  const groups = groupGuideTopics(GUIDE_TOPICS);

  return `    <section class="page-intro"><h1>Guide</h1><p>New to Extended OpenAI, or unsure which feature you need? Start here. This Guide explains the main features in plain language and links directly to the relevant settings.</p></section>
    <section class="content-card guide-quick-start">
      <h2>Common things you may want to do</h2>
      <p>Choose a goal to jump straight to the explanation.</p>
      <div class="guide-quick-tasks">${GUIDE_QUICK_TASKS.map((item) => `<button type="button" class="guide-quick-card" data-guide-topic="${panel._e(item.topic)}"><span class="guide-quick-icon" aria-hidden="true"><ha-icon icon="${panel._e(item.icon)}"></ha-icon></span><strong>${panel._e(item.title)}</strong><small>${panel._e(item.text)}</small></button>`).join("")}</div>
    </section>
    <label class="guide-search"><span class="sr-only">Search Guide topics</span><input id="guide-search" type="search" value="${panel._e(panel._guideQuery || "")}" placeholder="Search the Guide" aria-label="Search Guide topics"></label>
    <section class="guide-groups" aria-live="polite">${groups.map((group) => `<section class="guide-group" data-guide-group="${panel._e(group.id)}"><div class="guide-group-heading"><h2>${panel._e(group.title)}</h2><p>${panel._e(group.description)}</p></div><div class="guide-topics">${group.topics.map((topic) => renderGuideTopic(panel, topic)).join("")}</div></section>`).join("")}<p class="empty guide-no-results" hidden>No Guide topics match your search.</p></section>
    <section class="content-card"><div class="section-heading"><div><h2>How the memory and history features differ</h2><p>These features can look similar at first. Use the smallest kind of retained data that fits what you want the assistant to remember or retrieve.</p></div></div><div class="comparison-table"><table><thead><tr><th>Feature</th><th>Best used for</th><th>How long it lasts</th><th>When the model uses it</th></tr></thead><tbody>${MEMORY_COMPARISON.map((row) => `<tr>${row.map((cell) => `<td>${panel._e(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;
}

export function bindGuide(panel) {
  const root = panel.shadowRoot;
  const search = root.querySelector("#guide-search");
  search?.addEventListener("input", (event) => {
    panel._guideQuery = event.target.value;
    applyGuideSearch(panel, panel._guideQuery);
  });

  panel._openGuideTopicElement = root.querySelector(".guide-topic[open]") || null;
  root.querySelectorAll(".guide-topic").forEach((topic) => {
    topic.addEventListener("toggle", () => {
      const topicId = topic.id.replace(/^guide-/, "");
      if (topic.open) {
        const previous = panel._openGuideTopicElement;
        panel._openGuideTopicElement = topic;
        panel._guideTopic = topicId;
        if (previous && previous !== topic) previous.open = false;
      } else if (panel._openGuideTopicElement === topic) {
        panel._openGuideTopicElement = null;
        if (panel._guideTopic === topicId) panel._guideTopic = null;
      }
    });
  });

  root.querySelectorAll("[data-guide-topic]").forEach((button) => {
    button.addEventListener("click", () => openGuideTopic(panel, button.dataset.guideTopic));
  });

  root.querySelectorAll(".guide-action").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
  applyGuideSearch(panel, panel._guideQuery);
}
