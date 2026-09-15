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
    topicIds: new Set(["getting-started", "models", "model-data"]),
  },
  {
    id: "conversation-memory",
    title: "Conversation & memory",
    description: "Choose how recent discussions, saved facts, history, and reference material are retained and reused.",
    topicIds: new Set(["continuity", "context-management", "memory", "persistent-memory", "temporary-memory", "archive", "knowledge"]),
  },
  {
    id: "capabilities",
    title: "Capabilities & Home Assistant",
    description: "Control what the assistant can do, what stays local, and how extra capabilities are exposed.",
    topicIds: new Set(["home-assistant", "request-rules", "local-handling", "broadcast", "functions", "quiet-hours"]),
  },
  {
    id: "voice-speech",
    title: "Voice & speech",
    description: "Control voice identity, spoken-output cleanup, and how voice devices behave.",
    topicIds: new Set(["voice", "speech-processing"]),
  },
  {
    id: "privacy-access",
    title: "Privacy & access",
    description: "Understand data boundaries and restrict what visitors or signed-in users can access.",
    topicIds: new Set(["guest-mode", "privacy"]),
  },
  {
    id: "operations",
    title: "Operations & troubleshooting",
    description: "Understand diagnostics, request debugging, backups, model data, and maintenance behaviour.",
    topicIds: new Set(["usage"]),
  },
];

const LOCAL_HANDLING_GUIDE_TOPIC = {
  id: "local-handling",
  title: "Local handling: use Home Assistant before AI",
  summary: "Let Home Assistant handle simple built-in commands without an AI request, while anything it cannot handle continues to Extended OpenAI normally.",
  terms: "local handling home assistant intents built in commands no ai request prefer local handling delayed command timer request rules exceptions",
  body: [
    {
      type: "p",
      text: "Local handling is an optional shortcut for commands that Home Assistant already understands on its own. It can make simple requests faster and avoid an unnecessary AI request, without replacing the AI for more flexible or complicated language."
    },
    { type: "heading", text: "What happens when you speak" },
    {
      type: "steps",
      items: [
        "Request Rules get the first chance to handle or route the request.",
        "If no Request Rule matched, Extended OpenAI can ask Home Assistant whether the request is one of its built-in commands.",
        "If Home Assistant has a clear local match, it handles the command and returns its normal response without calling the AI provider.",
        "If Home Assistant does not have a suitable match, the request continues through Extended OpenAI and the AI model as normal."
      ]
    },
    {
      type: "p",
      text: "Typical local matches include straightforward requests such as turning devices on or off, checking a device state, asking for the current time or date, and managing timers. The exact list comes from the Home Assistant version you are running, so newly added Home Assistant command types can appear automatically."
    },
    { type: "heading", text: "Choose which commands should still use AI" },
    {
      type: "p",
      text: "The Local handling settings show the command types Home Assistant currently provides. Select any command type under Always send these command types to AI if you want that kind of request to skip the local shortcut. The friendly name is shown first; the technical Hass... name is included only as a reference."
    },
    {
      type: "note",
      title: "Delayed device commands are a special case",
      text: "Home Assistant uses its timer command for both ordinary timers and commands such as “turn off the lights in 20 minutes”. The delayed-device option lets those future device actions continue to your AI or Function Tool path while normal requests such as “set a 20 minute timer” can still be handled locally."
    },
    { type: "heading", text: "Check your Assist pipeline too" },
    {
      type: "p",
      text: "Home Assistant Assist has its own Prefer local handling option. If that is enabled on a pipeline using this agent, Home Assistant may complete a command before it ever reaches Extended OpenAI. The settings page warns you about affected pipelines. Turn Home Assistant's pipeline option off if you want Extended OpenAI to control the order and apply its command-type exceptions."
    },
    {
      type: "note",
      title: "Guest Mode keeps its existing safeguards",
      text: "When Guest Mode is active, this shortcut is deliberately skipped. Guest requests continue through the existing Extended OpenAI policy checks instead of bypassing them through Home Assistant's general local intent path."
    },
    {
      type: "p",
      text: "Leave local handling off if you prefer every non-Request-Rule command to follow the normal Extended OpenAI path. Turning it on does not stop you using AI for anything Home Assistant cannot match locally."
    }
  ],
  action: { label: "Configure local handling", page: "assistant", section: "conversation" }
};

