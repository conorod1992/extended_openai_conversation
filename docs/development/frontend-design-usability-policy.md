# EOAI FRONTEND DESIGN & USABILITY POLICY

**Version 1.1 — Revised draft after frontend policy audit**

## Purpose

This document defines the default design, layout, interaction, usability, accessibility, and consistency policies for the Extended OpenAI Conversation (EOAI) management frontend.

Its purpose is not to make every page look identical. Its purpose is to ensure that new and existing frontend features feel like parts of one product, behave predictably, remain understandable without unnecessary documentation, and continue to follow Home Assistant conventions where those conventions are suitable.

These policies are defaults. A specialised feature may diverge where the task genuinely benefits from a different interaction model, but divergence should be intentional and should preserve the shared hierarchy, terminology, accessibility, semantic styling, and interaction principles in this document.

## 1. CORE DESIGN PRINCIPLES

### 1.1 Self-explanatory by default

Ordinary workflows should normally be understandable from the page itself.

A user should generally be able to understand:
- what a control does;
- what its available choices mean;
- what changing it will affect;
- whether the change applies immediately or after saving;
- what to do next.

The Guide should provide depth, examples, concepts, advanced explanation, edge cases, and troubleshooting. It should not be required to decipher ordinary controls.

Where information is necessary to make a correct decision, it should be present beside the relevant control or section.

### 1.2 Native Home Assistant where practical

Prefer Home Assistant's native selectors, components, terminology, and interaction patterns where they suitably represent the setting or action, especially when configuring a Home Assistant-owned concept.

Examples include, where appropriate:
- entity selectors;
- device selectors;
- area selectors;
- user selectors;
- label selectors;
- action editors;
- condition editors;
- other standard HA selector types.

Ordinary accessible HTML controls remain appropriate for EOAI-owned scalar values such as free text, numbers, simple booleans, provider-specific enums, and similar settings where a native HA component adds no semantic or usability benefit.

Custom EOAI controls should exist because EOAI needs behaviour that native Home Assistant controls cannot express clearly or efficiently, not merely because a custom implementation is possible.

Native HA theme variables should be preferred over hard-coded colours or styling.

### 1.3 Design around user goals, not implementation structures

Labels, grouping, and page structure should reflect the decision the user is trying to make rather than the names used by EOAI's internal data model.

Prefer:
- "Use saved data from"
over:
- "Voice scope policy"

Prefer:
- "What should happen?"
over:
- "Action type configuration"

Internal terminology should be exposed only where the concept itself is useful or necessary for the user to understand.

### 1.4 Simple common path, full advanced capability

Common tasks should be easy to complete without removing advanced capability.

Use progressive disclosure:
- common settings remain visible;
- advanced, unusual, low-frequency, or technical controls may be placed in expandable sections, advanced areas, or specialist editors;
- expert features should remain accessible when needed.

Do not make an interface "simple" by silently removing meaningful capability.

### 1.5 Preserve user work

Navigation, refreshes, loading, switching assistants, background updates, and asynchronous operations must not unexpectedly discard user input.

Where a user has unsaved changes:
- preserve them where safely possible;
- clearly indicate unsaved state;
- warn before genuinely discarding work;
- do not create unnecessary confirmation friction where the draft can be retained safely.

Advanced or raw configuration must not be silently lost merely because a visual editor does not expose every field.

### 1.6 Truthful state

Loading, empty, unavailable, disabled, unknown, warning, and failed are distinct states and must not masquerade as one another.

For example:
- loading must not temporarily appear as "0 items";
- unavailable must not appear as intentionally disabled;
- failed requests must not appear as empty results;
- unknown must not appear as off;
- stale state must not appear authoritative.

### 1.7 Performance is part of usability

Frontend speed and responsiveness are product qualities, not merely implementation details.

Prefer:
- useful content first;
- progressive loading of expensive or secondary information;
- route-specific loading;
- local updates after small mutations;
- minimal unnecessary re-rendering;
- no full integration/page reload for a small local change where avoidable.

