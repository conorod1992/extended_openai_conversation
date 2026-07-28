# First setup

After installing the integration, connect it to a Home Assistant Voice Assistant and expose the entities you want it to use.

## Select the conversation agent

1. Open **Settings > Voice assistants**.
2. Edit the assistant you want to use.
3. Open the **Conversation agent** setting.
4. Select **Extended OpenAI Conversation (Responses)**.
5. Save the assistant.

If you have several conversation-agent entries, select the one whose configuration you want this assistant to use.

## Expose entities

Home Assistant controls which entities are available to conversation agents through Assist exposure.

1. Open **Settings > Voice assistants**.
2. Open **Expose**.
3. Select the entities you want the assistant to know about or control.

Examples might include lights, switches, climate entities, media players, covers, or sensors whose state is useful in conversation.

!!! important
    Expose only entities you actually want conversation agents to access. Exposure is what gives the model Home Assistant entity context; it is not necessary to create a custom function just to control an ordinary exposed entity.

## Test a simple request

Start with something easy to verify, for example:

> Turn on the kitchen light.

Then try a request that requires several entities:

> Turn off all the lights downstairs except the hallway light.

If the assistant cannot see an entity, first confirm that the entity is exposed to the assistant.

## Choose a model and API mode

Open the conversation agent's **Options**.

For a first setup, it is usually best to:

- choose the model you want to use
- leave **API mode** on **Auto**
- leave **Memory** and **Web Search** off until basic conversations work
- keep the included Functions configuration unless you intentionally want to customize tools

Once the basic agent works, enable additional features one at a time.

## Optional: enable Web Search

With direct OpenAI and a compatible Responses model, enable **Web Search** if you want the assistant to retrieve current public information.

Try:

> What's the latest Home Assistant release?

See [Web Search](../features/web-search.md) for limitations and cost considerations.

## Optional: enable persistent memory

Set **Memory mode** to **Manual** if you want the safest introduction to persistent memory. Then say:

> Remember that I prefer temperatures in Celsius.

Start a separate conversation later and ask:

> What temperature units do I prefer?

See [Persistent memory](../features/persistent-memory.md) for storage, privacy, and Automatic mode behavior.

## Optional: configure voice follow-ups

The **Continue conversation** option controls whether a compatible voice client listens for an immediate reply after the assistant responds.

- **HA Default** preserves Home Assistant behavior.
- **Always** requests a follow-up after every successful response.
- **Conditional** lets the model indicate whether an immediate reply is expected.

See [Voice follow-ups](../features/voice-followups.md).

## Use Test agent when something looks wrong

Under **Settings > Devices & services**, open the integration and choose **Configure > Test agent**.

The test checks the agent configuration and performs at most one minimal model request. It does not execute Home Assistant device or service actions.

For common setup problems, see [Troubleshooting](../troubleshooting.md).