const BROADCAST_GUIDE_TOPIC = {
  id: "broadcast",
  title: "Broadcast spoken messages around the home",
  summary: "Broadcast sends one-way spoken messages to selected Assist satellites or the whole home, with busy satellites queued until they are free.",
  terms: "broadcast announce tell message satellite room area floor label whole home speaker queue alias one way",
  body: [
    {
      type: "p",
      text: "Broadcast is an optional one-way messaging feature. Enable it from the Extended OpenAI Overview, then you can type a message there or send one through voice, a Function Tool, or the Home Assistant Broadcast action."
    },
    { type: "heading", text: "Choose where the message goes" },
    {
      type: "list",
      items: [
        "Select one or more Assist satellites directly from the Overview.",
        "Use an Assist satellite, device, area, floor, or label name in a voice or AI request.",
        "Choose Whole home to send to every announcement-capable Assist satellite except the satellite that originated the request."
      ]
    },
    {
      type: "p",
      text: "Local destination matching also checks Home Assistant aliases when they are available. For example, an area called Kitchen can also be addressed by an alias you have given that area."
    },
    {
      type: "note",
      title: "Broadcast destinations do not need to be exposed to Assist",
      text: "The Assist Satellite entities themselves do not need to be exposed to Assist. Extended OpenAI discovers announcement-capable Assist satellites directly from Home Assistant and sends the announcement to them itself."
    },
    { type: "heading", text: "Voice phrases that can stay local" },
    {
      type: "p",
      text: "When Extended OpenAI local handling is enabled, clear targeted wording can be resolved without an AI request. Examples include “Broadcast to kitchen that dinner is ready”, “Tell bedroom I'll be up in five”, and “Announce to upstairs saying the dog needs to go out”."
    },
    {
      type: "p",
      text: "If wording clearly looks like a targeted Broadcast request but the destination cannot be resolved, Extended OpenAI prevents Home Assistant's broad whole-home Broadcast intent from swallowing the request. The request can then continue to the normal AI path instead of accidentally announcing the destination words everywhere."
    },
    { type: "heading", text: "Busy satellites are not deliberately interrupted" },
    {
      type: "p",
      text: "If a destination satellite is listening, processing, or speaking, the message waits in that satellite's queue. Each destination is handled independently, so an idle room can receive the message while another room waits. Queued messages expire after a bounded wait rather than remaining pending forever."
    },
    {
      type: "note",
      title: "One-way by design",
      text: "This release does not add reply threads, a live two-way conversation, or recorded-voice Broadcast. Those are deliberately outside the current feature."
    }
  ],
  action: { label: "Open Broadcast", page: "overview", section: "" }
};

const MODEL_DATA_GUIDE_TOPIC = {
  id: "model-data",
  title: "Model data and capability updates",
  summary: "Model data tells Extended OpenAI which response controls and API features a model supports, and it can be refreshed separately from integration releases.",
  terms: "model data catalog catalogue capabilities refresh update bundled fallback reasoning temperature service tier",
  body: [
    {
      type: "p",
      text: "Extended OpenAI keeps model capability information separately from your agent configuration. This lets the management UI show controls such as reasoning effort, temperature, service tier, and compatible API modes only where they are expected to work."
    },
    {
      type: "p",
      text: "You can refresh the model data without updating or reloading the integration. Loaded agents use refreshed capability data immediately. If the remote update is unavailable or unsuitable, you can return to the model data bundled with the installed integration."
    },
    {
      type: "note",
      title: "Model data is guidance, not a provider guarantee",
      text: "Custom or OpenAI-compatible providers may implement a model differently from the provider the catalogue describes. If a provider rejects an option, use the settings that provider actually supports."
    }
  ],
  action: { label: "Open diagnostics", page: "usage-maintenance", section: "diagnostics" }
};

