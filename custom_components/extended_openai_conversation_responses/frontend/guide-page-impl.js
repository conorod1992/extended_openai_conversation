import { renderGuide as renderBaseGuide } from "./guide-page-base.js";

export * from "./guide-page-base.js";

function ensureGuideGroup(html, groupId, title, description) {
  if (html.includes(`data-guide-group="${groupId}"`)) return html;
  const marker = '<section class="content-card"><div class="section-heading"><div><h2>How the memory and history features differ</h2>';
  const group = `<section class="guide-group" data-guide-group="${groupId}"><div class="guide-group-heading"><h2>${title}</h2><p>${description}</p></div><div class="guide-topics"></div></section>`;
  return html.replace(marker, `${group}${marker}`);
}

function extractGuideTopic(html, topicId) {
  const pattern = new RegExp(`<details class="content-card guide-topic" id="guide-${topicId}"[\\s\\S]*?<\\/details>`);
  const match = html.match(pattern);
  if (!match) return {html, topic: ""};
  return {html: html.replace(match[0], ""), topic: match[0]};
}

function insertGuideTopic(html, groupId, topic) {
  if (!topic) return html;
  const start = html.indexOf(`<section class="guide-group" data-guide-group="${groupId}">`);
  if (start < 0) return html;
  const topicsStart = html.indexOf('<div class="guide-topics">', start);
  if (topicsStart < 0) return html;
  const insertAt = topicsStart + '<div class="guide-topics">'.length;
  return `${html.slice(0, insertAt)}${topic}${html.slice(insertAt)}`;
}

function removeEmptyGuideGroups(html) {
  return html.replace(/<section class="guide-group" data-guide-group="[^"]+"><div class="guide-group-heading">[\s\S]*?<\/div><div class="guide-topics"><\/div><\/section>/g, "");
}

function extraGuideTopic(panel, {id, title, summary, body, action}) {
  const blocks = body.map((block) => {
    if (block.type === "heading") return `<h3>${panel._e(block.text)}</h3>`;
    if (block.type === "note") return `<div class="notice"><strong>${panel._e(block.title)}</strong><p>${panel._e(block.text)}</p></div>`;
    return `<p>${panel._e(block.text)}</p>`;
  }).join("");
  return `<details class="content-card guide-topic" id="guide-${id}" ${panel._guideTopic === id ? "open" : ""}><summary><span><strong>${panel._e(title)}</strong><small>${panel._e(summary)}</small></span></summary><div class="guide-topic-body">${blocks}<button type="button" class="secondary guide-action" data-page="${action.page}" data-subsection="${action.section}">${panel._e(action.label)}</button></div></details>`;
}

function matchesGuideQuery(panel, text) {
  const query = String(panel._guideQuery || "").trim().toLowerCase();
  return !query || text.toLowerCase().includes(query);
}

