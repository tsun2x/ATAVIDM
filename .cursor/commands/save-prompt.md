Use the current conversation to create a reusable TAVIDM task prompt from the plan the user has explicitly approved.

The user may provide a name after `/save-prompt`; if absent, suggest a concise name and ask before writing. Make the prompt self-contained: title, goal, context, constraints, deliverables, and completion/verification criteria. Keep unknowns explicit and do not add new requirements. Saving does not authorize running the prompt.

Use the Mnemosyne MCP `mnemosyne_remember` tool to store it with global scope and metadata identifying it as a reusable TAVIDM prompt, including its exact name. Do not save the surrounding conversation, secrets, or credentials. Verify the tool result before claiming success. If Mnemosyne tools are unavailable or fail, report that it was not saved; do not create a local substitute unless the user asks.
