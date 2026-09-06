import { renderGuide as renderBaseGuide } from "./guide-page-base.js";

export * from "./guide-page-base.js";

export function renderGuide(panel) {
  return renderBaseGuide(panel)
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
      "Starts with, Ends with and Contains are broader. They can be useful when your trigger phrase may appear as part of a longer request.",
      "Starts with, Ends with and Contains are broader. They can be useful when your trigger phrase may appear as part of a longer request. For AI-routing rules these matches only choose the route: the entire original request is still sent unchanged, and the matched words are not stripped."
    )
    .replaceAll(
      "Home Assistant sentence patterns are for more flexible command shapes. They use Home Assistant's Hassil sentence format: square brackets such as [please] mean optional words, brackets such as (on|off) mean one of several choices, and slots such as {room} can capture part of the sentence.",
      "Home Assistant sentence patterns are for more flexible command shapes. They use Home Assistant's Hassil sentence format: square brackets such as [please] mean optional words, brackets such as (on|off) mean one of several choices, and slots such as {room} can capture part of the sentence. For AI routing, an Equals or Home Assistant sentence-pattern match is a complete routing command: Extended OpenAI acknowledges it locally and applies the selected route to the rest of the current conversation instead of forwarding that command as a normal prompt."
    )
    .replaceAll(
      "Word forms and editable wording alternatives let Extended OpenAI accept small, predictable wording differences. Fuzzy matching is a final fallback that can accept a slightly imperfect match, but it is only tried if no stricter rule matched first.",
      "Word forms and editable wording alternatives let Extended OpenAI accept small, predictable wording differences. Fuzzy matching is a final fallback that can accept a slightly imperfect match, but it is only tried if no stricter rule matched first. If otherwise equivalent rules still tie, their saved order is the final tie-breaker; moving a rule does not override match type or phrase specificity."
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
    );
}