export function renderGuide(panel) {
  let html = renderBaseGuide(panel)
    .replaceAll("Local handling: use Home Assistant before AI", "Local handling: Home Assistant before AI")
    .replaceAll(
      "Let Home Assistant handle simple built-in commands without an AI request, while anything it cannot handle continues to Extended OpenAI normally.",
      "Use Home Assistant's fast built-in commands after Request Rules, while keeping selected command types available to Function Tools or AI."
    )
    .replaceAll("Choose which commands should still use AI", "Choose what should continue to AI")
    .replaceAll("Always send these command types to AI", "Send these command types to AI")
    .replaceAll(
      "The Local handling settings show the command types Home Assistant currently provides. Select any command type under Send these command types to AI if you want that kind of request to skip the local shortcut. The friendly name is shown first; the technical Hass... name is included only as a reference.",
      "The Local handling settings show the command types Home Assistant currently provides. Select any item under Send these command types to AI if you want that kind of request to skip the local shortcut. Delayed device commands are included in this same list. Friendly names are shown first; the technical Hass... name is included only as a reference."
    )
    .replaceAll("Delayed device commands are a special case", "Delayed device commands stay in the same list")
    .replaceAll(
      "Home Assistant uses its timer command for both ordinary timers and commands such as “turn off the lights in 20 minutes”. The delayed-device option lets those future device actions continue to your AI or Function Tool path while normal requests such as “set a 20 minute timer” can still be handled locally.",
      "Home Assistant uses its timer command for both ordinary timers and commands such as “turn off the lights in 20 minutes”. Choose Delayed device commands in the same exception list to send those future device actions to your AI or Function Tool path while normal requests such as “set a 20 minute timer” can still be handled locally."
    )
    .replaceAll(
      "Home Assistant Assist has its own Prefer local handling option. If that is enabled on a pipeline using this agent, Home Assistant may complete a command before it ever reaches Extended OpenAI. The settings page warns you about affected pipelines. Turn Home Assistant's pipeline option off if you want Extended OpenAI to control the order and apply its command-type exceptions.",
      "Home Assistant Assist has its own Prefer local handling option, which runs before the request reaches Extended OpenAI. That is simple and fast, but Extended OpenAI cannot then apply Request Rules or choose a Function Tool for that command. Extended OpenAI local handling runs after Request Rules instead, so you can keep simple commands local while making exceptions. For example, a normal light command can stay local while a delayed light command goes to a deferred-action Function Tool. Turn Home Assistant's pipeline option off if you want Extended OpenAI to control this order."
    )
    .replaceAll(
      'data-page="assistant" data-subsection="conversation">Configure local handling',
      'data-page="capabilities" data-subsection="home-assistant">Configure local handling'
    )
    .replaceAll(
      "Starts with, Ends with and Contains are broader. They can be useful when your trigger phrase may appear as part of a longer request.",
      "Starts with, Ends with and Contains are broader. They can be useful when your trigger phrase may appear as part of a longer request. For AI-routing rules these matches only choose the route: the entire original request is still sent unchanged, and the matched words are not stripped."
    )
    .replaceAll(
      "Home Assistant sentence patterns are for more flexible command shapes. They use Home Assistant's Hassil sentence format: square brackets such as [please] mean optional words, brackets such as (on|off) mean one of several choices, and slots such as {room} can capture part of the sentence.",
      "Extended OpenAI sentence patterns provide bounded matching for flexible command shapes: [please] means optional words, (on|off) gives alternatives, {room} captures free text, {room=kitchen|bedroom} restricts a capture to listed choices, and {level=0..100} captures an integer in a range. This is Extended OpenAI's own pattern syntax, not a promise of Home Assistant/Hassil compatibility. For AI routing, an Equals or sentence-pattern match is a complete routing command: Extended OpenAI acknowledges it locally and applies the selected route to the rest of the current conversation instead of forwarding that command as a normal prompt."
    )
    .replaceAll(
      "Named Hassil expansions written like <expansion> are not supported here because Request Rules do not have their own expansion catalogue.",
      "Angle-bracket named expansions such as <expansion> are not supported. Request Rules use the Extended OpenAI sentence-pattern syntax described here rather than the full Home Assistant/Hassil grammar."
    )
    .replaceAll(
      "Word forms and editable wording alternatives let Extended OpenAI accept small, predictable wording differences. Fuzzy matching is a final fallback that can accept a slightly imperfect match, but it is only tried if no stricter rule matched first.",
      "Word forms and editable wording alternatives let Extended OpenAI accept small, predictable wording differences. Enabled deterministic rules are evaluated from top to bottom in the order shown, and the first deterministic rule that matches wins. Move rules to change that priority. Fuzzy matching is considered only if no deterministic rule matches."
    )
    .replaceAll(
      "Every Function Tool needs instructions that explain it to the model. If you have many tools, sending all of those instructions with every request can use unnecessary input tokens. Function Groups let you decide which tool instructions are always sent and which are loaded only when needed.",
      "Every Function Tool needs instructions that explain it to the model. If you have many tools, sending all of those instructions with every request can use unnecessary input tokens. Function Groups let you decide which tool instructions are always sent and which are loaded only when needed. Each group can also be disabled independently: its member Function Tool settings are kept, but none of those tools are available to the model until the group is enabled again."
    )
    .replaceAll(
      "Deleting a Function Group does not delete the functions inside it. Those functions simply return to the normal always-available collection unless you place them in another group.",
      "Disabling a Function Group does not disable its member Function Tools individually. Re-enabling the group restores only the members that are individually enabled. Deleting a Function Group is different: it does not delete the functions, but they become ungrouped and therefore return to the normal always-available collection unless you place them in another group. If an agent has Skills selected, Extended OpenAI also protects the built-in load_skill tool from becoming unavailable through tool state, group state, or a zero tool-call limit."
    )
    .replaceAll(
      "Knowledge sources are stored with the agent and indexed locally so they can be searched. When Knowledge is enabled, the assistant can search the library, see which sources match and retrieve only the useful part of a source. The entire library is not added to every model request.",
      "Knowledge sources are stored with the agent and indexed locally so they can be searched. Each source can also be disabled independently. A disabled source stays stored and editable, but it is removed from the assistant's Knowledge catalogue, search index and retrieval until you enable it again. When Knowledge is enabled, the assistant can search the available sources, see which sources match and retrieve only the useful part of a source. The entire library is not added to every model request."
    )
    .replaceAll(
      "If you use Guest Mode, you can separately choose whether guests may use all Knowledge sources, no Knowledge sources, or only specific sources that you select.",
      "If you use Guest Mode, you can separately choose whether guests may use all available Knowledge sources, no Knowledge sources, or only specific sources that you select. Guest permissions can only restrict availability further; they cannot make a disabled source available."
    )
    .replaceAll(
      "When Extended OpenAI is managing continuity itself, the conversation timeout controls how long a conversation can sit unused before the next request starts a new one. A shorter timeout reduces the chance of an old conversation being continued by mistake. A longer timeout makes it easier to return to a discussion later.",
      "When Extended OpenAI is managing continuity itself, the conversation timeout controls how long a conversation can sit unused before the next request starts a new one. A shorter timeout reduces the chance of an old conversation being continued by mistake. A longer timeout makes it easier to return to a discussion later. Live continuity and its conversation-scoped Function Group and Request Rule state are intentionally kept in memory, so a Home Assistant restart also starts fresh. You can explicitly ask the assistant to start a fresh conversation; that reset takes effect after the current reply and clears only live context, loaded groups, conversation routing overrides and the conversation's automatic memory selection. It does not delete Persistent Memory, Temporary Memory, Knowledge Library sources or archived conversation history."
    )
    .replaceAll(
      "Configured Function Tools can request delayed execution for future device actions. Pending delayed calls are persisted across Home Assistant restarts. When a call becomes due, Extended OpenAI rechecks the current configuration, requesting user's Home Assistant permissions, and applicable authorization policy before execution. A call that had already crossed the execution boundary before an unexpected process stop is not replayed automatically.",
      "Some configured Function Tools can schedule a device action for later. Pending actions can survive a Home Assistant restart. When the time arrives, Extended OpenAI checks the current tool configuration, the requesting user's Home Assistant permissions, and any current restrictions again. If Home Assistant stopped while an action may already have begun, Extended OpenAI does not automatically run it again, because that could perform the same real-world action twice."
    )
    .replaceAll(
      "When Home Assistant supplies a device-registry device ID, Extended OpenAI uses that stable device identity for voice mapping. Satellite metadata is retained and can be used as a fallback when no registry device ID is available. The resulting mapping chooses the retained-data scope; it does not bypass Home Assistant permissions.",
      "When Home Assistant provides a stable device ID, Extended OpenAI uses it to identify which voice device made the request. If that is unavailable, satellite information can be used as a fallback. The mapping decides whose retained data should be used; it never gives that person extra Home Assistant permissions."
    )
    .replaceAll(
      "Custom regular-expression replacements run only after the response is complete, so enabling any custom replacement disables progressive TTS for that response. Replacement rules are bounded and isolated; if one fails or times out, speech falls back atomically to the text from before custom replacements.",
      "Custom regular-expression replacements run only after the response is complete, so enabling any custom replacement disables progressive TTS for that response. If a replacement fails or takes too long, Extended OpenAI simply uses the speech text from before the custom replacements instead of speaking a partly modified result."
    );

  html = html.replace(/<h3>Request debugging and backups<\/h3>[\s\S]*?<div class="notice"><strong>Restore is validated before durable state is replaced<\/strong>[\s\S]*?<\/div>/, "");

  let moved = extractGuideTopic(html, "model-data");
  html = moved.html;
  html = ensureGuideGroup(html, "operations", "Operations & troubleshooting", "Inspect requests, backups, model data, diagnostics, and maintenance when you need them.");
  html = insertGuideTopic(html, "operations", moved.topic);

  moved = extractGuideTopic(html, "quiet-hours");
  html = moved.html;
  html = ensureGuideGroup(html, "voice-speech", "Voice & speech", "Control voice identity, spoken-output cleanup, Quiet Hours, and how voice devices behave.");
  html = insertGuideTopic(html, "voice-speech", moved.topic);

  const requestDebuggingText = "request debugging inspect provider request prompt context tools parameters troubleshooting what was sent diagnostics";
  if (matchesGuideQuery(panel, requestDebuggingText)) {
    const requestDebugging = extraGuideTopic(panel, {
      id: "request-debugging",
      title: "Request debugging: see what was sent",
      summary: "Inspect a real recent provider request when an answer, tool call, or provider error is difficult to explain.",
      body: [
        {type: "p", text: "Request debugging records recent real requests so you can inspect the prompt, context, tools and request settings that were actually sent. Use it when Preview effective request is not enough to explain a real result."},
        {type: "note", title: "Treat captured requests as private", text: "A captured request can include conversation text and Home Assistant context. Use it for targeted troubleshooting and keep the retention period no longer than you need."},
      ],
      action: {label: "Open request debugging", page: "usage-maintenance", section: "request-debugging"},
    });
    html = insertGuideTopic(html, "operations", requestDebugging);
  }

  const backupText = "backup restore recovery migrate agent memories knowledge archive usage private backup rollback";
  if (matchesGuideQuery(panel, backupText)) {
    const backupRestore = extraGuideTopic(panel, {
      id: "backup-restore",
      title: "Backup & Restore",
      summary: "Create a private recovery or migration backup containing an agent's settings and retained Extended OpenAI data.",
      body: [
        {type: "p", text: "A full backup can include the agent configuration, Request Rules, memories, Knowledge sources, archived conversations, Guest Mode schedule and usage data. Provider credentials and temporary runtime state are not included."},
        {type: "p", text: "Restore checks the backup before replacing the current saved agent data. If saving the restored state fails, Extended OpenAI attempts to put the previous data back rather than intentionally leaving only part of the backup applied."},
        {type: "note", title: "Backup files can contain private data", text: "Treat full backups as private even though provider API credentials are excluded."},
      ],
      action: {label: "Open Backup & Restore", page: "usage-maintenance", section: "backup-restore"},
    });
    html = insertGuideTopic(html, "operations", backupRestore);
  }

  return removeEmptyGuideGroups(html);
}
