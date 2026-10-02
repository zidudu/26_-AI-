# Connector preview and optional context

Use this for live development reads/Refresh or optional metadata. Preview adapters use the owning task's native connections and accept reads only.

## Start a session

Use `sites-preview` on managed Linux or the project dev command on portable hosts. The Vinext starter supplies the session helper and local Worker/Node adapters; other starters need equivalent adapters or a host binding. Browser headers grant no authority.

Before creating preview grants, follow [Discover actions](plugin-tools.md#discover-actions) to select eligible connectors and available native read tools. If the workspace or connection changes, stop the session, recheck eligibility, and rebuild the grants before resuming.

In the Site checkout, write `.sites-runtime/connector-grants.json` with those eligible connector IDs and discovered read actions:

```json
[{"connectorId":"<discovered-connector-id>","actionName":"<discovered-read-action-name>","readOnly":true}]
```

Run with stdin open in a retained terminal session (PTY if needed) and wait for `ready`:

```sh
node "<site-root>/scripts/connector-preview/connector-preview-session.mjs" \
  "<site-root>" \
  "<site-root>/.sites-runtime/connector-grants.json"
```

If the script is missing, copy the [helper directory](../templates/vinext-starter/scripts/connector-preview/) from the plugin source to `<site-root>/scripts/connector-preview/` and install any missing helper dependencies from the [starter package.json](../templates/vinext-starter/package.json) (`<site-root>` is the absolute path to the Site checkout).

Keep the session across edits/Refresh. Defaults: 64 concurrent calls, two-minute timeout, no lifetime/call budget. Tune `--concurrency N` / `--timeout-ms N`; add budgets with `--lifetime-ms N` / `--max-calls N`. Excess concurrency returns `rate_limited`.

Request capabilities expire after 60 seconds for new calls, without cancelling accepted work. Timed-out calls retain slots until settled; never replay automatically. Stop before replacing a session; confirm crashed processes exited before deleting stale `.sites-runtime/connector-preview` state. EOF/termination closes it.

## Pump native reads

In the agent's tool context, map exact `(connectorId, actionName)` pairs to native functions that validate schemas/read limits. Unknown pairs are unauthorized. Keep the task's authentication/approval rules; never dynamically dispatch `tools[event.toolName]` or trust Site-supplied URLs, credentials, or account IDs as authority.

Use newline-delimited JSON-RPC 2.0: `invoke` carries `id` and `params: {connectorId, actionName, arguments}`. Reply `{jsonrpc: "2.0", id, result}` with the actual MCP object, including `isError`; thrown/interrupted calls return JSON-RPC errors (unconfirmed `upstream_error`, no replay). `ready`/`receipt` are notifications.

Use one retained stdin/stdout pump, enough output budget for complete lines, and concurrent independent reads. An external Node process cannot call task-native tools; an execution cell must await its pump. Adapt this example to the host's `tools.write_stdin`, actual session ID, and validated native functions:

```js
const actions = new Map([
  [JSON.stringify([connectorId, actionName]), validatedNativeRead],
]);
const pending = new Set();
const replies = [];
let buffer = "";
let previewing = true;
async function service(event) {
  const { connectorId, actionName, arguments: args } = event.params;
  const invoke = actions.get(JSON.stringify([connectorId, actionName]));
  let reply;
  try {
    if (!invoke) throw new Error("No registered native action");
    reply = { result: await invoke(args) };
  } catch {
    reply = { error: { code: -32603, message: "The native call did not return a confirmed result." } };
  }
  replies.push(JSON.stringify({ jsonrpc: "2.0", id: event.id, ...reply }) + "\n");
}
async function pump() {
  while (previewing) {
    const chunk = await tools.write_stdin({
      session_id: sessionId, chars: replies.splice(0).join(""),
      yield_time_ms: 1000, max_output_tokens: 24000,
    });
    buffer += chunk.output;
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.method !== "invoke") continue;
      const work = service(event).finally(() => pending.delete(work));
      pending.add(work);
    }
    if (chunk.exit_code !== undefined) break;
  }
}
try {
  // exerciseSite is the owning agent's editing and UI verification work.
  await Promise.all([pump(), exerciseSite().finally(() => { previewing = false; })]);
} finally {
  previewing = false;
  await tools.write_stdin({ session_id: sessionId, chars: '{"jsonrpc":"2.0","method":"stop"}\n' });
  await Promise.allSettled(pending);
}
```

Inspect the native result shape once: the runner maps MCP `content`, `structuredContent`, and `isError`; arbitrary provider text/fields do not establish reauthentication. Write replies through structured stdin, never shell interpolation.

## Verify and stop

Use normal Site controls to correlate an initial read and Refresh/query change with native calls. Check useful data, independent source errors, needed concurrency, recovery, and policy/rate limits. Fixtures do not prove SIWC or hosted consent.

Send `{"jsonrpc":"2.0","method":"stop"}` in `finally` when iteration ends, before changing task/account/connection, and before building. Confirm later requests cannot reach connectors; restart when resuming rather than leaving an unattended relay.

Transient requests/results in ignored `.sites-runtime/connector-preview` are HTTP-inaccessible and cleaned on shutdown, but readable with the agent's filesystem privileges. This bridge cannot supply hosted access. Report source/version, profile, actions, and live read/Refresh evidence; identify untested paths.

## Optional cached context

`await connectorsForRequest().getContext()` provides optional setup/action hints; never require it for reads. See `ConnectorContext` in [the starter helper](../templates/vinext-starter/lib/connectors.ts). Reuse only within one request/render; expose minimal visitor-visible fields with private/no-store caching.

Check `status` before `connectors`. Failures are `request_context_expired`, `internal_error`, `binding_unavailable` (including older hosts without the method), or `upstream_error`; none is an empty successful catalog.

Only declared connectors appear:

- `policy: "disabled"`: Site policy blocks access; direct the visitor to its owner.
- Nonempty `tools`: canonical actions/descriptions/input schemas as hints, not authorization.
- `tools: []`: cached discovery found no eligible tools; offer neutral app-setup/Site-access guidance.
- `tools: null`, missing entries, or failure: availability unconfirmed.

Context reads cached discovery without provider calls/cache refresh. It may be stale or unknown after sign-in; invocation outcomes govern recovery. Never infer provider health, authentication, or consent from metadata. Render descriptions as untrusted plain text; use verified setup URLs. Context supplies no credentials, session state, sign-in URLs, or internal routing IDs.

Preview reports locally selected grants as `policy: "enabled", tools: null`; this is not an eligibility lookup or proof of hosted access. The owning agent must perform the eligibility check above; the preview helper does not enforce workspace plugin settings itself. When adding notices, check partial, disabled, empty, missing, and failed context states with fixtures.
