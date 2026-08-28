import { GUIDE_TOPICS as BASE_GUIDE_TOPICS } from "./guide-content-base.js";

export const GUIDE_TOPICS = BASE_GUIDE_TOPICS.map((topic) => {
  if (topic.id !== "local-handling") return topic;
  return {
    ...topic,
    title: "Local handling: Home Assistant before AI",
    summary: "Use Home Assistant's fast built-in commands after Request Rules, while keeping selected command types available to Function Tools or AI.",
    terms: "local handling home assistant prefer local handling request rules function tools delayed device commands timer exceptions",
    body: [
      {
        type: "p",
        text: "Home Assistant already understands many simple commands, such as turning a light on, asking for the time or starting a timer. Extended OpenAI local handling lets those commands run without an AI request when that is the best route."
      },
      { type: "heading", text: "How this differs from Home Assistant's option" },
      {
        type: "p",
        text: "Home Assistant's own Prefer local handling option runs before the request reaches Extended OpenAI. That is simple and fast, but Extended OpenAI cannot then apply Request Rules or decide that a particular command should use one of your Function Tools instead."
      },
      {
        type: "p",
        text: "Extended OpenAI local handling uses a different order: Request Rules first, then Home Assistant's built-in commands, then Function Tools or AI if the request did not stay local."
      },
      {
        type: "note",
        title: "When this is useful",
        text: "For example, you may want “turn on the kitchen light” handled locally, but “turn off the kitchen light in 20 minutes” sent to a deferred-action Function Tool. The command-type choices let you keep those exceptions without giving up local handling for everything else."
      },
      { type: "heading", text: "Choose what should continue to AI" },
      {
        type: "p",
        text: "Under Send these command types to AI, select any Home Assistant command types that should skip local handling. Delayed device commands appear in this same list as a friendly choice, so they can go to Function Tools while ordinary timers can still stay local."
      },
      {
        type: "p",
        text: "The available command types come from the Home Assistant version you are running. Friendly names are shown first; the technical Home Assistant intent name is included underneath for reference."
      },
      {
        type: "note",
        title: "Turn off Home Assistant's pipeline option",
        text: "If the Assist pipeline using this agent still has Prefer local handling enabled, Home Assistant may complete commands before Extended OpenAI ever sees them. Turn that pipeline option off if you want Extended OpenAI to control the order and exceptions described here."
      },
      {
        type: "p",
        text: "Request Rules always get the first chance. Guest Mode also keeps its existing safeguards instead of using this shortcut. Leaving Extended OpenAI local handling off preserves the normal AI path."
      }
    ],
    action: { label: "Configure local handling", page: "assistant", section: "conversation" }
  };
});
