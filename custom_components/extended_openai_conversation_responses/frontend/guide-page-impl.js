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
    );
}