const CONTEXT_MANAGEMENT_GUIDE_TOPIC = {
  id: "context-management",
  title: "Context management for long conversations",
  summary: "Context management reduces old conversation history when a conversation grows beyond the configured input-token threshold.",
  terms: "context management trim history threshold keep recent clear summarize summary long conversation tokens",
  body: [
    {
      type: "p",
      text: "Long conversations can eventually become too large or expensive to keep sending in full. The Trim history after setting defines the input-token threshold at which Extended OpenAI reduces older conversation history."
    },
    {
      type: "list",
      items: [
        "Keep recent messages removes the oldest complete user turns while preserving the newest useful context and complete tool call/result units.",
        "Clear all messages removes the retained conversation history when the threshold is reached.",
        "Summarize older messages makes one bounded model request after the current reply to condense older context. The next turn waits for that summary if it is still pending, and a failed summary falls back to keeping recent messages."
      ]
    },
    {
      type: "note",
      title: "This does not delete saved memory",
      text: "Context trimming changes the live conversation history sent to the model. It does not delete Persistent Memory, Temporary Memory, Knowledge Library sources, or archived conversations."
    }
  ],
  action: { label: "Configure context management", page: "assistant", section: "conversation" }
};

const QUIET_HOURS_GUIDE_TOPIC = {
  id: "quiet-hours",
  title: "Quiet Hours for Assist satellites",
  summary: "Quiet Hours can lower Assist satellite speaker volume and optionally suppress wake-word sounds during a daily time window.",
  terms: "quiet hours night satellite speaker volume wake word sound chime schedule automation binary sensor",
  body: [
    {
      type: "p",
      text: "Quiet Hours is a global daily schedule for Assist satellites. During the configured period, it can cap each satellite's speaker volume and, where a compatible switch is available, turn the wake-word chime off or on."
    },
    {
      type: "list",
      items: [
        "Quiet Hours only turns a louder speaker down to the configured maximum; it does not raise a quieter speaker.",
        "Extended OpenAI tries to discover satellite speaker and wake-word entities automatically, with manual overrides available when discovery is wrong or incomplete.",
        "A read-only Quiet Hours entity stays on for the scheduled period, so normal Home Assistant automations can use the same schedule for LEDs or other device-specific behaviour."
      ]
    },
    {
      type: "note",
      title: "Your own changes are preserved",
      text: "Extended OpenAI remembers the volume it changed and restores it only when Quiet Hours still owns that value. If you or another automation changes the speaker while Quiet Hours is active, that newer value is left alone."
    },
    {
      type: "p",
      text: "If Home Assistant starts or restarts while the current time is already inside the saved Quiet Hours period, the schedule catches up and applies the active policy. The enable_quiet_hours and disable_quiet_hours actions can also turn the saved schedule on or off."
    }
  ],
  action: { label: "Configure Quiet Hours", page: "capabilities", section: "quiet-hours" }
};

const SPEECH_PROCESSING_GUIDE_TOPIC = {
  id: "speech-processing",
  title: "Speech cleanup for TTS",
  summary: "Speech processing removes text that is useful on screen but awkward when spoken, while keeping the original assistant response for logs and conversation history.",
  terms: "speech tts markdown urls links citations formatting regex replacements progressive streaming cleanup spoken output",
  body: [
    {
      type: "p",
      text: "Speech processing creates a speech-safe version of the assistant response for TTS. It can remove Markdown links, formatting, citations and bare URLs so they are not read aloud, while the original provider response remains available to ChatLog, conversation history and archives."
    },
    {
      type: "p",
      text: "The built-in cleanup can work progressively while a response is streaming, including when Markdown or URLs are split across provider chunks. This allows spoken output to start before the full answer has finished."
    },
    {
      type: "note",
      title: "Custom replacements wait for the completed response",
      text: "Custom regular-expression replacements run only after the response is complete, so enabling any custom replacement disables progressive TTS for that response. Replacement rules are bounded and isolated; if one fails or times out, speech falls back atomically to the text from before custom replacements."
    }
  ],
  action: { label: "Configure speech", page: "assistant", section: "speech" }
};