A small mutation should not normally cause a large reload.

### 1.8 Stable interfaces

Successful actions should update the affected object or region without unnecessarily replacing, resetting, scrolling, or re-rendering unrelated parts of the page.

The interface should avoid "jumping around" after ordinary actions.

Where possible:
- retain scroll position;
- retain focus sensibly;
- retain open context;
- update only the relevant collection item, status region, or control.

### 1.9 Authoritative persisted state

Persisted state shown to the user should come from authoritative backend results.

Optimistic or local UI updates are acceptable where safe, but the frontend must not imply that a save succeeded before persistence is established.

A successful authoritative mutation response should not later appear to have failed solely because a secondary refresh or decorative fetch failed.

### 1.10 Explain dependencies

If one control enables, disables, limits, or changes another, that relationship should be visible in the layout and wording.

Dependent settings should appear structurally subordinate to the setting that governs them.

If a disabled control could reasonably confuse the user, explain:
- why it is disabled;
- what enables it;
- where to change the prerequisite, where useful.

### 1.11 Discoverability matters

Important capabilities should not require the user to already know:
- the exact setting name;
- an internal EOAI term;
- an obscure navigation path;
- a hover-only interaction;
- an undocumented gesture.

Search, labels, descriptions, and navigation should use words users are likely to think of, including sensible aliases where appropriate.

### 1.12 Mobile and accessibility are first-class constraints

The frontend should be designed for keyboard, touch, mobile, desktop, light mode, and dark mode as part of the normal design process.

Do not treat mobile as a compressed desktop page.

Responsive layouts may change structure rather than merely shrinking.

No essential interaction should depend only on:
- hover;
- colour;
- precision clicking;
- wide-screen layout.

### 1.13 Confirmation is for meaningful risk

Use confirmation dialogs for meaningful destructive or difficult-to-reverse operations.

Examples:
- deletion;
- discarding unsaved work;
- clearing substantial stored data;
- destructive reset;
- ending or materially weakening an active protective/security state;
- consequential ownership/scope changes where ambiguity matters.

Do not add confirmation to harmless, routine, or trivially reversible operations.

### 1.14 Make scope and ownership clear

Where an action or setting applies to a specific assistant, user, guest/shared scope, memory store, device, or other owner, that scope should be clear before the user acts.

Destructive actions should name the affected object or scope where ambiguity would matter.

### 1.15 Summaries must remain accurate

Concise UI summaries are encouraged, but never simplify configuration to the point of being misleading.

If behaviour is nuanced, prefer a slightly longer but correct summary over a neat but false one.

### 1.16 Prefer shared patterns, allow justified exceptions

Shared layout, typography, action, spacing, and semantic patterns are the default.

Specialised UX is allowed where the generic pattern is genuinely unsuitable.

Consistency means:
- shared hierarchy;
- shared semantics;
- shared expectations;
- predictable interaction.

It does not mean every feature must look structurally identical.

### 1.17 Round-trip configuration safely

Switching between visual editors, raw YAML/JSON, native HA editors, or other representations must not silently destroy valid configuration that another representation does not expose.

Editing one representation should preserve valid information represented elsewhere unless the user explicitly chooses to remove it.

### 1.18 Errors should be actionable

Error messages should explain the problem in user terms and provide the next useful action where one is known.

Prefer:
- "Select a Home Assistant user before enabling Default user."
over:
- "Invalid configuration."

Technical detail may remain available for troubleshooting, but it should not be the only explanation.

## 2. INFORMATION HIERARCHY

EOAI should use a predictable four-level hierarchy.

### 2.1 Page

Purpose:
- identifies the current major page or task area.

Presentation:
- exactly one semantic H1 for the current routed content view;
- optional one-sentence tagline directly beneath;
- optional one or two subordinate contextual actions where they genuinely help the task, such as Manage, Learn more, or a closely related route;
- no competing large title immediately below unless it represents a distinct major section.

