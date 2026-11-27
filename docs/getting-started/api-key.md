# Get an OpenAI API key

If you are using the normal **OpenAI** option in Extended OpenAI, you need an OpenAI API key.

An API key is simply a private code that lets Home Assistant send requests to OpenAI on your behalf. You create it once, copy it into the integration, and normally do not need to think about it again.

!!! important "ChatGPT and the API are billed separately"
    ChatGPT and the API are separate products. Eligible Pro and Business subscribers may [claim API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits) by linking an eligible subscription to an API organisation. Credits are not automatically activated, and API usage outside the credit allowance may be billed separately.

## The short version

1. Open [OpenAI's API platform](https://platform.openai.com/) and sign in.
2. Make sure your API organisation has usable credits or API billing set up. A payment method is not required while eligible activated free credits are available.
3. Open the [API keys page](https://platform.openai.com/api-keys).
4. Select **Create new secret key**.
5. Give it a simple name such as **Home Assistant**.
6. Create the key.
7. **Copy it immediately.** OpenAI only shows the full secret key when it is created.
8. Return to Home Assistant and paste it into **OpenAI API key**.
9. Leave **Show advanced provider settings** off unless you know you need a custom endpoint.

That's it.

## If OpenAI asks about a project

For an ordinary personal setup, using your existing/default project is fine. You do not need to create a complicated project structure just for Extended OpenAI.

## If OpenAI asks about permissions

The simplest option for a personal setup is to leave the key's normal/default permissions unchanged.

You can use more restrictive permissions later if you know exactly which API features you want to allow, but that is not required just to get started.

## If the key does not work

The most common things to check are:

- Make sure you copied the **API key**, not your ChatGPT password.
- Check that the API organisation has active credits or billing enabled. A ChatGPT subscription alone does not grant API access.
- If you created a new key because the old one was lost, paste the **new** key into Home Assistant.
- Do not add spaces before or after the key when pasting it.

OpenAI's own help pages are useful if you need more detail:

- [Where do I find my OpenAI API key?](https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key)
- [Managing billing for ChatGPT and the API platform](https://help.openai.com/en/articles/9039756)

## Keep the key private

Treat an API key like a password.

Do not paste it into screenshots, GitHub issues, public YAML examples, forum posts, or messages to other people. If you think a key has been exposed, delete it on the OpenAI API keys page, create a replacement, and update Home Assistant with the new key.