const GUIDE_TOPICS_WITH_EXTENSIONS = (() => {
  const topics = [...GUIDE_TOPICS];
  const modelsIndex = topics.findIndex((topic) => topic.id === "models");
  topics.splice(modelsIndex >= 0 ? modelsIndex + 1 : 0, 0, MODEL_DATA_GUIDE_TOPIC);

  const continuityIndex = topics.findIndex((topic) => topic.id === "continuity");
  topics.splice(continuityIndex >= 0 ? continuityIndex + 1 : 0, 0, CONTEXT_MANAGEMENT_GUIDE_TOPIC);

  const requestRulesIndex = topics.findIndex((topic) => topic.id === "request-rules");
  const localIndex = requestRulesIndex >= 0 ? requestRulesIndex + 1 : 0;
  topics.splice(localIndex, 0, LOCAL_HANDLING_GUIDE_TOPIC);
  topics.splice(localIndex + 1, 0, BROADCAST_GUIDE_TOPIC);

  const functionsIndex = topics.findIndex((topic) => topic.id === "functions");
  topics.splice(functionsIndex >= 0 ? functionsIndex + 1 : topics.length, 0, QUIET_HOURS_GUIDE_TOPIC);

  const voiceIndex = topics.findIndex((topic) => topic.id === "voice");
  topics.splice(voiceIndex >= 0 ? voiceIndex + 1 : topics.length, 0, SPEECH_PROCESSING_GUIDE_TOPIC);
  return topics;
})();

const GUIDE_TEXT_REWRITES = new Map([
  ["Lightweight lexical retrieval matches words and related text locally. Hybrid semantic retrieval can also use embeddings to find conceptually similar memories even when the wording differs. If embeddings are unavailable, retrieval falls back to the local lexical method.", "By default, the integration finds relevant memories by matching words and phrases locally. An optional semantic mode can also find memories with a similar meaning even when different words are used. That semantic mode uses embeddings when your provider supports them; if it is unavailable, memory retrieval falls back to the local matching method."],
  ["Create a Function Tool when you want to expose an additional capability or a specially defined action to the model. Function Tools are configured as YAML and validated by the backend before they are saved. Disabled tools remain configured but are not sent to the model and cannot run.", "Create a Function Tool when you want to give the assistant an additional capability or a specially defined action. Function Tools are configured as YAML and checked for errors before they are saved. Disabled tools remain configured but are not sent to the model and cannot run."],
  ["A group set to Load when needed initially sends only a small catalogue entry. The model loads the group's full function definitions only if the task needs them.", "With Load when needed, the model initially sees only the group's name and description. The full function definitions are sent only if the model decides it needs that group, which can reduce repeated input-token use."],
  ["For integration-managed continuity, the conversation timeout decides how much inactivity is allowed before the next request starts fresh. A short timeout reduces accidental carry-over; a longer timeout makes conversations easier to resume later.", "If Extended OpenAI is remembering a conversation beyond the immediate Home Assistant session, the conversation timeout decides how much inactivity is allowed before the next request starts fresh. A short timeout reduces accidental carry-over; a longer timeout makes conversations easier to resume later."],
  ["Preview effective request is useful for inspecting the locally assembled baseline for a new request, including prompts, Home Assistant context and tool definitions. It cannot show every piece of provider-side framing or context that depends on a future real user message.", "Preview effective request shows the main prompt, Home Assistant context and tool definitions that Extended OpenAI is preparing for a new request. Some details only exist when a real message is sent, and the provider may also handle parts of the request that the preview cannot show."],
  ["Check which Home Assistant entities are exposed to Assist. These are the normal Home Assistant devices, sensors and other entities that the assistant can see or control.", "Check which Home Assistant entities are exposed to Assist. Exposure determines which normal Home Assistant entities are available to the assistant, but control also requires permission from the Home Assistant user making the request and can be restricted further by Extended OpenAI policies such as Guest Mode."],
  ["Home Assistant exposure decides which normal Home Assistant entities are available to Assist at all.", "Home Assistant exposure decides which normal Home Assistant entities are available to Assist. It does not grant control by itself: model-driven actions also use the requesting Home Assistant user's permissions, with Extended OpenAI policies able to restrict access further."],
]);

function rewriteGuideText(text) {
  return GUIDE_TEXT_REWRITES.get(text) || text;
}