The persistent product or application brand, such as "Extended OpenAI", is shell identity rather than the semantic page title. It should not consume the routed view's H1.

A route whose content begins directly with an H2 or lower-level heading is incomplete even if the surrounding application shell already displays the product name.

Example:

Assistant settings
Configure how this assistant responds, handles conversations, uses context, and works with voice.

### 2.2 Section

Purpose:
- identifies a major functional area within the page.

Presentation:
- H2;
- optional short description;
- optional local action aligned with the heading where appropriate.

Examples:
- Memories
- Sources
- Setup & health
- Local handling

### 2.3 Subsection

Purpose:
- groups closely related settings or controls within a section.

Presentation:
- H3;
- optional short description;
- usually lighter visual separation than a full card.

Examples:
- Unidentified voice requests
- Default voice user
- Advanced matching

### 2.4 Field

Purpose:
- identifies an individual setting.

Presentation:
- concise label;
- control;
- optional help text;
- inline validation/error where applicable.

A label should identify the setting. Help text should explain its effect, trade-off, dependency, or consequence.

## 3. PAGE LAYOUT

### 3.1 Standard page structure

Normal management pages should broadly follow:

Page intro
-> optional warning/status
-> section(s)
-> section contents

The page intro may include a small number of subordinate contextual actions where they help the user continue the task, for example Manage stored memories or Learn more. Such actions should not compete visually with the main local task.

Avoid stacking multiple introductory title blocks.

### 3.2 Width

Use the shared page shell / standard EOAI maximum width.

Do not create feature-specific arbitrary page widths unless the workflow genuinely requires a different canvas, such as a specialist editor.

### 3.3 Settings-heavy pages

Settings-heavy pages should default to a flat structure:

Section heading
Settings
Divider
Next section

Do not place every settings subsection into its own card merely for visual separation.

A single enclosing settings surface is acceptable when it gives the page a coherent working area, supports a shared sticky Save/Discard affordance, or visually separates the editable configuration from surrounding navigation or status content. The interior of that surface should still remain flat and hierarchy-led rather than becoming a card-per-subsection layout.

Use spacing, headings, and dividers first.

### 3.4 Collection pages

Collection-oriented pages should generally follow:

Section card
-> section heading
-> short description/count
-> create/add action
-> optional feature status
-> search/filter
-> item list
-> empty state / pagination

Memory and Knowledge are the canonical examples of this pattern.

### 3.5 Specialist pages

Complex specialist experiences such as Request Rules may use custom internal layouts.

Their outer hierarchy should still remain consistent:
- page intro;
- clear main collection/work area;
- supporting settings/tools;
- standard action semantics;
- standard responsive and accessibility behaviour.

## 4. CARD AND SURFACE POLICY

Use hierarchy before containers.

Ordinary content should be separated using:
- headings;
- spacing;
- dividers.

Use a card when the content represents a distinct object, collection, status unit, or independently actionable group.

EOAI should recognise the following main surface types.

### 4.1 Section card

Use for a significant self-contained area.

Typical properties:
- card background;
- standard border;
- standard radius;
- generous internal padding;
- section heading at the top.

Examples:
- Memories collection;
- Knowledge sources;
- major management tools;
- Broadcast.

### 4.2 Item card / list row

Use for repeated objects within a collection.

Typical structure:
- title/content area;
- optional metadata;
- local actions.

Examples:
- memory;
- Knowledge source;
- Function Tool;
- other repeated managed objects.

Keep item cards relatively compact.

### 4.3 Supporting panel

Use for contextual information within a section.

Examples:
- preview;
- warning;
- dependency explanation;
- small status summary;
- conditional group;
- flow explanation.

Supporting panels should be visually less prominent than section cards.

### 4.4 Dashboard summary card

Overview/dashboard summary cards are a recognised special-purpose pattern.

They may differ from ordinary section cards because they are navigational/status summaries.

Do not reuse dashboard-card styling as a generic card pattern elsewhere without a reason.

### 4.5 Metric / stat card

