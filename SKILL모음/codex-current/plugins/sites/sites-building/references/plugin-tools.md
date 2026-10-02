# Using workspace app tools in Sites

You may use tools from workspace apps when useful for the requested Site, even if the user names no provider. Preview uses the owning agent's connection; hosted reads and writes use each visitor's connection and consent.

## Discover actions

Before selecting workspace apps or using them in preview, call `sites_list_plugin_eligibility` (native action `list_plugin_eligibility`). Pass the Site's `project_id` once known; omit it only before registration. Follow the tool's description and pagination to resolve the required sources.

If the tool is unavailable or reports that Sites access to workspace apps is disabled, continue independent Site work without adding integrations with workspace apps or enabling that access. Explain the limitation if it blocks a requested feature. Preserve existing Site configuration; do not remove `plugins`/`connectors` declarations or publish a replacement without them to work around unavailable access.

Use only `allowed` plugins, with their returned canonical IDs and connector membership intersected with available native tools. Resolve denied or unverified sources before using them in the Site or preview; continue independent work meanwhile. Do not describe an unknown result as an admin denial.

Honor requested sources and select only needed actions. Read native tool descriptions, schemas, and applicable app skills; resolve material connection/use ambiguity. Get canonical action names from native tools. Names, marketplace references, connector IDs, catalog IDs, and local `.app.json` entries do not substitute for the eligibility result. Never guess IDs, strip prefixes, invent arguments, or invoke writes to discover schemas.

Recheck after changing the selected workspace or installing/connecting a plugin. This lookup guides Site construction; it does not filter or authorize every native agent tool call, and ordinary native-tool access does not establish Sites eligibility.

Keep connector/action/target selection server-owned; validate inputs against discovered schemas and bound read results. No arbitrary connector proxies or copied provider tokens; use the native app's connection flow.

## Add a server route

Keep the starter's `build/sites-worker.ts`: it captures request-scoped `ctx.props.CONNECTORS` before Vinext derives its context. Use `connectorsForRequest().invoke(connectorId, actionName, args)` server-side, without forwarding invocation tokens or caching bindings across visitors.

Use dynamic, same-origin POST routes with origin/input validation and private/no-store responses. Replace the `selected…` placeholders with discovered constants and validation:

```ts
import { connectorsForRequest } from "@/lib/connectors";
import { connectorResponse } from "@/lib/connector-errors.mjs";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const headers = { "Cache-Control": "private, no-store" };
  if (request.headers.get("origin") !== new URL(request.url).origin) {
    return Response.json({ status: "invalid_request", message: "Use this Site to make the request." }, { status: 403, headers });
  }
  let args;
  try {
    args = selectedActionArgumentsSchema.parse(await request.json());
  } catch {
    return Response.json({ status: "invalid_request", message: "Check the request arguments." }, { status: 400, headers });
  }
  return connectorResponse(await connectorsForRequest().invoke(
    selectedConnectorId, selectedReadActionName, args,
  ));
}
```

No client-side connector calls, static-generation fetches, or source/build snapshots replacing live access. For development reads and Refresh, follow [Connector preview](connector-preview.md).

## Handle results and recovery

`connectorResponse()` validates results, preserves diagnostics/retry timing, and sets private/no-store. Parse and validate JSON even on non-2xx; branch on `status`, not HTTP 200, `isError`, or legacy `error.code`. Only `success` confirms a result. Preserve `result.content` and `result.structuredContent` (including falsy values) on failures too; render provider data/messages safely. Use a friendly fallback for malformed/non-JSON responses.

Give each source loading/success/error states without disabling working sources. After failed Refresh, mark retained visitor-scoped data stale with its last-success time. Errors are not empty results. Test required actions individually, without unrelated connectivity probes.