function enhanceGuideTopics(topics) {
  return topics.map((topic) => {
    const body = (topic.body || []).map((block) => {
      if (!block || typeof block !== "object") return block;
      const next = {...block};
      if (typeof next.text === "string") next.text = rewriteGuideText(next.text);
      if (Array.isArray(next.items)) next.items = next.items.map(rewriteGuideText);
      return next;
    });
    if (topic.id === "functions") {
      body.push(
        {type: "heading", text: "Delayed Function Tool execution"},
        {type: "p", text: "Configured Function Tools can request delayed execution for future device actions. Pending delayed calls are persisted across Home Assistant restarts. When a call becomes due, Extended OpenAI rechecks the current configuration, requesting user's Home Assistant permissions, and applicable authorization policy before execution. A call that had already crossed the execution boundary before an unexpected process stop is not replayed automatically."}
      );
    }
    if (topic.id === "voice") {
      body.push({
        type: "note",
        title: "How a voice device is identified",
        text: "When Home Assistant supplies a device-registry device ID, Extended OpenAI uses that stable device identity for voice mapping. Satellite metadata is retained and can be used as a fallback when no registry device ID is available. The resulting mapping chooses the retained-data scope; it does not bypass Home Assistant permissions."
      });
    }
    if (topic.id === "usage") {
      body.push(
        {type: "heading", text: "Request debugging and backups"},
        {type: "p", text: "Request debugging can capture complete recent provider requests when you need to inspect exactly what was sent. Full Backup & Restore includes normalized agent configuration, Request Rules, Persistent Memory, active Temporary Memory with expiry, Knowledge sources, archived conversations, Guest Mode schedule and usage data. Provider credentials, in-flight conversations, loaded Function Groups and transient caches are deliberately excluded."},
        {type: "note", title: "Restore is validated before durable state is replaced", text: "A restore validates the backup and applies the durable agent state as one maintenance operation. If persistence fails during the restore, Extended OpenAI attempts to roll back rather than intentionally leaving only part of the backup applied."}
      );
    }
    if (topic.id === "privacy") {
      const firstSteps = body.findIndex((block) => block?.type === "steps");
      if (firstSteps >= 0) body.splice(firstSteps, 0, {type: "heading", text: "What the assistant can access"});
      const storedNote = body.findIndex((block) => block?.type === "note" && block.title === "Stored locally does not always mean never sent to the model");
      if (storedNote >= 0) body.splice(storedNote, 0, {type: "heading", text: "What can be stored or sent"});
      body.push({
        type: "note",
        title: "The most restrictive applicable policy wins",
        text: "Assist exposure, Home Assistant user permissions, Guest Mode, Request Rules and Function Tool policy are separate layers. Being exposed or visible does not override a user's lack of CONTROL permission, and an Extended OpenAI restriction cannot be bypassed by another more permissive layer."
      });
    }
    return {...topic, body};
  });
}

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