Metric or stat cards are a recognised special-purpose surface for compact quantitative or status summaries.

Use them when:
- several comparable figures should be scanned together;
- the number or short status is the primary information;
- the cards are supporting evidence rather than navigation destinations.

Examples:
- token totals;
- request counts;
- diagnostic summary figures;
- Guest Mode scope counts.

Metric cards should remain compact and should not grow into general-purpose content cards.

### 4.6 Avoid unnecessary card proliferation

Before introducing a new card style, determine whether the content already fits:
- Section card;
- Item card;
- Supporting panel;
- Dashboard summary card;
- Metric / stat card.

A new feature should not invent another card hierarchy by default.

## 5. SPACING

Use a small consistent spacing scale wherever practical.

Recommended scale:

4px
8px
12px
16px
24px
32px

Approximate intended relationships:

4-8px:
- label to help text;
- title to metadata;
- tightly related inline elements.

12-16px:
- closely related controls;
- rows within a compact subsection;
- item-level internal spacing.

16-24px:
- subsection content;
- heading to section contents;
- related groups.

24-32px:
- major section separation;
- page intro to first substantial region.

Avoid introducing one-off spacing values without a layout reason.

Consistency of rhythm is more important than exact adherence to any single number.

## 6. TITLES, TAGLINES, AND COPY

### 6.1 Page and section descriptions

Use at most one short descriptive paragraph immediately beneath a page or major section title.

It should explain:
- purpose;
- consequence;
- or what the user can do there.

Avoid repeating documentation-level detail in introductory text.

### 6.2 Keep descriptions concise

Prefer:

Knowledge
Give the assistant reference information it can retrieve when needed.

Avoid several paragraphs describing every possible behaviour before the user reaches the controls.

Detailed explanation belongs in:
- contextual help;
- expandable details;
- the Guide;
- specialist supporting panels.

### 6.3 Plain language first

Prefer user-facing concepts over backend terminology.

Avoid implementation jargon unless it is necessary for advanced users.

### 6.4 Consistent verbs

Use consistent action verbs throughout the product.

Preferred vocabulary includes:
- Add
- Create
- Edit
- Delete
- Remove
- Enable
- Disable
- Save
- Cancel
- Reset
- Import
- Export
- Test
- Preview

Do not use multiple near-synonyms for the same operation without a semantic distinction.

### 6.5 Avoid misleading simplification

Short summaries, badges, and status labels must accurately reflect behaviour.

## 7. BUTTONS AND ACTIONS

### 7.1 Primary action

Use the primary/accent action style for the action that most naturally completes or advances the current local task.

Examples:
- Save;
- Create;
- Add source;
- Add memory;
- Confirm;
- Run diagnostics when running diagnostics is the purpose of the current section.

There may be multiple primary actions on a page if they belong to separate independent action contexts, but avoid competing primary actions in the same local context.

### 7.2 Secondary action

Use secondary styling for:
- Cancel;
- Edit;
- Configure;
- Preview;
- import/export;
- alternate choices;
- subordinate creation actions;
- actions inside a larger unsaved configuration.

"Add" is not automatically primary. It depends on whether creation is the principal action of that section.

### 7.3 Destructive action

Use destructive/danger red styling only for actions that are genuinely destructive, difficult to reverse, or materially weaken an active security, privacy, or safety state.

Examples:
- Delete;
- Remove;
- Clear;
- destructive Reset;
- ending or weakening an active protective mode where the consequence is meaningful.

Do not use red merely because an action is important, unusual, or disabled.

### 7.4 Icon actions

Use icons where:
- the meaning is conventional and clear;
- space is constrained;
- accessible names are provided.

Do not rely on icons alone when the action is unusual or ambiguous.

### 7.5 Action placement

Keep common actions close to the object or state they affect.

Avoid unnecessary separate "manage" screens when direct manipulation is clearer.

### 7.6 Async action behaviour

When an action is in progress:
- disable duplicate triggering;
- show a clear busy state;
- use appropriate loading text where useful;
- retain context;
- avoid unnecessary page reload;
- restore the control state after failure.

