---
name: chatgpt-mcp-image-gallery
description: Add a visible multi-image gallery to a ChatGPT-connected MCP server using MCP Apps UI. Use when image tool results need to appear as an actual gallery in ChatGPT, including cases where the model receives images but the chat shows no image cards.
---

# ChatGPT MCP image gallery

Build a gallery that people can see in the ChatGPT conversation. Returning MCP `ImageContent` alone is insufficient evidence that the ChatGPT UI displayed it.

1. Inspect the target server, image source, existing MCP connection, and user-authorized data scope. If the MCP connection itself is missing, use `$chatgpt-local-mcp` where available; this skill handles the gallery layer. Do not carry over archive-specific SQL, paths, or titles.
2. Make image tools return a useful text result even without UI. For each displayable image, return a short `TextContent` caption followed by its bounded `ImageContent`; the supplied template expects that order. Keep counts, dimensions, and total response size modest. If images live on disk, constrain resolved paths to an approved root and reject oversized, unsupported, or corrupt files before encoding. Use saved files only unless the user has also authorized network retrieval.
3. Copy [assets/image-gallery.html](assets/image-gallery.html) into the target server and register it as a `ui://` MCP resource with MIME `text/html;profile=mcp-app`. Give each gallery tool `_meta.ui.resourceUri` pointing to that resource. The optional `_meta["openai/outputTemplate"]` alias can help older ChatGPT integrations. Adapt the server binding to its language and SDK; [references/python-mcp.md](references/python-mcp.md) has a concrete Python example when relevant.
4. The template renders image blocks from `ui/notifications/tool-result` and uses text blocks as captions. Adapt the response parser if the target tool returns a different shape. Keep the tool useful in clients that cannot display MCP Apps UI. When making an incompatible template change, version the `ui://` URI to avoid stale cached HTML.
5. Validate the tool contract and resource through an MCP client, then call the tool in the connected ChatGPT app and **visually verify at least two distinct image cards**. A model statement such as “images shown above” does not prove the cards rendered. Check actual tool discovery again after changing tool metadata or the resource.

Report separately what was verified in code, in the MCP protocol, and in the ChatGPT UI. Avoid exposing local file paths or full-resolution originals in captions or public artifacts unless requested.
