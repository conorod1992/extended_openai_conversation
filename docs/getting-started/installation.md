# Installation

This guide gets the integration installed and visible in Home Assistant. Configuration comes next in [First setup](setup.md).

## Before you begin

You need:

- **Home Assistant 2026.7.0 or newer**; see [Compatibility](../reference/compatibility.md)
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
4. Choose **OpenAI** or **Azure OpenAI**, then enter the requested credentials.
5. For direct OpenAI, leave **Show advanced provider settings** off. A compatible third-party endpoint uses the OpenAI option with advanced provider settings enabled.
6. Set **Base URL** only when your provider requires one. For Azure, enter the endpoint. Leave **API version** blank for an endpoint ending in `/openai/v1/`. For a legacy Azure endpoint, set the dated version here or in the Home Assistant process environment with `OPENAI_API_VERSION`.
7. If your Azure gateway only exposes model deployments, enable **Skip authentication** to skip the model-list check. Requests still use the supplied API key.

For an Azure API Management unified model endpoint on `azure-api.net` ending in `/llm/v1`, choose **OpenAI**, use that gateway URL under advanced provider settings, and select **Chat Completions** and the gateway's model alias in the assistant. If using an API Management subscription key as the API key, enable bearer subscription-key recognition on the gateway. [Microsoft's unified model setup](https://learn.microsoft.com/en-us/azure/api-management/unified-model-api#call-the-api-from-a-client-application) describes this setting.

If the gateway base URL includes query parameters, give each parameter a unique name and a non-empty value. For legacy Azure endpoints, set the API version in the **API version** field or in `OPENAI_API_VERSION`. The configured version takes precedence over an `api-version` parameter in the base URL.

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

For 7.0.0, read [Upgrading this fork](../migration.md#upgrading-this-fork-to-700) before updating. Use a complete Home Assistant backup for rollback. An agent backup does not replace a backup of the integration installation and its external dependencies.
