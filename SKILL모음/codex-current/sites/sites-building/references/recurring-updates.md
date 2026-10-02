# Recurring updates

Use this path when the user requests recurring updates, such as “updates daily” or “keep this Site current,” or accepts a background-update suggestion. Build the requested update capability before preparing a schedule. Follow the hosting skill for when to create, offer, or skip an automation.

## Update while the Site is closed

Browser polling or fetching when someone opens a page does not satisfy a request to update while closed. Use persistent data and an authenticated writer that a cloud task can access without the user present. Follow Persistence and storage in `persistence-and-storage.md` and inspect the Site metadata, calling `get_site` if needed. Reuse working data operations and connections. Routine refreshes should update data without rebuilding or republishing the Site.

Before choosing the writer, read Service access in `authentication.md#service-access`. Preserve the required identity, authorization, and data scope. A database binding, MCP URL, or Site service-access token alone does not prove the task can read a user's connected sources or write their data. In particular, Site service access does not supply a signed-in visitor or establish their consent to access connected apps. Verify source access and writer access independently; never use a preview binding for unattended hosted execution.

If supported access is missing, implement the supported writer and connection as part of the request. When Site-hosted tools are required, follow [Sites MCP](../../sites-mcp/SKILL.md), reusing data operations and authorization. Browser WebMCP and local-only plugins cannot supply a cloud connection. Recurring updates alone do not require a Site plugin. Offer Install/Connect only when the required connection is missing, briefly explain what it enables, and resume setup once the tools are available without asking the user to repeat the request. Do not replace requested background work with manual updates. If the required path is unavailable, explain the missing prerequisite without claiming it works.

## Save retrievable instructions

For a multi-step update, save source settings, how each run obtains supported access, required identity and data scope, update and readback steps, and retry behavior in the Site's README or existing update instructions. Never store credentials. Commit and push these instructions with the Site using the normal Site workflow in `../../sites-hosting/SKILL.md#site-workflow`.

Before referring to saved instructions in a schedule prompt, verify that a fresh cloud task can retrieve the saved revision through the linked Site without the authoring checkout. Try available source-reading tools before declaring the instructions inaccessible. Each run must reopen the same Site through Sites tools and follow that plan. A verified existing action that completes the task with a self-contained prompt does not need an additional instructions file.

## Verify the update path

After publication, verify a new or changed writer through the intended unattended access and read back the saved result through the Site's data-reading path. Preserve identity and data scope without relying on an open page. Reuse an initial content write and avoid duplicate or test-only records. Reuse this verification while the path and access scope are unchanged; do not wait for a scheduled occurrence merely to prepare setup.

Then follow Recurring work in `../../sites-hosting/SKILL.md#recurring-work`. A verified writer is a capability. An optional offer does not create a task until accepted. Successful creation confirms the saved schedule and its status, not that an unattended update has already run. Report only the state established by the tool results.
