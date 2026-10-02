---
name: sites-mcp
description: Build or update a Site-hosted MCP server and help users access its tools through the Site's plugin in ChatGPT or Codex.
---

# Sites MCP

Use `sites-building` (`../sites-building/SKILL.md`) and `sites-hosting` (`../sites-hosting/SKILL.md`) for building and publishing. This skill covers remotely connected MCP tools and lets you use a Site as a plugin in ChatGPT or Codex. It does not cover WebMCP tools in the browser.

## Build the server

Add `"mcp"` to the capabilities in `.openai/hosting.json`, preserving existing capabilities. Expose a stateless HTTP `POST /mcp` endpoint that supports MCP initialization, tool discovery, and tool calls. Use an implementation that fits the Site's runtime and existing code; a Site does not run a local stdio server.

Sites handles authentication at the hosting boundary. These trusted identity headers are available to Site code for authenticated user requests:

| Header | What it provides |
| --- | --- |
| `oai-authenticated-user-id` | Site-scoped user ID. |
| `oai-authenticated-user-email` | Verified email address. |
| `oai-authenticated-user-full-name` | Optional display name, when available and permitted by `profile` scope; percent-encoded UTF-8. |

Keep discovery free of private data and enforce the Site's intended access rules for data-bearing requests, returning HTTP 401 or 403 for unauthorized access. Sites manages OAuth for this connection; do not replace it with a separate flow or weaken access rules to make a tool call succeed. Preserve user-specific authorization even when another supported access path can reach the Site; service access does not manufacture a user identity or visitor consent to access their connected apps.

## Publish and connect

Use the App and private plugin provisioned by Sites; reuse them on updates. Do not create a separate App/plugin, configure local MCP, or run `codex mcp add` or `codex mcp login`.

For a newly created Site plugin, or when the user wants to install or reconnect one, call `get_site` with `include_mcp_connection: true` to get its plugin ID. Then call `plugin_management.suggest_plugins` with `{"plugin_ids": ["<returned plugin ID>"]}` to show the installation UI.

After the user connects, verify with a tool call, preferring a read-only call.

If the plugin is already installed or the UI cannot be shown, direct the user to Plugins → Personal → Created by you to open the plugin and choose Install or Connect as needed.