## 8. COLOUR AND SEMANTIC STATE

Colour should communicate semantic meaning, not feature identity.

Use Home Assistant theme variables and semantic colours where possible.

### 8.1 State palette

Recommended semantics:

Primary/accent:
- action;
- selection;
- active navigation;
- focus.

Green:
- success;
- healthy state;
- confirmed positive result.

Amber/orange:
- warning;
- review recommended;
- attention required but not necessarily broken.

Red:
- error;
- destructive/danger action;
- genuine failure.

Neutral/grey:
- informational;
- inactive;
- deliberately disabled/off;
- unavailable;
- secondary state.

A normal disabled, inactive, or off state must not inherit error-red or success-green styling merely because a badge implementation is shared with another state.

Never rely on colour alone to convey meaning.

Use text, icons, labels, or structure as well.

### 8.2 Notice semantics

Notices and callouts should use explicit semantic variants rather than a single visual style for every message.

Use:
- neutral/information for explanation, background, ordinary guidance, and non-problem status;
- amber/warning for review recommended, caution, partial degradation, or a condition that needs attention;
- green/success for confirmed healthy or successful outcomes;
- red/error for genuine failures or severe problems.

Do not present routine explanatory copy as a warning simply because it is placed in a notice container.

## 9. FORMS

### 9.1 Field structure

A normal field should contain:
- label;
- control;
- optional help text;
- optional validation/error message.

### 9.2 Single-column by default for sequential decisions

Use a single-column layout when settings are conceptually sequential or require explanation.

Use multiple columns only where:
- fields are true peers;
- each is reasonably compact;
- the layout materially improves readability.

Collapse multi-column forms to one column on narrow screens.

### 9.3 Dependent settings

Settings controlled by another setting should appear beneath or visually attached to that parent setting.

Use indentation, subtle border treatment, grouping, or another shared subordinate pattern.

Do not scatter dependent controls elsewhere on the page.

### 9.4 Validation

Validation should:
- appear close to the affected field;
- explain the problem clearly;
- avoid relying only on colour;
- preserve the user's entered value where safe.

### 9.5 Save semantics

A user should be able to tell whether:
- a change is immediately live;
- a change is a local draft;
- Save is required;
- changes remain unsaved.

Save behaviour should be consistent across similar sections.

### 9.6 Dirty state

Unsaved changes should be clearly indicated in relevant navigation/context.

If leaving would discard changes, warn before doing so.

Do not warn if the draft can safely remain available.

### 9.7 Disabled fields

Disabled controls should not become mysterious dead ends.

Where the reason is not obvious, provide:
- explanation;
- prerequisite;
- link/route to the enabling setting where useful.

## 10. COLLECTIONS

### 10.1 Standard collection structure

Collections should generally provide:
- clear heading;
- short description or count;
- create/add action;
- search/filter where useful;
- repeated items;
- empty state;
- pagination/load-more where required.

### 10.2 Repeated item actions

Edit/Delete/Remove and similar item-specific actions should generally live on the item itself.

### 10.3 Empty states

An empty state should normally make clear:
1. what is absent;
2. why the user might want it;
3. how to create it, where the creation action is not already obvious.

These requirements may be satisfied by the surrounding section context. If the section heading and description already explain the purpose and a visible Add/Create action is present, the empty-state message itself may remain concise rather than repeating the same information.

Avoid over-designed empty states that add clutter without improving understanding.

### 10.4 Search

Search should use terms a user is likely to know.

Where appropriate, include:
- name;
- description;
- aliases;
- user-facing purpose;
- metadata.

Do not require exact internal setting names.

### 10.5 Mutation updates

After successful add/edit/delete operations:
- update the affected collection locally where safe;
- preserve scroll/context;
- avoid full-route reloads unless needed for correctness.

## 11. LOADING, EMPTY, ERROR, AND UNAVAILABLE STATES

These states must remain distinct.

