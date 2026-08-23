export const GUIDE_TOPICS = [
  {
    id: "getting-started",
    title: "Getting started",
    summary: "You do not need to set up every feature. Start with the basics, make sure Home Assistant access is correct, then add extra features only when you need them.",
    terms: "setup first steps beginner basics save draft exposed entities assist provider",
    body: [
      {
        type: "p",
        text: "Extended OpenAI can work well with only a small amount of setup. Features such as memory, reference information, custom actions, voice identity and privacy controls are optional extras that you can add later."
      },
      { type: "heading", text: "A good first setup" },
      {
        type: "steps",
        items: [
          "Choose the conversation agent you want to set up using the selector at the top of the page. Each agent has its own model, instructions, features and saved data.",
          "Go to Assistant → Basics and choose the AI model you want to use. For most people, API format can stay on Auto and the other default settings are a good place to start.",
          "Check which Home Assistant entities are exposed to Assist. These are the normal Home Assistant devices, sensors and other entities that the assistant can see or control.",
          "Try using the assistant before turning on lots of extra features. Add persistent memory, Knowledge sources or custom Function Tools only when you have a reason to use them.",
          "If you want to see what information will be sent to the model, use Preview effective request. If something is not working, Diagnostics can help you test the connection and find problems."
        ]
      },
      {
        type: "note",
        title: "You can keep it simple",
        text: "You do not need to understand or change every setting. For a basic working assistant, you mainly need a model, useful instructions and the Home Assistant entities you already expose to Assist."
      },
      {
        type: "p",
        text: "Settings pages share a draft while you move between related sections. Changes are not saved automatically, so remember to press Save when you are finished."
      }
    ],
    action: { label: "Configure the assistant", page: "assistant", section: "basics" }
  },
  {
    id: "models",
    title: "Choosing a model and provider",
    summary: "The provider is the AI service you connect to, while the model is the specific AI model that answers you. Most users can leave API format on Auto.",
    terms: "model provider api responses chat completions reasoning effort service tier temperature top p tokens",
    body: [
      {
        type: "p",
        text: "The provider is the online AI service that Home Assistant connects to. The model is the particular AI model you want that service to use. One provider may offer several models, and different models may support different features."
      },
      { type: "heading", text: "The settings that matter most" },
      {
        type: "list",
        items: [
          "Chat model chooses which AI model the provider should use.",
          "API format chooses the type of request Extended OpenAI sends to the provider. Auto is recommended for most users because the integration can choose the appropriate supported format for you.",
          "Maximum response length limits how much text the model can return in a single answer.",
          "Maximum tool calls limits how many actions or tools the model can use during one request, helping prevent it from repeatedly calling tools without a sensible end."
        ]
      },
      {
        type: "p",
        text: "Some models have extra settings, such as reasoning effort or service tier. These only work if your chosen model and provider support them. Higher reasoning effort can improve answers to more difficult questions, but it may also make replies slower and use more tokens."
      },
      {
        type: "note",
        title: "Using a custom provider",
        text: "A service that describes itself as “OpenAI-compatible” may still support only some OpenAI features. If a custom provider has trouble with the Responses API, tools or advanced model settings, check which API format that provider recommends rather than assuming every option will work."
      },
      {
        type: "p",
        text: "If you are unsure what to choose, select your model, leave API format on Auto and keep the advanced response settings at their defaults. You can change them later if you find a specific reason to."
      }
    ],
    action: { label: "Open model settings", page: "assistant", section: "model-responses" }
  },
  {
    id: "continuity",
    title: "Conversation continuity",
    summary: "Conversation continuity lets the assistant remember the recent discussion so follow-up questions can make sense.",
    terms: "conversation continuity follow up timeout recent context device user sessions resume history",
    body: [
      {
        type: "p",
        text: "Normally, a new Assist request may be treated as a completely new conversation. Conversation continuity keeps the recent discussion available, so a follow-up such as “what about tomorrow?” can still refer to what you were just talking about."
      },
      { type: "heading", text: "Ways a conversation can continue" },
      {
        type: "list",
        items: [
          "Use Home Assistant sessions follows Home Assistant's normal conversation and session behaviour.",
          "Remember by voice device lets a voice device continue its recent conversation even after the immediate Assist session has ended.",
          "Remember by user across devices lets the same identified user continue a recent conversation from a different device."
        ]
      },
      {
        type: "p",
        text: "When Extended OpenAI is managing continuity itself, the conversation timeout controls how long a conversation can sit unused before the next request starts a new one. A shorter timeout reduces the chance of an old conversation being continued by mistake. A longer timeout makes it easier to return to a discussion later."
      },
      {
        type: "note",
        title: "This is not long-term memory",
        text: "Conversation continuity keeps the recent discussion together. Persistent memory stores reusable facts for later, while the conversation archive stores old conversations that you can review or search. These are separate features."
      },
      {
        type: "p",
        text: "Very long conversations can eventually become too large to keep sending to the model. Context-management settings decide what happens at that point: the integration can keep the newest complete turns, remove older context, or summarize older parts of the conversation once the configured limit is reached."
      }
    ],
    action: { label: "Configure continuity", page: "assistant", section: "conversation" }
  },
  {
    id: "memory",
    title: "What the assistant can remember",
    summary: "Extended OpenAI has several different kinds of memory and history. Each is intended for a different job.",
    terms: "memory comparison context temporary persistent archive knowledge remember data retention difference",
    body: [
      {
        type: "p",
        text: "Several features can make the assistant seem as though it “remembers” something, but they work in different ways. Using the right one keeps requests smaller and makes it clearer what information is being stored."
      },
      {
        type: "list",
        items: [
          "Conversation context keeps the recent back-and-forth so normal follow-up questions make sense.",
          "Temporary memory stores useful facts that should automatically stop being active at a particular expiry time.",
          "Persistent memory stores longer-lasting facts or preferences that may be useful in future conversations.",
          "Conversation archive stores previous discussions so you can review them and, if enabled, let the assistant search them later.",
          "Knowledge Library stores larger reference material that the assistant can search when it needs information from it."
        ]
      },
      {
        type: "note",
        title: "Use the simplest option that fits",
        text: "If a fact only matters until tomorrow, temporary memory is usually better than permanent memory. A long manual belongs in Knowledge rather than memory. A normal follow-up question usually only needs conversation continuity."
      },
      {
        type: "p",
        text: "The comparison table at the bottom of this Guide shows these five features side by side."
      }
    ],
    action: { label: "Manage memories", page: "data-memory", section: "memories" }
  },
  {
    id: "persistent-memory",
    title: "Persistent memory",
    summary: "Persistent memory stores useful facts and preferences so you do not have to explain them again in future conversations.",
    terms: "persistent long term memory manual automatic lexical semantic embeddings retrieve shared personal remember preference fact",
    body: [
      {
        type: "p",
        text: "Persistent memory is best for information that is likely to remain useful over time, such as preferences, stable facts, recurring personal context or anything else you would otherwise need to repeat regularly."
      },
      { type: "heading", text: "Choose how memories are saved" },
      {
        type: "list",
        items: [
          "Off means persistent memories are neither saved nor retrieved for this agent.",
          "Manual means a memory is saved only when you specifically ask the assistant to remember something.",
          "Automatic also allows the assistant to save stable and useful information by itself, following the integration's memory rules."
        ]
      },
      {
        type: "p",
        text: "Automatically include memories controls how many relevant memories may be added when a new conversation begins. That selected set is then reused for the conversation rather than choosing memories again after every message. Set this to 0 if you prefer memories to be used only when the assistant deliberately searches for them using its memory tools."
      },
      {
        type: "p",
        text: "For finding relevant memories, lightweight lexical retrieval looks for matching words and related text locally. Hybrid semantic retrieval can also use embeddings, which are a way of finding information with a similar meaning even when different words were used. If embeddings are not available, Extended OpenAI falls back to the local word-based method."
      },
      {
        type: "note",
        title: "What makes a good persistent memory?",
        text: "Save information that is likely to be useful beyond the current conversation. Short-term plans, one-off status updates and facts with a clear expiry time are usually better suited to temporary memory."
      },
      {
        type: "p",
        text: "You can review and delete saved memories at any time under Data & Memory → Memories. You do not have to rely on the AI model to tell you what it has stored."
      }
    ],
    action: { label: "Manage persistent memory", page: "data-memory", section: "memories" }
  },
  {
    id: "temporary-memory",
    title: "Temporary memory",
    summary: "Temporary memory keeps short-lived information available for a limited time and then lets it expire automatically.",
    terms: "temporary short term memory expiry expiring reminder current visitor package until tomorrow",
    body: [
      {
        type: "p",
        text: "Temporary memory is useful when the assistant should know something for a while, but the information should not become a permanent fact."
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
        text: "While the memory is still active, the assistant can use it when it is relevant. After the expiry time passes, it is no longer treated as active information."
      },
      {
        type: "note",
        title: "An expiry time is not a reminder",
        text: "Temporary memory does not schedule a notification or cause the assistant to contact you when the expiry time arrives. The expiry only decides how long the information remains available as memory."
      },
      {
        type: "p",
        text: "Use persistent memory when information should remain until you change or delete it. Use conversation continuity when the information only needs to last for the current discussion."
      }
    ],
    action: { label: "View temporary memories", page: "data-memory", section: "memories" }
  },
  {
    id: "archive",
    title: "Conversation archive",
    summary: "The archive stores previous conversations so you can review or search them later. It is separate from the recent context used for an active conversation.",
    terms: "archive conversation history retained search retention sessions past discussions privacy delete",
    body: [
      {
        type: "p",
        text: "Conversation continuity answers “what were we just talking about?”. The conversation archive answers “what did we discuss in the past?”. It stores conversation records for the retention period you choose."
      },
      {
        type: "p",
        text: "Under Data & Memory → Conversation history, you can browse saved conversations, search them, open individual sessions and delete them. Administrators can also see recent active continuity sessions and choose to make a session start fresh the next time it is used."
      },
      {
        type: "note",
        title: "Your whole archive is not sent with every request",
        text: "Archived conversations are stored separately. If assistant archive search is enabled, the assistant can search for relevant past discussions when needed instead of placing every old conversation into every model request."
      },
      {
        type: "p",
        text: "Retention settings decide how long archived conversations are kept. If you do not need searchable conversation history, leaving the archive disabled reduces the amount of conversation data stored."
      }
    ],
    action: { label: "Open conversation history", page: "data-memory", section: "conversations" }
  },
  {
    id: "knowledge",
    title: "Knowledge Library",
    summary: "Knowledge Library stores larger reference material that the assistant can search when needed instead of sending all of it with every request.",
    terms: "knowledge library source document manual reference search retrieval article text local excerpts",
    body: [
      {
        type: "p",
        text: "Use Knowledge Library for information that is too large or detailed to work well as a few memories. Examples include manuals, policies, household notes, project documentation, procedures and other reference material that you maintain."
      },
      {
        type: "p",
        text: "Knowledge sources are stored with the agent and indexed locally so they can be searched. When Knowledge is enabled, the assistant can search the library, see which sources match and retrieve only the useful part of a source. The entire library is not added to every model request."
      },
      {
        type: "note",
        title: "Knowledge is different from memory",
        text: "Memory is better for small facts about people, preferences or ongoing context. Knowledge is better for larger reference material that the assistant should look up when a question requires it."
      },
      {
        type: "p",
        text: "Turning the Knowledge capability off does not delete your saved sources. It simply prevents the assistant from using the Knowledge tools until you enable them again."
      },
      {
        type: "p",
        text: "If you use Guest Mode, you can separately choose whether guests may use all Knowledge sources, no Knowledge sources, or only specific sources that you select."
      }
    ],
    action: { label: "Open Knowledge Library", page: "data-memory", section: "knowledge" }
  },
  {
    id: "functions",
    title: "Function Tools and Function Groups",
    summary: "Function Tools give the assistant extra actions beyond normal Home Assistant control. Function Groups help keep large collections of tools efficient.",
    terms: "function tools groups yaml actions capabilities built in on demand loading token schemas enabled disabled",
    body: [
      {
        type: "p",
        text: "You do not need to create a Function Tool just to control normal Home Assistant devices. The assistant can already work with the Home Assistant entities and actions that are exposed through Assist."
      },
      {
        type: "p",
        text: "Create a Function Tool when you want to give the AI an extra capability or a specially defined action. Function Tools are configured using YAML, a structured text format commonly used by Home Assistant. Extended OpenAI checks the configuration before saving it. If a tool is disabled, its setup is kept, but the model cannot see or use it."
      },
      { type: "heading", text: "What Function Groups are for" },
      {
        type: "p",
        text: "Every Function Tool needs instructions that explain it to the model. If you have many tools, sending all of those instructions with every request can use unnecessary input tokens. Function Groups let you decide which tool instructions are always sent and which are loaded only when needed."
      },
      {
        type: "list",
        items: [
          "Functions that are not in a group are always available, so their full instructions are sent with requests.",
          "A group set to Always available works the same way: its functions are ready for immediate use.",
          "A group set to Load when needed initially sends only a short description of the group. If the model decides it needs those functions, it can then load the full definitions."
        ]
      },
      {
        type: "note",
        title: "The trade-off",
        text: "Load when needed can reduce repeated input-token use when you have many tools. However, the first time the assistant needs a group during a request, it may require one extra exchange with the model to load it."
      },
      {
        type: "p",
        text: "Deleting a Function Group does not delete the functions inside it. Those functions simply return to the normal always-available collection unless you place them in another group."
      }
    ],
    action: { label: "Manage functions", page: "capabilities", section: "functions" }
  },
  {
    id: "request-rules",
    title: "Request Rules",
    summary: "Request Rules let you create reliable shortcuts for particular phrases, either running Home Assistant actions without using AI or changing how the AI handles a request.",
    terms: "request rules local command hassil sentence pattern synonym fuzzy routing model action automation voice shortcut",
    body: [
      {
        type: "p",
        text: "Request Rules check what you said before the request is sent to the AI model. They only apply after Home Assistant has already chosen this Extended OpenAI conversation agent to handle the request."
      },
      {
        type: "p",
        text: "There are two main uses. A local rule can recognise a phrase, run one or more Home Assistant actions in order, and give you a response without making an OpenAI request. An AI-routing rule still sends the request to the model, but can temporarily use a different model or reasoning effort for that request or for the rest of the current conversation."
      },
      { type: "heading", text: "Choosing how a phrase should match" },
      {
        type: "list",
        items: [
          "Equals is the safest option. The full request must match the phrase, so there is less chance of a rule running accidentally.",
          "Starts with, Ends with and Contains are broader. They can be useful when your trigger phrase may appear as part of a longer request.",
          "Word forms and editable wording alternatives let Extended OpenAI accept small, predictable wording differences. Fuzzy matching is a final fallback that can accept a slightly imperfect match, but it is only tried if no stricter rule matched first.",
          "Home Assistant sentence patterns are for more flexible command shapes. They use Home Assistant's Hassil sentence format: square brackets such as [please] mean optional words, brackets such as (on|off) mean one of several choices, and slots such as {room} can capture part of the sentence.",
          "Named Hassil expansions written like <expansion> are not supported here because Request Rules do not have their own expansion catalogue.",
          "Sentence-pattern rules do not also use fuzzy matching, wording alternatives or automatic singular/plural handling. Any captured slots are recorded as part of the match, but they are not automatically inserted into the Home Assistant action data."
        ]
      },
      {
        type: "note",
        title: "Request Rule or normal Home Assistant automation?",
        text: "Use a Request Rule when the phrase is specifically for this Extended OpenAI agent, when you want it to bypass the AI model, or when it should change the model or reasoning used for the request. Use a normal Home Assistant sentence-trigger automation when the command should work independently of Extended OpenAI, needs normal automation conditions, triggers or templates, or simply belongs with the rest of your automations. A Home Assistant sentence trigger may handle the command before it ever reaches this agent."
      },
      { type: "heading", text: "Setting up the actions" },
      {
        type: "p",
        text: "The normal editor helps you choose Home Assistant entities, devices, areas and actions using familiar selectors and searchable action names. It also shows any fields provided by the selected Home Assistant action. Advanced JSON is available for options that the normal editor does not expose. If you edit an existing action using the friendly editor, existing target and data values that it does not replace are kept."
      },
      { type: "heading", text: "Safety" },
      {
        type: "p",
        text: "When Guest Mode is active, Extended OpenAI checks the entire sequence of local actions before running the first one. If any action in the sequence is not allowed for the guest, none of the actions run."
      },
      {
        type: "p",
        text: "Request Rules do not provide PIN protection or verify who is speaking. For sensitive actions such as locks or alarms, use strict matching and consider whether the action is appropriate for a voice shortcut at all. Request Rules, their default settings and their wording alternatives are included in each agent's backup."
      }
    ],
    action: { label: "Manage Request Rules", page: "capabilities", section: "request-rules" }
  },
  {
    id: "guest-mode",
    title: "Guest Mode",
    summary: "Guest Mode gives visitors a restricted version of the assistant so they do not automatically receive the owner's access, memories or conversation history.",
    terms: "guest visitors privacy exclusions labels areas domains entities knowledge functions shared memory schedule control restrictions",
    body: [
      {
        type: "p",
        text: "Guest Mode is designed for homes where another person may speak to or use the same assistant. Its restrictions are enforced by Extended OpenAI itself, rather than depending only on instructions that ask the AI model not to do certain things."
      },
      { type: "heading", text: "Choose which Home Assistant devices guests can access" },
      {
        type: "p",
        text: "Guests begin with the Home Assistant entities that are normally available to the assistant. You can then block anything they should not be able to use by Home Assistant label, area, domain or individual entity. If an entity matches any one of your exclusions, it is unavailable to the guest."
      },
      {
        type: "note",
        title: "Labels are often the easiest option",
        text: "For example, you could create a Home Assistant label called “Guest restricted” or “Private” and apply it to sensitive locks, cameras, trackers or other entities. Excluding that label in Guest Mode means the guest restrictions automatically follow the way you organise those entities in Home Assistant."
      },
      {
        type: "p",
        text: "Normally, if a guest is not allowed to read an entity, they are also not allowed to control it. Advanced options let you add extra control-only restrictions if you want an entity to be visible to guests but not controllable."
      },
      { type: "heading", text: "Knowledge, custom functions and memory" },
      {
        type: "list",
        items: [
          "Knowledge can be Off, On, or Custom. Custom lets you choose exactly which Knowledge sources guests may use.",
          "Custom functions can be Off, On, or Custom. Custom lets you choose exactly which functions or Function Groups guests may use.",
          "Shared household memory can be Off, Read only, or Read & write.",
          "Personal memory and the owner's conversation archive are never available while Guest Mode is active."
        ]
      },
      {
        type: "p",
        text: "Choosing On for Knowledge or custom functions means eligible sources or functions that you add in the future are also allowed automatically. Choose Custom if you want a fixed list that changes only when you edit it yourself."
      },
      { type: "heading", text: "Starting and ending Guest Mode" },
      {
        type: "p",
        text: "Guest Mode can start immediately or at a scheduled time. You can give it an end time or leave it active until you turn it off."
      },
      {
        type: "p",
        text: "For safety, the AI model only has one-way control over Guest Mode: it may make Guest Mode start sooner or remain active for longer, but it cannot shorten, cancel or disable it. Only trusted Home Assistant controls can reduce or end the restriction."
      },
      {
        type: "p",
        text: "Guest conversations and owner conversations are kept separate. Turning on Guest Mode does not give a visitor the owner's recent conversation context, and turning Guest Mode off does not continue the visitor's conversation as though it belonged to the owner."
      }
    ],
    action: { label: "Configure Guest Mode", page: "capabilities", section: "guest-mode" }
  },
  {
    id: "voice",
    title: "Voice assistants and multiple users",
    summary: "If several people use voice devices, you can control whose conversation and saved personal data each voice request is allowed to use.",
    terms: "voice satellite device user mapping household identity scope default user unmapped continuity privacy",
    body: [
      {
        type: "p",
        text: "A request typed into Home Assistant normally comes from a known logged-in user. Voice devices can be less clear. A kitchen satellite might be used by everyone, while a phone or bedroom satellite may effectively belong to one person."
      },
      {
        type: "p",
        text: "Voice scope settings decide whose saved data a voice request is allowed to use. Depending on your setup, an unmapped voice request can avoid personal data entirely, use shared household data, use a default user that you choose, or use a device-to-user mapping."
      },
      {
        type: "note",
        title: "Identity and conversation continuity are different",
        text: "Voice scope answers “whose memory and saved data may this request use?”. Conversation continuity answers “which recent conversation should this request continue?”. If you use shared voice devices, think about both settings."
      },
      {
        type: "p",
        text: "Device-to-user mappings work well for voice devices that reliably belong to one person. For a genuinely shared satellite, shared data or no personal retention may be safer than treating every speaker as the same person."
      },
      {
        type: "p",
        text: "If you change your voice-device mappings, also check what happens to devices that are not mapped. A new or renamed satellite should not unexpectedly start using someone's personal saved data just because no mapping exists for it yet."
      }
    ],
    action: { label: "Configure voice identity", page: "assistant", section: "voice" }
  },
  {
    id: "privacy",
    title: "Privacy and security",
    summary: "Privacy is controlled in several layers: Home Assistant access, what is sent to the model, what is stored, extra tools, Guest Mode and retention settings.",
    terms: "privacy security provider data sent local context exposed entities prompt backup guest retention memory knowledge archive tools",
    body: [
      {
        type: "p",
        text: "There is no single privacy switch because different settings control different kinds of information and access. It is easier to think of privacy in Extended OpenAI as several separate layers."
      },
      {
        type: "steps",
        items: [
          "Home Assistant exposure decides which normal Home Assistant entities are available to Assist at all.",
          "Prompt and context settings decide which instructions and live Home Assistant information are prepared for model requests.",
          "Memory, Knowledge and conversation archive decide which information is stored and may be retrieved again later.",
          "Function Tools and hosted capabilities decide which extra actions or outside information the model is allowed to use.",
          "Guest Mode places an additional, stricter visitor policy on top of your normal assistant setup.",
          "Retention and deletion settings decide how long locally stored conversation history and detailed usage information are kept."
        ]
      },
      {
        type: "note",
        title: "Stored locally does not always mean it will never be sent",
        text: "Memory, Knowledge and archived conversations can remain stored locally until they are needed. If the assistant retrieves some of that information to answer a question, the relevant part may then be included in a model request or returned as the result of a tool."
      },
      {
        type: "p",
        text: "Preview effective request lets you inspect the basic request that Extended OpenAI assembles locally for a new message, including instructions, Home Assistant context and tool definitions. It cannot show every part of provider-side handling or information that can only be decided after a future real user message is received."
      },
      {
        type: "p",
        text: "Full backups can contain private instructions, memories, Knowledge sources, archived conversations and usage information. Treat backup files as sensitive. Provider API credentials are kept separately and are not included in those backups."
      },
      {
        type: "p",
        text: "For an assistant used by several people, pay particular attention to voice identity, Guest Mode and which Home Assistant entities are exposed. These create more reliable privacy boundaries than simply putting an instruction in the prompt asking the AI model not to mention something."
      }
    ],
    action: { label: "Review Home Assistant access", page: "capabilities", section: "home-assistant" }
  },
  {
    id: "usage",
    title: "Usage, maintenance and troubleshooting",
    summary: "The maintenance pages help you understand token use, test the provider, inspect recent requests, manage retention and create private backups.",
    terms: "usage troubleshooting diagnostics tokens cached uncached recent runs provider test backup restore retention cleanup preview",
    body: [
      {
        type: "p",
        text: "The Usage page shows recent and total token use. This can help you see how heavily an agent is being used and whether changes such as Function Groups are reducing the amount of repeated input sent to the model."
      },
      {
        type: "list",
        items: [
          "Today, month and lifetime totals show overall usage.",
          "Recent runs show individual completed requests, including request counts, how long they took and whether they succeeded or failed.",
          "Cached input is a reusable part of the input included within the total token count. Some providers charge less for these cached tokens.",
          "Retention settings control how long detailed usage records are kept. This is separate from the lifetime totals."
        ]
      },
      { type: "heading", text: "If something is not working" },
      {
        type: "steps",
        items: [
          "If the agent cannot answer at all, run the provider connection test. It sends only a minimal test request and does not run Home Assistant actions.",
          "If the answer suggests the model is receiving the wrong instructions, Home Assistant context or tools, use Preview effective request.",
          "Check the recent usage and run information for failed requests or unusual behaviour.",
          "If the problem only affects one feature, check its own page: memory, Knowledge, Functions, Guest Mode or conversation history.",
          "Before making major changes, create a full backup if you want an easy way to restore the agent's saved data."
        ]
      },
      {
        type: "note",
        title: "Configuration export and full backup are different",
        text: "A configuration export is mainly for copying an agent's behaviour and settings. Full Backup & Restore also includes saved data such as memories, Knowledge sources, retained conversations, the Guest Mode schedule and usage history."
      }
    ],
    action: { label: "Open diagnostics", page: "usage-maintenance", section: "diagnostics" }
  }
];

export const MEMORY_COMPARISON = [
  [
    "Conversation context",
    "Natural follow-up questions during an ongoing discussion",
    "Until the Home Assistant session ends or the configured continuity timeout causes a new conversation to start",
    "Carried into the relevant follow-up conversation"
  ],
  [
    "Temporary memory",
    "Facts that are useful only for a limited time",
    "Until the memory's expiry time",
    "Available while it is still active when relevant to the request"
  ],
  [
    "Persistent memory",
    "Stable facts and preferences that are worth reusing later",
    "Until you change or delete them",
    "Relevant memories can be selected when a conversation starts or retrieved when needed"
  ],
  [
    "Conversation archive",
    "Reviewing or searching previous discussions",
    "For the archive retention period you have configured",
    "Previous conversations are searched only when archive access or archive search is used"
  ],
  [
    "Knowledge Library",
    "Larger reference material such as manuals, policies or project notes",
    "Until you delete the source",
    "Searched when needed, with only the relevant source content retrieved"
  ]
];