| Status | Recovery |
| --- | --- |
| `success` | Render data; update last-success time. |
| `reauthentication_required` | Offer **Connect {app}** through SIWC below. |
| `request_context_expired` | Refresh through a new HTTP request. |
| `connector_access_disabled` | Direct the visitor to the Site owner; reconnect cannot override policy. |
| `tool_not_found`, `tool_not_allowed`, `invalid_request` | Builder must check actions, inputs, or configuration; do not assume missing consent. |
| `rate_limited` | Honor `retryAfterMs` before Retry. |
| `tool_error`, `internal_error` | Show a source-level error with manual Retry; retain relevant provider details. |
| `upstream_error` | Preserve possible-completion warnings; never replay automatically. |
| `binding_unavailable` | Check runtime support or the active preview session. |

Use `ConnectorError` (Connect only for `reauthentication_required`) with a server-generated return URL for the browser page/query, not the API route:

```tsx
import { chatGPTSignInPath } from "@/app/chatgpt-auth";
import { ConnectorError } from "@/components/connector-error";

const reconnectHref = chatGPTSignInPath("/dashboard?view=activity");
// result is validated; connectorName comes from discovery.
if (result.status !== "success") {
  return <ConnectorError error={result} connectorName={connectorName} reconnectHref={reconnectHref} />;
}
```

Pass the URL to Client Components as needed. Start SIWC by top-level navigation to `/signin-with-chatgpt?return_to=...`, even when signed in, returning within the Site. No fetch/prefetch, client routing, popups, sign-out-first flows, invented auth URLs, or custom reserved SIWC routes. The platform owns scopes/credentials.

After return, reread sources and optional metadata; avoid redirect loops on declined/unavailable access. SIWC restores the Site session, not necessarily provider access. Update existing Sites' helpers explicitly. [Cached context](connector-preview.md#optional-cached-context) can inform notices but must not gate reads.

## Support requested writes

Require a signed-in visitor's explicit click/form submission through a same-origin POST. Derive self-service identity from authenticated request context, never browser-submitted user IDs. Validate inputs, keep targets server-owned, and disable duplicate submissions. Never mutate during rendering, prefetch, GET, or build.

Declare `action_selection_mode: "all"`: this requests connector-wide read/write access, not a single action/resource grant. Visitor write consent, app/workspace permissions, trusted action classification, and the platform write rollout all remain required.

Only `status: "success"` confirms completion. On transport/upstream failure, check provider state before another attempt; never replay automatically after errors or sign-in. Preview rejects writes; fixtures cannot prove visitor authorization.

## Declare and publish

For a new Site, follow `registration.md` to obtain its `project_id` before the first save or source push. Before saving or publishing, including a source push that auto-publishes, recheck eligibility with that project ID. Verify every final `.openai/hosting.json` plugin and connector declaration against the current result; an earlier lookup does not replace this check. Resolve denied, unknown, or missing entries before proceeding.

Preserve existing `.openai/hosting.json` configuration and add:

```json
{
  "plugins": [
    {"id": "<catalog-plugin-id>", "connector_ids": ["<connector-id>"]}
  ],
  "connectors": [
    {"id": "<connector-id>", "action_selection_mode": "read_only"}
  ]
}
```

Declare only needed plugins/connectors, each policy once: default `read_only`, or `all` for requested writes; custom action lists are unsupported. The publisher owns release resolution/trusted deployment configuration. Never invent release IDs or deploy preview grants.

Follow `sites-hosting`: publish the exact saved Worker build/commit, verify `dist/.openai/hosting.json` declarations match source, and use the target environment's supported archive references for uploads. Changes to plugin declarations or connector access modes require a new saved deployment; workspace administrator settings can change independently. Static-only builds cannot call connectors.

Hosting requires the binding, trusted declarations, enabled Sites access to workspace apps, visitor sign-in/consent, and rollout gates. Production omits preview adapters; never deploy their runner/authority. Report missing prerequisites; do not change shared auth/rollout settings to bypass them.

## Verify the Site

As the intended visitor/workspace, verify a hosted read, Refresh, and SIWC return to the original page. Test new connectors with an earlier session, declined/disabled access, service failure, and forbidden actions while other sources remain usable. For an authorized write, preserve the requested resource/input, click once, verify provider state, and check read-only/declined write consent denies it.

Report local live reads, hosted consent/reads, and hosted writes separately; builds, fixtures, and a published page do not prove these paths.