### 11.1 Loading

Use:
- spinner/progress;
- "Loading..." or equivalent;
- retained previous useful content where safe.

Do not display misleading zero/empty values while waiting.

### 11.2 Empty

Use an empty state only when the authoritative result is genuinely empty.

### 11.3 Error

Show:
- what failed;
- whether existing data remains usable;
- retry/recovery action where meaningful.

Whole-route or major-section load failures should normally provide an explicit Retry action when repeating the failed operation is safe and useful.

### 11.4 Unavailable

Unavailable should mean that a feature/control cannot currently be used because a prerequisite, capability, provider feature, or external dependency is absent.

It should not look identical to a feature intentionally turned off.

### 11.5 Disabled by choice

If a feature is deliberately off, say so clearly where relevant.

Stored data that remains available to manage should not be implied to have disappeared merely because the feature is disabled.

## 12. DIALOGS

Dialogs should use a predictable structure:

Header
-> title
-> optional close control

Body
-> grouped fields/content

Footer
-> Cancel / secondary actions
-> primary action

Destructive actions may be visually separated where appropriate.

Dialogs should:
- fit within the viewport;
- scroll internally when required;
- retain clear focus behaviour;
- work on narrow screens;
- avoid unnecessarily huge empty areas.

## 13. NAVIGATION AND DISCOVERABILITY

### 13.1 Stable navigational identity

Major pages and meaningful subsections should have stable navigational identities and remain directly addressable where practical.

This supports:
- browser history;
- deep links;
- Guide links;
- settings search;
- troubleshooting;
- documentation.

### 13.2 Global settings search

Global settings search should remain a first-class discoverability tool.

Search should prioritise user-facing names and purposes rather than implementation names alone.

### 13.3 Navigation should not hide important actions

Avoid important features that exist only:
- behind hover;
- in obscure menus;
- in undocumented gestures;
- off-screen without clear indication.

### 13.4 Mobile navigation

Where desktop navigation becomes unsuitable on narrow screens, replace or reorganise it rather than merely compressing it.

## 14. GUIDE AND CONTEXTUAL HELP

### 14.1 Role of the Guide

The Guide is for:
- conceptual explanation;
- advanced usage;
- examples;
- complex feature relationships;
- edge cases;
- troubleshooting.

It is not a substitute for understandable controls.

### 14.2 Contextual help

Put the minimum information needed for a safe decision next to the setting.

Use:
- short help text;
- help buttons/popovers;
- "Learn more" links;
- expandable references;
- guidance panels.

### 14.3 Avoid overloading the page

Do not duplicate long documentation directly into settings pages.

Use progressive disclosure for deeper information.

## 15. ACCESSIBILITY

All interactive elements should be usable by keyboard.

Custom controls should provide:
- accessible names;
- sensible roles;
- focus-visible treatment;
- keyboard interaction;
- state where applicable.

Do not rely only on:
- colour;
- hover;
- pointer precision.

Status changes that matter should use appropriate live-region or equivalent accessible behaviour where necessary.

Clickable cards or custom interactive regions must behave like genuine accessible controls.

Native HA controls are preferred partly because they already provide established accessibility behaviour.

## 16. RESPONSIVE DESIGN

### 16.1 Mobile is a designed layout

Narrow layouts may:
- stack sections;
- convert two columns to one;
- move actions beneath headings;
- make buttons full width;
- replace horizontal navigation;
- simplify secondary presentation.

### 16.2 Preserve capability

No feature should become inaccessible merely because the viewport is narrow.

### 16.3 Avoid overflow

Controls, cards, dialogs, tables, and editors should:
- remain reachable;
- avoid clipping;
- wrap appropriately;
- use scrolling only where it is the correct interaction.

## 17. HOME ASSISTANT INTEGRATION PRINCIPLES

### 17.1 Prefer HA selectors

Use native HA selectors when they naturally represent the value being configured, especially where the value is a Home Assistant-owned concept.

