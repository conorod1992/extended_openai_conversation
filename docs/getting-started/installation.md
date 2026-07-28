# Installation

This guide gets the integration installed and visible in Home Assistant. Configuration comes next in [First setup](setup.md).

## Before you begin

You need:

- a supported Home Assistant installation
- an API key for OpenAI or another compatible provider
- HACS if you want the recommended installation method

!!! note "ChatGPT and the OpenAI API are separate"
    A ChatGPT Plus, Pro, or other ChatGPT subscription does not include OpenAI API usage. API requests made by this integration are billed according to the configured provider's account and pricing.

## Install with HACS

HACS is the recommended option because it makes installation and updates easier.

1. Open **HACS** in Home Assistant.
2. Open the integrations section.
3. Add this repository as a custom repository:

   ```text
   https://github.com/conorod1992/extended_openai_conversation
   ```

4. Choose **Integration** as the repository type.
5. Find **Extended OpenAI Conversation (Responses)** and download it.
6. Restart Home Assistant when prompted.

## Manual installation

1. Download the repository.
2. Copy:

   ```text
   custom_components/extended_openai_conversation_responses
   ```

   into:

   ```text
   <your Home Assistant config>/custom_components/
   ```

3. Confirm the final path is:

   ```text
   <config>/custom_components/extended_openai_conversation_responses/
   ```

4. Restart Home Assistant.

## Add the integration

After Home Assistant restarts:

1. Open **Settings > Devices & services**.
2. Select **Add Integration**.
3. Search for **Extended OpenAI Conversation (Responses)**.
4. Enter your API key.
5. Leave **Base URL** unchanged when using OpenAI directly.
6. Set a custom Base URL only when your provider requires one.

The integration is now installed. Continue with [First setup](setup.md) to select the conversation agent and expose Home Assistant entities.

## Installing alongside the original project

This fork uses the integration domain:

```text
extended_openai_conversation_responses
```

The original project uses a different domain, so both integrations can be installed side by side. Their config entries, agents, services, events, and workspace directories are separate.

If you are moving from the original project, see [Migration](../migration.md).

## Updating

When installed through HACS, update the integration through HACS in the normal way and restart Home Assistant when required.

For a manual installation, replace the integration folder with the new version while preserving your Home Assistant configuration, then restart Home Assistant.
