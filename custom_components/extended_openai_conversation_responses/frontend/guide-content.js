export const GUIDE_TOPICS = [
  {
    id: "getting-started",
    title: "Getting started",
    summary: "You do not need to configure everything. Start with a model, check Home Assistant access, then add extra capabilities only when you need them.",
    terms: "setup first steps beginner basics save draft exposed entities assist provider",
    body: [
      {
        type: "p",
        text: "Extended OpenAI can be useful with very little setup. The extra sections are there so you can add memory, reference information, custom actions, voice identity and privacy controls when they are useful to you."
      },
      { type: "heading", text: "A sensible first setup" },
      {
        type: "steps",
        items: [
          "Choose the conversation agent you want to configure from the selector at the top of the page. Each agent has its own model, prompt, capabilities and retained data.",
          "Open Assistant → Basics and choose the model you want to use. For most users, API format can stay on Auto and the other defaults are a good starting point.",
          "Check what Home Assistant exposes to Assist. Those exposed entities form the normal Home Assistant surface the assistant can see and control.",
          "Try the assistant before adding lots of extra features. Add persistent memory, Knowledge sources or custom Function Tools only when you have a clear use for them.",
          "Use Preview effective request and Diagnostics if you want to check what is being assembled for the model or verify that the provider connection works."
        ]
      },
      {
        type: "note",
        title: "You can keep it simple",
        text: "You do not need to understand or change every setting. A model, a sensible prompt and the Home Assistant entities you already expose to Assist are enough for a basic working assistant."
      },
      {
        type: "p",
        text: "Settings pages share a local draft. Moving between related settings does not save automatically, so remember to use Save when you are happy with your changes."
      }
    ],
    action: { label: "Configure the assistant", page: "assistant", section: "basics" }
  },
  {
    id: "models",
    title: "Choosing a model and provider",
    summary: "The provider is the service you connect to; the model is the specific AI model that answers. Most users can leave API format on Auto.",
    terms: "model provider api responses chat completions reasoning effort service tier temperature top p tokens",
    body: [
      {
        type: "p",
        text: "The provider is the API service your Home Assistant connects to. The model is the particular AI model you ask that provider to use. A provider may offer many models, and different models support different features."
      },
      { type: "heading", text: "The main choices" },
      {
        type: "list",
        items: [
          "Chat model chooses the model name sent to your provider.",
          "API format chooses how requests are sent. Auto is the recommended default because the integration can select the appropriate supported format.",
          "Maximum response length places a ceiling on how much text the model may return in one answer.",
          "Maximum tool calls prevents a conversation from repeatedly calling tools without a sensible limit."
        ]
      },
      {
        type: "p",
        text: "Some models expose extra controls such as reasoning effort or service tier. These settings only appear or work when the selected model and provider support them. Higher reasoning effort can help with harder questions, but it can also increase response time and token use."
      },
      {
        type: "note",
        title: "Custom providers",
        text: "A provider describing itself as OpenAI-compatible does not necessarily support every OpenAI feature. If a custom provider has problems with the Responses API, tools or advanced controls, try its documented API format rather than assuming every option is supported."
      },
      {
        type: "p",
        text: "If you are unsure, choose your model, leave API format on Auto and leave advanced response controls at their defaults. You can tune them later if you have a specific reason."
      }
    ],
    action: { label: "Open model settings", page: "assistant", section: "model-responses" }
  },
  {
    id: "continuity",
    title: "Conversation continuity",
    summary: "Continuity lets a later Assist request continue the recent conversation instead of treating every utterance as completely new.",
    terms: "conversation continuity follow up timeout recent context device user sessions resume history",
    body: [
      {
        type: "p",
        text: "Without continuity, a new Assist request may start with no knowledge of what you were just discussing. Continuity keeps recent conversational context available so natural follow-ups such as “what about tomorrow?” can refer to the previous exchange."
      },
      { type: "heading", text: "Ways to continue a conversation" },
      {
        type: "list",
        items: [
          "Use Home Assistant sessions follows Home Assistant's normal conversation/session behaviour.",
          "Remember by voice device lets the same voice device continue its recent conversation after the immediate Assist session has ended.",
          "Remember by user across devices lets the same identified user continue a recent conversation from another device."
        ]
      },
      {
        type: "p",
        text: "For integration-managed continuity, the conversation timeout decides how much inactivity is allowed before the next request starts fresh. A short timeout reduces accidental carry-over; a longer timeout makes conversations easier to resume later."
      },
      {
        type: "note",
        title: "Continuity is not long-term memory",
        text: "Continuity carries the recent conversation forward. Persistent memory stores reusable facts, and the conversation archive stores reviewable history. Turning one of these on does not automatically turn the others on."
      },
      {
        type: "p",
        text: "Long conversations can eventually become too large. Context-management settings decide whether the integration keeps the most recent complete turns, clears older context, or summarizes older discussion when the configured threshold is reached."
      }
    ],
    action: { label: "Configure continuity", page: "assistant", section: "conversation" }
  },
  {
    id: "memory",
    title: "What the assistant can remember",
    summary: "Recent conversation context, temporary memory, persistent memory, the conversation archive and the Knowledge Library all store or retrieve different kinds of information.",
    terms: "memory comparison context temporary persistent archive knowledge remember data retention difference",
    body: [
      {
        type: "p",
        text: "Several features can make the assistant appear to “remember”, but they solve different problems. Choosing the right one keeps prompts smaller and makes privacy and retention easier to understand."
      },
      {
        type: "list",
        items: [
          "Conversation context is the recent back-and-forth needed for natural follow-up questions.",
          "Temporary memory is for useful facts that should disappear automatically at a known expiry time.",
          "Persistent memory is for durable facts or preferences that may be useful in future conversations.",
          "Conversation archive keeps past discussions so they can be reviewed and, if enabled, searched later.",
          "Knowledge Library stores larger reference material that the assistant can search when it needs an answer from it."
        ]
      },
      {
        type: "note",
        title: "Use the smallest feature that fits",
        text: "A fact that matters only until tomorrow usually belongs in temporary memory, not permanent memory. A long manual belongs in Knowledge, not in memory. A follow-up question usually needs conversation continuity, not either of those."
      },
      {
        type: "p",
        text: "The comparison table at the bottom of this Guide gives a quick side-by-side view of these five features."
      }
    ],
    action: { label: "Manage memories", page: "data-memory", section: "memories" }
  },
  {
    id: "persistent-memory",
    title: "Persistent memory",
    summary: "Persistent memory stores reusable facts and preferences so the assistant can use them in later conversations without you repeating them each time.",
    terms: "persistent long term memory manual automatic lexical semantic embeddings retrieve shared personal remember preference fact",
    body: [
      {
        type: "p",
        text: "Persistent memory is best for information that is likely to remain useful: preferences, stable facts, recurring context or other details you would otherwise have to explain repeatedly."
      },
      { type: "heading", text: "Memory modes" },
      {
        type: "list",
        items: [
          "Off disables persistent-memory storage and retrieval for the agent.",
          "Manual stores memories only when you explicitly ask the assistant to remember something.",
          "Automatic also allows the assistant to save stable, useful information proactively under the integration's memory rules."
        ]
      },
      {
        type: "p",
        text: "Automatically include memories controls how many relevant memories can be selected when a new conversation starts. The selected bundle is reused for that conversation instead of being re-selected on every turn. Set it to 0 if you want memories to be used only when the assistant deliberately retrieves them through memory tools."
      },
      {
        type: "p",
        text: "Lightweight lexical retrieval matches words and related text locally. Hybrid semantic retrieval can also use embeddings to find conceptually similar memories even when the wording differs. If embeddings are unavailable, retrieval falls back to the local lexical method."
      },
      {
        type: "note",
        title: "Good memory candidates",
        text: "Prefer facts that are useful beyond the current conversation. Short-lived plans, one-off status updates and information with a clear expiry are usually better suited to temporary memory."
      },
      {
        type: "p",
        text: "Memories can be reviewed and deleted from Data & Memory → Memories, so you do not have to rely on the model to tell you what has been retained."
      }
    ],
    action: { label: "Manage persistent memory", page: "data-memory", section: "memories" }
  },
  {
    id: "temporary-memory",
    title: "Temporary memory",
    summary: "Temporary memory stores short-lived information that remains useful for a while and then disappears automatically at its expiry time.",
    terms: "temporary short term memory expiry expiring reminder current visitor package until tomorrow",
    body: [
      {
        type: "p",
        text: "Temporary memory is useful when the assistant should know something for a limited period but you do not want it kept as a permanent fact."
      },
      {
        type: "list",
        items: [
          "“We have a guest staying until Sunday.”",
          "“The car is in the garage until tomorrow afternoon.”",
          "“For the next week, use the temporary work address.”"
        ]
      },
      {
        type: "p",
        text: "While the memory is active, it can be made available to the assistant as relevant context. Once its expiry time passes, it is no longer treated as active information."
      },
      {
        type: "note",
        title: "Expiry is not a reminder",
        text: "Temporary memory does not itself schedule a notification or wake the assistant when the expiry time arrives. The expiry simply controls how long the information remains available as memory."
      },
      {
        type: "p",
        text: "Use persistent memory instead when the information should remain until you explicitly change or delete it. Use conversation continuity when the information only needs to survive within the current discussion."
      }
    ],
    action: { label: "View temporary memories", page: "data-memory", section: "memories" }
  },
  {
    id: "archive",
    title: "Conversation archive",
    summary: "The archive keeps past conversations for review and optional search. It is separate from the recent context used to continue an active conversation.",
    terms: "archive conversation history retained search retention sessions past discussions privacy delete",
    body: [
      {
        type: "p",
        text: "Conversation continuity answers “what were we just talking about?”. The archive answers “what did we discuss before?”. It keeps retained conversation records for the configured retention period."
      },
      {
        type: "p",
        text: "You can browse retained conversations, search them, open individual sessions and delete them from Data & Memory → Conversation history. Administrators can also see recent active continuity sessions and choose to make one start fresh next time."
      },
      {
        type: "note",
        title: "Archive does not mean every old conversation is sent every time",
        text: "Archived history is stored separately. When assistant archive search is enabled, relevant past discussion can be searched when needed rather than injecting the entire archive into every request."
      },
      {
        type: "p",
        text: "Retention settings control how long archived conversations are kept. If you do not need searchable conversation history, leaving the archive off reduces the amount of conversational data retained."
      }
    ],
    action: { label: "Open conversation history", page: "data-memory", section: "conversations" }
  },
  {
    id: "knowledge",
    title: "Knowledge Library",
    summary: "Knowledge Library is for larger reference material that the assistant can search when needed, without placing the whole source into every prompt.",
    terms: "knowledge library source document manual reference search retrieval article text local excerpts",
    body: [
      {
        type: "p",
        text: "Use Knowledge Library for information that is too large or structured to make sense as a handful of memories: manuals, policies, household reference notes, project documentation, procedures or other maintained source material."
      },
      {
        type: "p",
        text: "Sources are stored with the agent and indexed locally. When Knowledge is enabled, the assistant can search the library, see matching sources and retrieve the relevant part of a source. The full library is not automatically inserted into every model request."
      },
      {
        type: "note",
        title: "Knowledge and memory are different",
        text: "Memory is suited to compact facts about people, preferences or ongoing context. Knowledge is suited to larger reference material that the assistant should consult when a question calls for it."
      },
      {
        type: "p",
        text: "Turning the Knowledge capability off does not delete your stored sources. It makes the Knowledge tools unavailable to the assistant until you enable them again."
      },
      {
        type: "p",
        text: "In Guest Mode you can independently allow all Knowledge sources, block Knowledge entirely, or explicitly choose which sources guests may use."
      }
    ],
    action: { label: "Open Knowledge Library", page: "data-memory", section: "knowledge" }
  },
  {
    id: "functions",
    title: "Function Tools and Function Groups",
    summary: "Function Tools give the model extra actions. Function Groups help large tool collections use fewer repeated input tokens by loading detailed tool definitions only when needed.",
    terms: "function tools groups yaml actions capabilities built in on demand loading token schemas enabled disabled",
    body: [
      {
        type: "p",
        text: "You do not need a custom Function Tool for ordinary Home Assistant control. The assistant can already work with the Home Assistant entities and actions exposed through its normal Assist capabilities."
      },
      {
        type: "p",
        text: "Create a Function Tool when you want to expose an additional capability or a specially defined action to the model. Function Tools are configured as YAML and validated by the backend before they are saved. Disabled tools remain configured but are not sent to the model and cannot run."
      },
      { type: "heading", text: "What Function Groups do" },
      {
        type: "list",
        items: [
          "Ungrouped functions are always available, so their full instructions are sent with requests.",
          "A group set to Always available behaves similarly, keeping its functions immediately available.",
          "A group set to Load when needed initially sends only a small catalogue entry. The model loads the group's full function definitions only if the task needs them."
        ]
      },
      {
        type: "note",
        title: "The trade-off",
        text: "Load when needed can reduce repeated input-token use when you have many tools, but the first use of a group may require one extra model round-trip."
      },
      {
        type: "p",
        text: "Deleting a Function Group does not delete the functions inside it. They return to the normal always-available set unless you assign them elsewhere."
      }
    ],
    action: { label: "Manage functions", page: "capabilities", section: "functions" }
  },
  {
    id: "guest-mode",
    title: "Guest Mode",
    summary: "Guest Mode creates a backend-enforced visitor policy so guests can use the assistant without automatically getting the same access, memory and history as the owner.",
    terms: "guest visitors privacy exclusions labels areas domains entities knowledge functions shared memory schedule control restrictions",
    body: [
      {
        type: "p",
        text: "Guest Mode is designed for situations where another person can speak to or use the same assistant. Its restrictions are enforced by the integration itself rather than relying only on a prompt telling the model what not to do."
      },
      { type: "heading", text: "Home Assistant access" },
      {
        type: "p",
        text: "Guests start with the Home Assistant entities normally available to the assistant. You then exclude anything that should not be available by label, area, domain or individual entity. If an entity matches any exclusion, it is unavailable to the guest."
      },
      {
        type: "note",
        title: "Labels are usually the easiest approach",
        text: "For example, create a Home Assistant label such as “Guest restricted” or “Private” and apply it to sensitive locks, cameras, trackers or other entities. Excluding that label lets the Guest policy follow your Home Assistant organisation automatically."
      },
      {
        type: "p",
        text: "By default, anything a guest cannot read also cannot be controlled. If you need a narrower control policy, Advanced options can add extra control-only exclusions so an entity may be visible but not controllable."
      },
      { type: "heading", text: "Knowledge, functions and memory" },
      {
        type: "list",
        items: [
          "Knowledge can be Off, On, or Custom. Custom lets you explicitly choose the sources guests may read.",
          "Custom functions can be Off, On, or Custom. Custom lets you explicitly choose the functions or groups guests may use.",
          "Shared household memory can be Off, Read only, or Read & write.",
          "Personal memory and the owner's conversation archive remain unavailable in Guest Mode."
        ]
      },
      {
        type: "p",
        text: "Choosing On for Knowledge or custom functions also includes eligible sources or functions you add later. Choose Custom when you want a fixed, explicit guest allow-list."
      },
      { type: "heading", text: "Activation and safety" },
      {
        type: "p",
        text: "Guest Mode can be activated immediately, scheduled for later, given an end time, or left active indefinitely. The model's Guest Mode control is deliberately one-way: it may make Guest Mode start sooner or last longer, but only trusted Home Assistant controls may shorten, cancel or disable it."
      },
      {
        type: "p",
        text: "Guest and owner conversation continuity are kept separate so switching into Guest Mode does not hand an owner's recent conversation context to a visitor, and returning to owner mode does not continue the guest conversation."
      }
    ],
    action: { label: "Configure Guest Mode", page: "capabilities", section: "guest-mode" }
  },
  {
    id: "voice",
    title: "Voice assistants and multiple users",
    summary: "Voice requests may come from shared devices, so the integration lets you decide how a voice device maps to conversation continuity and retained personal data.",
    terms: "voice satellite device user mapping household identity scope default user unmapped continuity privacy",
    body: [
      {
        type: "p",
        text: "A typed request in Home Assistant usually has a clear logged-in user. A voice satellite can be different: one kitchen device may be used by several people, while a phone or bedroom satellite may effectively belong to one person."
      },
      {
        type: "p",
        text: "Voice scope settings decide whose retained data a voice request is allowed to use. Depending on your setup, an unmapped voice request can avoid personal retention, use shared household data, use a chosen default user, or use a device-to-user mapping."
      },
      {
        type: "note",
        title: "Identity and continuity are separate questions",
        text: "Voice scope answers “whose memory/data does this request belong to?”. Conversation continuity answers “which recent conversation should this request continue?”. Configure both with shared devices in mind."
      },
      {
        type: "p",
        text: "Device-to-user mappings are useful for devices that reliably belong to one person. For a genuinely shared satellite, a shared or non-retained policy may be safer than pretending every speaker is the same person."
      },
      {
        type: "p",
        text: "If you change voice mappings, review the fallback behaviour for devices that are not mapped. A new or renamed satellite should not unexpectedly inherit personal retention simply because it has no mapping yet."
      }
    ],
    action: { label: "Configure voice identity", page: "assistant", section: "voice" }
  },
  {
    id: "privacy",
    title: "Privacy and security",
    summary: "Think of privacy as layers: Home Assistant exposure, the context you send, retained data, extra tools, Guest restrictions and retention all control different parts of the system.",
    terms: "privacy security provider data sent local context exposed entities prompt backup guest retention memory knowledge archive tools",
    body: [
      {
        type: "p",
        text: "There is no single privacy switch because different features control different kinds of access. A useful way to think about the integration is as several layers."
      },
      {
        type: "steps",
        items: [
          "Home Assistant exposure decides which normal entities are available to Assist in the first place.",
          "Prompt and context settings decide which instructions and live Home Assistant information are assembled for model requests.",
          "Memory, Knowledge and conversation archive decide what information is retained and may later be retrieved.",
          "Function Tools and hosted capabilities decide what extra actions or outside information the model can use.",
          "Guest Mode adds a stricter visitor policy on top of the normal assistant configuration.",
          "Retention and deletion settings decide how long locally retained history and detailed usage information remain."
        ]
      },
      {
        type: "note",
        title: "Stored locally does not always mean never sent to the model",
        text: "Memory, Knowledge and archive data can be stored locally until needed. If the assistant retrieves a piece of that data to answer a question, the relevant retrieved content may then be included in the model request or tool result."
      },
      {
        type: "p",
        text: "Preview effective request is useful for inspecting the locally assembled baseline for a new request, including prompts, Home Assistant context and tool definitions. It cannot show every piece of provider-side framing or context that depends on a future real user message."
      },
      {
        type: "p",
        text: "Full backups can contain private prompts, memories, Knowledge sources, archived conversations and usage metadata. Treat backup files as sensitive even though provider API credentials are kept separately."
      },
      {
        type: "p",
        text: "For a shared household assistant, pay particular attention to voice identity, Guest Mode and which Home Assistant entities are exposed. Those controls are more reliable privacy boundaries than simply asking the model in its prompt not to mention something."
      }
    ],
    action: { label: "Review Home Assistant access", page: "capabilities", section: "home-assistant" }
  },
  {
    id: "usage",
    title: "Usage, maintenance and troubleshooting",
    summary: "Use the maintenance pages to understand token use, test the provider connection, inspect recent runs, control retention and make private backups.",
    terms: "usage troubleshooting diagnostics tokens cached uncached recent runs provider test backup restore retention cleanup preview",
    body: [
      {
        type: "p",
        text: "The Usage page shows recent and accumulated token use so you can see how heavily an agent is being used and whether changes such as Function Groups are reducing repeated input."
      },
      {
        type: "list",
        items: [
          "Today, month and lifetime totals give the broad picture.",
          "Recent runs show individual completed requests, request counts, duration and success or failure.",
          "Cached input is reusable request content within the total token figure and may be cheaper when the provider supports discounted caching.",
          "Retention settings control detailed usage records separately from lifetime totals."
        ]
      },
      { type: "heading", text: "A useful troubleshooting order" },
      {
        type: "steps",
        items: [
          "Run the provider connection test if the agent cannot answer at all. It sends a minimal request and does not run Home Assistant actions.",
          "Use Preview effective request if the answer suggests the model is receiving the wrong prompt, context or tool definitions.",
          "Check recent usage/run information for failures or unusual request behaviour.",
          "Review the relevant feature page if the problem is specific to memory, Knowledge, Functions, Guest Mode or conversation history.",
          "Create a full backup before major changes when you want an easy recovery point for the agent's durable data."
        ]
      },
      {
        type: "note",
        title: "Configuration export and full backup are different",
        text: "Configuration export is useful for copying agent behaviour and settings. Full Backup & Restore also includes durable data such as memories, Knowledge sources, retained conversations, Guest Mode schedule and usage history."
      }
    ],
    action: { label: "Open diagnostics", page: "usage-maintenance", section: "diagnostics" }
  }
];

export const MEMORY_COMPARISON = [
  [
    "Conversation context",
    "Natural follow-ups within an ongoing discussion",
    "Until the Home Assistant session ends or the configured continuity timeout starts a fresh conversation",
    "Carried into the relevant follow-up conversation"
  ],
  [
    "Temporary memory",
    "Facts that are useful for a limited period",
    "Until the memory's expiry time",
    "Available while active when it is relevant to the request"
  ],
  [
    "Persistent memory",
    "Stable facts and preferences worth reusing later",
    "Until changed or deleted",
    "Relevant memories can be selected at conversation start or retrieved on demand"
  ],
  [
    "Conversation archive",
    "Reviewing or searching past discussions",
    "For the configured archive retention period",
    "Past conversations are searched only when archive access/search is used"
  ],
  [
    "Knowledge Library",
    "Larger reference material such as manuals, policies or project notes",
    "Until the source is deleted",
    "Searched on demand and relevant source content is retrieved when needed"
  ]
];