Strong candidates include:
- entities;
- devices;
- areas;
- users;
- labels;
- Home Assistant actions;
- Home Assistant conditions.

Ordinary accessible HTML controls remain appropriate for EOAI-owned scalar values such as free text, numbers, simple booleans, provider-specific enums, and other settings where a native HA selector adds no usability or semantic benefit.

The goal is to reuse Home Assistant's established authoring experience where it helps the user, not to replace every normal form control with a custom HA component.

### 17.2 Prefer HA-native action/condition editing

Where EOAI exposes Home Assistant actions or conditions, use the native authoring experience where practical rather than inventing a parallel editor.

### 17.3 Respect HA themes

Use HA-provided theme variables for:
- backgrounds;
- text;
- divider colours;
- primary/accent colours;
- semantic colours where available.

Avoid hard-coded feature colours.

### 17.4 Follow HA conventions unless EOAI has a usability reason to differ

Consistency with Home Assistant is a benefit, but EOAI may diverge where:
- HA has no suitable component;
- the native pattern would make the task materially worse;
- EOAI requires a specialist workflow.

Such divergence should be intentional.

## 18. ADVANCED EDITORS AND RAW CONFIGURATION

### 18.1 Preserve unsupported fields

Visual editors must not erase valid advanced configuration they do not expose.

### 18.2 Make mode changes understandable

If switching between visual/native/raw modes changes editing capability or representation, explain that clearly.

### 18.3 Avoid forcing raw configuration for ordinary tasks

YAML/JSON should remain available where useful, but common workflows should not require it when a clear visual/native alternative exists.

## 19. STATUS AND HEALTH PRESENTATION

Use consistent language for:
- healthy;
- enabled;
- disabled by choice;
- inactive;
- warning;
- unavailable;
- failed;
- unknown.

Do not use one label for multiple materially different states.

Disabled/inactive should normally use neutral presentation. Green should remain reserved for positive/healthy state and red for failure or danger.

Status text should tell the user what the state means, not merely name it.

Where a problem is actionable, provide the route or action needed to address it.

## 20. CONTENT DENSITY

EOAI is a powerful configuration interface and may contain a lot of information.

Aim for:
- high capability;
- low ambiguity;
- moderate visual density.

Avoid:
- giant empty layouts;
- unnecessary cards;
- repeated explanatory paragraphs;
- excessive modal flows;
- hiding common settings behind too many clicks.

Use spacing and hierarchy to improve readability without making every section oversized.

## 21. CONSISTENCY RULES FOR NEW FEATURES

When adding a new frontend feature, first determine:

1. Is this a settings section, collection, dashboard summary, metric/stat summary, or specialist workflow?
2. Can an existing EOAI layout pattern be reused?
3. Does this configure a Home Assistant-owned concept for which a native HA selector/component should be used?
4. What is the primary user task?
5. What information must be visible without the Guide?
6. What is advanced and can be progressively disclosed?
7. What state can be loading/empty/error/unavailable?
8. What data could be lost if navigation or refresh occurs?
9. What is the scope/owner of the change?
10. Does the feature behave correctly on mobile and keyboard?
11. Does it introduce a new card, spacing, colour, or button pattern unnecessarily?
12. Can the user recover from failure without losing work?

If a new visual or interaction pattern is introduced, there should be a clear reason existing patterns are unsuitable.

## 22. CURRENT CANONICAL PATTERNS

The following existing EOAI patterns should be treated as useful references.

### 22.1 Settings pages

Configuration's flatter, divider-separated section layout is the preferred model for settings-heavy pages.

A single encompassing settings surface may be retained where it provides a coherent editing canvas or shared Save/Discard behaviour, provided the internal hierarchy remains flat rather than card-per-subsection.

### 22.2 Collections

Memory and Knowledge are the preferred model for collection-oriented pages:
- section heading;
- description/count;
- Add action;
- search;
- list items;
- empty state.

### 22.3 Dashboard

Overview may retain its dashboard-summary-card pattern.

### 22.4 Metrics and compact summaries

