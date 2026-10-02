---
name: sites-hosting
description: Host websites with Sites. Use after `sites-building` to publish new sites and edits, for requested website publishing or deployment, or for hosting management. A project containing `.openai/hosting.json` uses Sites hosting only when the current request concerns that Site. Publishing an npm package or standalone asset is not website publishing. Honor an explicit request to use another hosting provider.
---

# Sites hosting

Use native Sites connector calls and their argument schemas. Copy IDs and cursors unchanged from the Site's manifest or tool responses.

Only the Site-owning agent operates its checkout and Sites tools through handoff. Asset and research subagents return their results; an independent background task can own a Site. Keep hosting internals out of user-facing messages: give a short publishing update, then the URL or a plain-language blocker.

## Recurring work

Automations can run on a schedule to update a Site. For clearly requested recurring work, use Sites `create_schedule` directly. For useful optional work, you can suggest an automation.

### Before setup

Read the selected Site and its linked automations. It must be active, published and owned by the user, with access to the sources and any writes needed for the work; follow [Recurring updates](../sites-building/SKILL.md#recurring-updates). If these checks or the linked automation list are unavailable, do not create or suggest an automation. This does not block the normal Site handoff.

Check `automations` for existing schedules. An empty list means none linked; missing or null means the list is unavailable, so you cannot check for duplicates. This list does not establish whether an updater is connected or running.

Reuse an automation that already covers the work unless the user requests a separate one. Preserve paused automations unless asked to resume them. Use the existing task tools for edits, preserving the Site link; changing a schedule does not require republishing the Site.

### Create when clear

Create directly when the user asks for recurring work, accepts an offer in chat, or requests an outcome that clearly requires scheduled updates. They do not need to say “schedule.” A Site's category or possible benefit alone is not enough.

If it is unclear which Site or automation to use, or what work the user wants, ask before creating or changing an automation.

Preserve requested or accepted timing. Otherwise choose a reasonable time in the user's known timezone; ask if the timezone cannot be determined.

### Suggest when optional

When an automation would be useful but is not clearly requested, finish the normal Site handoff, then add one short sentence and `offer_site_schedule`: “Want this updated with the latest news every day at 7am Pacific?” Propose a complete schedule, choosing reasonable timing when unspecified and stating the time and timezone. Do not ask setup questions before offering. The button label should name the work and timing, and the Site when needed. A text-only offer is not enough; wait for acceptance before creating.

Accepting the suggestion with the button creates and links the automation directly; do not also call `create_schedule`. If the user accepts in chat, use `create_schedule` for the same Site, work and timing offered, including their changes. Use that Site's exact ID from its tool response and recheck its linked automations. Change an existing automation for timing edits instead of creating another.

Skip optional suggestions for fixed snapshots, requests to update only manually or while the page is open, scheduled runs, work already covered by an automation, or an offer already made or declined.

### Confirm setup

After successful creation, briefly confirm the saved automation, its timing and whether it is enabled or paused. Creating an automation does not mean an update has already run.

## Rules

- Publish new sites and edits by default, including subsequent turns. Respect explicit local-only, save-without-deploying, and do-not-publish requests.
- New sites start private. Preserve the current audience unless the user requests a change. Native runtime approvals and access checks apply; no separate conversational deployment confirmation is needed.
- Publishing needs no additional browser testing or visual QA.
- Preserve the optional deployment thumbnail at `public/screenshot.jpeg` (`screenshot.jpeg` under `static.directory` for buildless sites). Create or refresh it only for an explicit Sites deployment-thumbnail request, not a generic screenshot request. Its absence or capture failure never blocks publishing.
- Store only `project_id`, optional `static` configuration, logical `d1`/`r2` bindings, verified `plugins`/`connectors` declarations, and requested supported `capabilities` in `.openai/hosting.json`. Manage runtime values through Sites.

## Site workflow

Run the bundled script directly in the selected checkout. It owns checkout preparation, ordered checks/build, source push, packaging, and archive validation:

```sh
node <plugin-root>/scripts/site-workflow.mjs --project-id <project_id>
```

Launch with `exec_command(tty: true, yield_time_ms: 1000)`. After `Ready for Site workflow JSON on stdin (input is hidden).`, send one newline-terminated JSON object through `write_stdin` with `yield_time_ms: 30000`. Wait for successful exit and return the final JSON line to the model.

Input contains `credential` plus the fields below. Reuse registration's credential or obtain one from native `create_source_repository_write_credential`. Keep credentials in session memory and stdin, out of shell arguments and files. Use absolute plugin, checkout, and archive paths and literal command arguments. The result contains `project_id`, `checkout_path`, verified `commit_sha`, and, for publishing, `archive`.

## Open a Site

- **Existing:** Reuse its `project_id`, call `get_site`, and run the script without `archivePath` before editing. Retain its result as `source` and use its `checkout_path`; pass it back when publishing. Restore missing source into an empty directory.
- **New:** Once project files exist, start [Registration](../sites-building/references/registration.md). The script prepares the new checkout automatically when publishing.

Only when updating an existing Site to add connectors for the first time, treat publishing as an upgrade of that same Site: preserve its `project_id` and use the normal save/deploy sequence. Publishing prepares its existing sign-in client automatically; do not register a replacement Site or make a separate upgrade call. New Sites and later edits to connector-enabled Sites need no migration steps. If the first connector publish failed or the older Site reports `client_not_eligible`, retry the normal publish on that same Site so it can finish the upgrade.

Overlap registration, dependency installation, asset work, and discovery of native save/deploy/status tools with authoring. Collect each result before its dependent step. Reuse successful setup and checks/builds while their inputs remain unchanged.

For starters, follow [Execution profile](../sites-building/SKILL.md#execution-profile) and its setup reference in the selected checkout. Plain static HTML needs neither profile configuration nor installation.

## Fast publish sequence

Reuse a matching archive-backed saved version for unchanged source, or continue an existing deployment to [Handoff](#handoff). Otherwise run the script once with:

- `source`: the prior opening result, when available.
- `commands`: remaining checks/builds as argument arrays, in order, after edits and required installation/assets finish. Generate changed D1 migrations before building. Use `["node", "<plugin-root>/scripts/build-site.mjs"]` for generated output; plain static HTML needs no build. Server frameworks must produce Cloudflare Workers-compatible output.
- `archivePath`: the absolute output archive path.

After the script succeeds, make a separate native call using its returned `project_id`, `commit_sha`, and `archive`:

- **Private:** use `save_version_and_deploy_private` when exposed; otherwise `save_site_version` then `deploy_private_site_version`.
- **Other audiences:** use `save_site_version` then `deploy_site_version`.

Native tools upload the archive; keep it unchanged until saving succeeds. Reuse returned version IDs, including `saved_version_id`, and skip saving an already archive-backed version. A source-only version still needs its matching archive. Return the full native result.

When the Site needs `OPENAI_API_KEY`, use the [OpenAI Developers](plugin://openai-developers@openai-curated-remote) plugin's `openai-platform-api-key` skill with user approval and configure the key as a Site secret before deployment. If unavailable, ask the user to enable that plugin.

## Deployment audience

Reuse ownership and audience from opening: private for a new owner-only Site or one confirmed owner-private for the selected account; otherwise use its known audience. If unknown or changed (`site_not_owner_only`), resolve it with `get_site` and respect the user's sharing restrictions before deploying. Never use private deployment as an access probe.

## Handoff

Before returning the Site link, apply [Recurring work](#recurring-work) to decide whether to create a task, show a suggestion or skip scheduling.

For `pending`, `building`, or `publishing`, poll `get_deployment_status` in a short `functions.exec` loop. A `succeeded` result with a URL completes verification; if its URL is missing, make one same-ID status call. Return the literal URL only from a successful native result, or report the user-visible blocker.

In a visible foreground task, use `open_in_codex` or equivalent when available, reusing the existing Site tab and stable tab ID. A failed browser handoff does not block returning the URL. Skip browser handoff for background tasks. Do not fetch the deployed URL or navigate an agent browser there merely to finish publishing; cloud-browser QA uses [managed preview](../sites-building/references/preview/managed-linux.md).
