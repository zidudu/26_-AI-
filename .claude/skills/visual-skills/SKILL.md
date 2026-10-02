---
name: visual-skills
description: Create visual-atlas code maps, atlas-review drift updates, visual-recap PR or commit walkthroughs, visual-spec design reviews, visual-doc illustrated HTML, and quiz comprehension checks. Use for those named workflows or requests to visualize a repository, diff, spec, plan, or document.
---

# Visual Skills

Use the bundled upstream renderer, schemas, shared guidance, and six workflows. Read [references/provenance.json](references/provenance.json) for the pinned source and [references/platform.md](references/platform.md) for platform adaptation.

1. Run `python3 <THIS_SKILL_DIR>/scripts/setup.py`. It prepares a writable runtime from the bundled source, installs locked dependencies, and verifies a pinned D2 binary. The final stdout line is the absolute runtime path; keep it as `VISUAL_SKILLS_DIR`. Resolve THIS_SKILL_DIR from the current skill's actual path: installation can rename it.
2. Choose and read the complete workflow at `<VISUAL_SKILLS_DIR>/skills/<workflow>/SKILL.md`. Read the shared plain-language guide and schemas referenced there before authoring content.

| Request | Workflow |
| --- | --- |
| Repository architecture | `visual-atlas` |
| Refresh an existing atlas against changed code | `atlas-review` |
| PR, commit, branch, or diff walkthrough | `visual-recap` |
| Design spec or RFC review | `visual-spec` |
| Markdown document or plan | `visual-doc` |
| Comprehension check | `quiz` |

3. Replace the workflow's Claude-specific tool-location formula with the returned `VISUAL_SKILLS_DIR`. For each renderer command, run from that directory, prepend `<VISUAL_SKILLS_DIR>/.bin` to PATH, and use `node --import tsx bin/<renderer>.ts ...`. This loads the installed TypeScript runtime without the tsx CLI's IPC listener, which is restricted in some hosted shells. Avoid unpinned npx downloads. Use absolute input and output paths.
4. Ground explanations in actual source, use Korean by default, and briefly define unfamiliar terms. Preserve schema field names. Read code before stamping an atlas. Do not invent files, calls, test results, or PR details.
5. Render and confirm the output exists, diagrams are SVG rather than placeholders, and requested interactions work. Open local HTML through an available preview or deliver a file link; cloud loopback URLs are not user-PC URLs.
6. Save deliverables using the applicable persistent file workflow, except repository-backed artifacts which follow repository rules. Preserve all linked pages and assets for multi-page atlases.

Editable Excalidraw export is optional and off by default. Run its upstream setup only when the deliverable needs editable scenes; distinguish the HTML from editable sidecars. Mermaid diagram handling is included in this renderer, not a separately installed account service.