Usage, Diagnostics, Guest Mode, and similar analytical/status views may use the shared metric/stat-card pattern for compact comparable figures.

Metric/stat cards are evidence summaries, not generic navigation or content containers.

### 22.5 Specialist workflow

Request Rules may retain a more specialised internal structure, while continuing to follow shared hierarchy, action, accessibility, and semantic rules.

### 22.6 Voice & Identity

Voice & Identity should gradually converge on the shared Section / Subsection / Supporting Panel model rather than accumulating feature-specific card types.

## 23. VISUAL CONSISTENCY CHECKLIST

Before merging a frontend change, check:

- Does the routed content expose exactly one semantic H1, separate from persistent product branding?
- Does the page use the normal H1 -> H2 -> H3 hierarchy without skipped levels where avoidable?
- Is the tagline concise?
- Is a card actually necessary?
- Is the correct shared card/surface type being used, including metric/stat cards where appropriate?
- Are spacing values consistent with the shared scale?
- Is the primary action obvious?
- Are destructive/danger actions red only when genuinely destructive, difficult to reverse, or materially weakening a protective state?
- Are disabled/inactive states neutral rather than accidentally red or green?
- Do notices use the correct neutral/warning/success/error semantics?
- Are HA theme variables used instead of arbitrary colours?
- Does the layout remain usable on narrow screens?
- Are labels understandable without the Guide?
- Are disabled controls explained where necessary?
- Are loading/error/empty/unavailable states distinct?
- Are unsaved changes preserved or clearly protected?
- Are native HA selectors/components used for Home Assistant-owned concepts where suitable, without forcing them onto ordinary EOAI scalar fields?
- Are controls keyboard accessible?
- Does the UI remain stable after local actions?
- Is the displayed state authoritative?
- Are summaries semantically accurate?

## 24. USABILITY CHECKLIST

Ask:

- Can a first-time user tell what this page is for from its own H1 and intro, without relying on the application brand or navigation label?
- Can they tell what the main action is?
- Can they understand each ordinary setting without leaving the page?
- Is important help next to the decision?
- Is advanced complexity hidden until useful?
- Will they understand what scope or assistant they are editing?
- Could they accidentally lose work?
- Could loading be mistaken for an empty result?
- If something is disabled, will they know why?
- If an operation fails, will they know what to do next?
- Does the interface behave sensibly without hover?
- Does it work on mobile?
- Does it remain usable with keyboard navigation?
- Does it feel like Home Assistant where a native HA pattern exists?
- Does the page feel like the same product as the rest of EOAI?

## 25. EXCEPTIONS

These policies are defaults, not rigid restrictions.

A feature may diverge where:
- the generic pattern would make the task harder;
- a specialised editor genuinely requires a different layout;
- performance constraints justify a different interaction;
- Home Assistant has no suitable native control;
- accessibility or clarity improves through a different design.

When diverging:
- preserve shared terminology;
- preserve semantic colours;
- preserve action meaning;
- preserve spacing discipline;
- preserve accessibility;
- preserve responsive behaviour;
- document the reason if the divergence introduces a new reusable pattern.

## 26. POLICY INTENT

The purpose of this policy is to make future EOAI frontend work easier to design and easier to review.

A contributor should be able to answer:

"How should this new feature be laid out?"
"Which button style should this use?"
"Should this be a card, a metric/stat card, a supporting panel, or no container at all?"
"Should this use a native HA selector, or is it an ordinary EOAI-owned scalar value?"
"Is this notice informational, warning, success, or error?"
"Where should the help text go?"
"What should happen while it loads?"
"What happens to unsaved work?"
"Will this still make sense on mobile?"
"Does the user need the Guide just to understand it?"

without inventing a new answer each time.

The target is not visual uniformity for its own sake.

The target is a frontend that remains:
- understandable;
- fast;
- consistent;
- powerful;
- safe;
- discoverable;
- accessible;
- recognisably native to Home Assistant;
- and scalable as EOAI grows.
