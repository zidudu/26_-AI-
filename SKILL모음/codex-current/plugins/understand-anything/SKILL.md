---
name: understand-anything
description: Analyze a repository with Understand Anything into a searchable interactive knowledge graph; explore architecture, launch its dashboard, explain code, inspect diff impact, create onboarding tours, or map business domains and knowledge bases. Use for Understand Anything and understand, understand-dashboard, understand-chat, understand-diff, understand-explain, understand-onboard, understand-domain, understand-knowledge requests.
---

# Understand Anything

Use the bundled upstream analysis engine, agent instructions, schemas, and dashboard. This is a ChatGPT/Codex adaptation of the pinned source in [references/provenance.json](references/provenance.json), not a local Claude Code installation. Read [references/platform.md](references/platform.md) before invoking a workflow.

1. Run `python3 <THIS_SKILL_DIR>/scripts/setup.py`. Its final stdout line is the writable `PLUGIN_ROOT`, rebuilt from bundled source when the cache is absent. Resolve THIS_SKILL_DIR from this skill's actual current path, since installation can rename it.
2. Select and read the complete `<PLUGIN_ROOT>/skills/<workflow>/SKILL.md`, including agent references it requires. Treat `<SKILL_DIR>` there as that upstream workflow directory. Replace upstream root discovery with the returned `PLUGIN_ROOT`; set `CLAUDE_PLUGIN_ROOT` only per-command when required.

| Task | Workflow |
| --- | --- |
| Analyze or update a graph | `understand` |
| View a graph | `understand-dashboard` |
| Ask about the project | `understand-chat` |
| Inspect diff impact | `understand-diff` |
| Explain a file or function | `understand-explain` |
| Onboarding tour | `understand-onboard` |
| Business processes | `understand-domain` |
| Existing knowledge wiki | `understand-knowledge` |

3. Analyze the user-specified project, never the tool runtime by accident. Use `--language ko` for Korean requests. Respect existing settings and incremental graph state. Set `UNDERSTAND_NO_WORKTREE_REDIRECT=1` unless the user asked for main-worktree redirection.
4. Follow the upstream pipeline using available tools. Read bundled `<PLUGIN_ROOT>/agents/` definitions before dispatching specialist agents. If agents are unavailable, execute those roles sequentially and report the analysis actually performed. Keep unresolved relationships explicit. Validate graph schema, edge references, and source coverage before claiming completion.
5. Use the bundled dashboard, avoiding a different latest viewer: `GRAPH_DIR=<PROJECT_ROOT> pnpm --dir <PLUGIN_ROOT> --filter @understand-anything/dashboard dev --host 127.0.0.1`. Preserve its printed token URL for authorized local access. In a cloud session, obtain a supported preview route before claiming user access; loopback URLs belong to that environment. If no preview route is available, deliver graph JSON and explain how to view it locally.
6. Save graphs, tours, and deliverables using the applicable persistent file workflow, except repository-backed work which follows repository rules. Background servers are temporary, not permanent hosting.

Do not enable post-commit automation, modify Git hooks, delete old analysis directories, or push analyzed code as part of setup. Those optional upstream actions must fit the user's explicit scope. Figma integration is present upstream but is not configured here; use only when independently requested and connected.
