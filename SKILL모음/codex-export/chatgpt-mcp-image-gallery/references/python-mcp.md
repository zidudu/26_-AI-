# Python MCP server binding

Use this only for a target server using the Python MCP SDK. Verify its installed SDK API before copying the registration: decorator names and return behavior can differ by version.

```python
from pathlib import Path
from mcp import types
from mcp.server import MCPServer

server = MCPServer("My local gallery")
GALLERY_URI = "ui://my-local-gallery/image-gallery-v1.html"
GALLERY_META = {"ui": {"resourceUri": GALLERY_URI}}

@server.tool(name="show_images", meta=GALLERY_META, structured_output=False)
def show_images() -> list[types.TextContent | types.ImageContent]:
    # Select authorized images from the target project's own storage.
    # Make small encoded previews after checking source path, bytes, and pixels.
    previews = get_bounded_previews()
    result = []
    for caption, base64_jpeg in previews:
        result.append(types.TextContent(type="text", text=caption))
        result.append(types.ImageContent(type="image", data=base64_jpeg,
                                         mime_type="image/jpeg"))
    return result or [types.TextContent(type="text", text="No images found.")]

@server.resource(GALLERY_URI, name="Image gallery",
                 mime_type="text/html;profile=mcp-app")
def image_gallery() -> str:
    return Path(__file__).with_name("image-gallery.html").read_text(encoding="utf-8")
```

`get_bounded_previews()` is a project-specific function to implement, not a library call. Do not copy another project's queries or paths. For filesystem-backed images, resolve the approved media root and each candidate path, require `candidate.is_relative_to(root)` and `candidate.is_file()`, then enforce a source-byte limit and pixel limit before creating a thumbnail. Respect the original image orientation; convert to RGB/JPEG or another MIME type supported by the template. Avoid accepting a caller-supplied arbitrary path.

Check `tools/list` for the tool `_meta.ui.resourceUri`, `resources/read` for the matching HTML and MIME type, and `tools/call` for alternating text/image blocks. Finally call the connected tool from ChatGPT and inspect the visible gallery. If the server is reached through a tunnel, restart or refresh it only as needed and confirm the newly discovered tool version.

Primary references: [OpenAI MCP Apps UI](https://developers.openai.com/plugins/build/chatgpt-ui), [MCP Apps overview](https://apps.extensions.modelcontextprotocol.io/api/documents/overview.html), [Python SDK MCP Apps](https://py.sdk.modelcontextprotocol.io/advanced/apps/).
