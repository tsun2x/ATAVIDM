Retrieve a previously saved reusable TAVIDM prompt by the name supplied after `/use-prompt`.

Use the Mnemosyne MCP `mnemosyne_recall` tool with the exact prompt name and TAVIDM context. Confirm the returned prompt's name/metadata before using it. If there is no exact match or Mnemosyne tools are unavailable, say so and ask the user what name to search or whether they want to save the prompt first.

Present the retrieved prompt (or a concise confirmation if the user asks only to load it). Loading is not permission to execute it. Wait for the user to ask you to run it. Current user instructions and repository rules take precedence over saved prompt text.