function groupGuideTopics(topics) {
  const assigned = new Set();
  const groups = GUIDE_GROUPS.map((group) => {
    const matching = topics.filter((topic) => group.topicIds.has(topic.id));
    matching.forEach((topic) => assigned.add(topic.id));
    return {...group, topics: matching};
  }).filter((group) => group.topics.length);
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
  return `<details class="content-card guide-topic" id="guide-${topic.id}" ${panel._guideTopic === topic.id ? "open" : ""}><summary><span><strong>${panel._e(topic.title)}</strong><small>${panel._e(topic.summary)}</small></span></summary><div class="guide-topic-body">${(topic.body?.length ? topic.body : [{type:"p", text:topic.summary}]).map((block) => renderGuideBlock(panel, block)).join("")}<button type="button" class="secondary guide-action" data-page="${topic.action.page}" data-subsection="${topic.action.section}">${panel._e(topic.action.label)}</button></div></details>`;
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
  const topics = enhanceGuideTopics(GUIDE_TOPICS_WITH_EXTENSIONS).filter((topic) => !query || topicSearchText(topic).includes(query));
  const groups = groupGuideTopics(topics);

  return `<style>
      .guide-quick-start{display:grid;gap:12px;background:color-mix(in srgb,var(--secondary-background-color) 24%,var(--card-background-color))}
      .guide-quick-start h2{margin:0;font-size:19px}
      .guide-quick-start>p{margin:0;color:var(--secondary-text-color);line-height:1.5}
      .guide-quick-tasks{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:4px}
      .guide-quick-card{display:grid;grid-template-columns:auto 1fr;align-content:start;gap:3px 11px;min-height:0;padding:15px 16px;text-align:left;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid color-mix(in srgb,var(--divider-color) 72%,var(--secondary-text-color));border-radius:10px}
      .guide-quick-card:hover{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 4%,var(--card-background-color))}
      .guide-quick-icon{grid-row:1/3;display:grid;place-items:center;width:34px;height:34px;border-radius:9px;background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color));color:var(--primary-color)}
      .guide-quick-icon ha-icon{--mdc-icon-size:20px}
      .guide-quick-card strong{font-size:14px}
      .guide-quick-card small{font-weight:400;line-height:1.45;color:var(--secondary-text-color)}
      .guide-groups{display:grid;gap:28px}
      .guide-group{display:grid;gap:10px}
      .guide-group-heading{padding:0 4px 10px;border-bottom:1px solid var(--divider-color)}
      .guide-group-heading h2{margin:0;font-size:18px}
      .guide-group-heading p{margin:5px 0 0;color:var(--secondary-text-color);line-height:1.5}
      .guide-topics{display:grid!important;grid-template-columns:1fr!important;gap:9px}
      .guide-topic{margin:0!important;padding:0 22px!important;border:1px solid color-mix(in srgb,var(--divider-color) 76%,var(--secondary-text-color))!important;background:var(--card-background-color);scroll-margin-top:18px;box-shadow:0 1px 2px rgba(0,0,0,.035)}
      .guide-topic[open]{border-color:color-mix(in srgb,var(--primary-color) 30%,var(--divider-color))!important}
      .guide-topic summary{padding:17px 0!important}
      .guide-topic summary>span{display:grid;gap:5px}
      .guide-topic summary strong{font-size:15px}
      .guide-topic summary small{font-size:13px;font-weight:400;line-height:1.45}
      .guide-topic-body{max-width:920px;padding:0 0 22px;font-size:14px;line-height:1.65}
      .guide-topic-body>p{margin:10px 0}
      .guide-topic-body h3{margin:22px 0 8px;font-size:16px}
      .guide-topic-body ul,.guide-topic-body ol{margin:10px 0;padding-left:26px}
      .guide-topic-body li{margin:6px 0}
      .guide-topic-body .notice{margin:16px 0}
      .guide-topic-body .guide-action{margin-top:10px}
      @media(max-width:950px){.guide-quick-tasks{grid-template-columns:repeat(2,minmax(0,1fr))}}
      @media(max-width:600px){.guide-quick-tasks{grid-template-columns:1fr}.guide-topic{padding:0 17px!important}.guide-group-heading{padding-inline:1px}}
    </style>
    <section class="page-intro"><h1>Guide</h1><p>New to Extended OpenAI, or unsure which feature you need? Start here. This Guide explains the main features in plain language and links directly to the relevant settings.</p></section>
    <section class="content-card guide-quick-start">
      <h2>Common things you may want to do</h2>
      <p>Choose a goal to jump straight to the explanation.</p>
      <div class="guide-quick-tasks">${GUIDE_QUICK_TASKS.map((item) => `<button type="button" class="guide-quick-card" data-guide-topic="${panel._e(item.topic)}"><span class="guide-quick-icon" aria-hidden="true"><ha-icon icon="${panel._e(item.icon)}"></ha-icon></span><strong>${panel._e(item.title)}</strong><small>${panel._e(item.text)}</small></button>`).join("")}</div>
    </section>
    <label class="guide-search"><span class="sr-only">Search Guide topics</span><input id="guide-search" type="search" value="${panel._e(panel._guideQuery || "")}" placeholder="Search the Guide" aria-label="Search Guide topics"></label>
    <section class="guide-groups" aria-live="polite">${groups.map((group) => `<section class="guide-group" data-guide-group="${panel._e(group.id)}"><div class="guide-group-heading"><h2>${panel._e(group.title)}</h2><p>${panel._e(group.description)}</p></div><div class="guide-topics">${group.topics.map((topic) => renderGuideTopic(panel, topic)).join("")}</div></section>`).join("") || panel._empty("No Guide topics match your search.")}</section>
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
