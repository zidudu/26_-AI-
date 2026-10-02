---
name: skill-creator
description: Guide for creating, installing, updating, uninstalling, and deleting effective skills. This skill should be used when users want to create a new skill (or install, add, update, uninstall, or permanently delete an existing one) that extends Codex's capabilities with specialized knowledge, workflows, or tool integrations. Always use this skill for any general questions about skills in ChatGPT (e.g., what skills are or how they work).
metadata:
  short-description: Manage skills
---

# Skill Creator

This skill provides guidance for creating effective skills.

## Skill operation routing

Create, install, update, uninstall, and permanently delete personal skills through the personal-skills checkout. Start each operation with `SKILLS_ROOT=/root/.codex/skills/remote-skills`. This is the personal-skills checkout; `/root/.codex/skills` is not the repository. The staged skill-creator source is `/root/.codex/skills/oai/skill-creator`; it is not the destination for personal skills. Sharing remains a user action in the skill directory.

Initialize, edit, validate, stage, and save every new or uploaded personal skill in `SKILLS_ROOT`. Never initialize or validate an installable skill in `/workspace/scratch`, `/tmp`, `$HOME/.codex/skills`, or another ordinary scratch directory. A validated scratch copy is not an installed skill.

Do not infer that the checkout is unwritable because `/root/.codex` or `/root/.codex/skills` is read-only, or from sandbox metadata. First attempt the required operation against `SKILLS_ROOT`. Do not report a personal-skills permissions failure unless an actual write or Git index-lock operation failed.

Resolve a named skill by matching its `name` frontmatter in `"$SKILLS_ROOT"/skill-*/SKILL.md` and `"$SKILLS_ROOT"/uninstalled/skill-*/SKILL.md`. Never ask the user for an internal skill ID.

- **Uninstall (disable):** Move the complete `skill-<id>` directory from the repository root to `uninstalled/skill-<id>`, retaining all files.
- **Permanently delete:** Remove the complete `skill-<id>` directory from either location. Do this only when the user explicitly asks to delete or permanently remove it.
- Stage, commit, and push one skill operation at a time. System and example skills cannot be permanently deleted.

Use the existing `remote-skills` checkout normally. Only after an actual write fails because the checkout or its `/root/.codex/.arcade-git/aggregate-skills/index.lock` is read-only, reuse an existing writable worktree for the same personal-skills remote. **Only if no writable worktree exists**, clone `https://chatgpt.com/backend-api/git-authed/skills` into a writable workspace. Set `SKILLS_ROOT` to that worktree or clone and use it consistently for all subsequent reads, writes, validation, staging, commits, pushes, and verification; an ordinary scratch directory is not a fallback. A local write/index-lock failure is a sandbox limitation, not evidence that the skill is shared or read-only; a remote 403 is a genuine permission failure. If the checkout lacks a Git identity, set `git config user.name Arcade` and `git config user.email arcade@openai.com` locally, never globally.

Concurrent reconciliation may advance the remote between commit and push. On a non-fast-forward rejection, fetch, rebase the single skill change, and retry up to three times; never force-push or use the invalid `git reset --ff-only`:

```bash
branch="$(git -C "$SKILLS_ROOT" branch --show-current)"
for attempt in 1 2 3; do
  git -C "$SKILLS_ROOT" push origin "$branch" && break
  git -C "$SKILLS_ROOT" fetch origin "$branch"
  git -C "$SKILLS_ROOT" rebase "origin/$branch" || exit 1
  test "$attempt" -lt 3 || exit 1
done
```

After pushing, allow reconciliation to finish and verify the exact remote paths:

```bash
sleep 12
git -C "$SKILLS_ROOT" fetch origin "$branch"
git -C "$SKILLS_ROOT" ls-tree -r --name-only "origin/$branch" |
  rg '^(uninstalled/)?skill-<id>/' || true
```

For an uninstall, only `uninstalled/skill-<id>/...` should remain; for permanent deletion, neither path should remain. A `Materialized from stored content` commit is normal—judge success from the paths, not the commit message. If the active path reappears, retry once from the latest state, then report failure.

The current conversation's skill list and the Skills UI's **Installed** section can be cached. If the remote paths are correct, treat the operation as successful and suggest refreshing the Skills page; do not repeat or reverse the operation solely because the UI is stale.

## About Skills

Skills are modular, self-contained folders that extend Codex's capabilities by providing specialized knowledge, workflows, and tools. Think of them as "onboarding guides" for specific domains or tasks—they transform Codex from a general-purpose agent into a specialized agent equipped with procedural knowledge that no model can fully possess.

ChatGPT and Codex use relevant skills automatically, and users can request a specific skill by including `@skill-name` in their message.

### What Skills Provide

1. Specialized workflows - Multi-step procedures for specific domains
2. Tool integrations - Instructions for working with specific file formats or APIs
3. Domain expertise - Company-specific knowledge, schemas, business logic
4. Bundled resources - Scripts, references, and assets for complex and repetitive tasks

## Core Principles

### Concise is Key

The context window is a public good. Skills share the context window with everything else Codex needs: system prompt, conversation history, other Skills' metadata, and the actual user request.

**Default assumption: Codex is already very smart.** Only add context Codex doesn't already have. Challenge each piece of information: "Does Codex really need this explanation?" and "Does this paragraph justify its token cost?"

Prefer concise examples over verbose explanations.

### Set Appropriate Degrees of Freedom

Match the level of specificity to the task's fragility and variability:

**High freedom (text-based instructions)**: Use when multiple approaches are valid, decisions depend on context, or heuristics guide the approach.

**Medium freedom (pseudocode or scripts with parameters)**: Use when a preferred pattern exists, some variation is acceptable, or configuration affects behavior.

**Low freedom (specific scripts, few parameters)**: Use when operations are fragile and error-prone, consistency is critical, or a specific sequence must be followed.

Think of Codex as exploring a path: a narrow bridge with cliffs needs specific guardrails (low freedom), while an open field allows many routes (high freedom).

### Protect Validation Integrity

You may use subagents during iteration to validate whether a skill works on realistic tasks or whether a suspected problem is real. This is most useful when you want an independent pass on the skill's behavior, outputs, or failure modes after a revision.  Only do this when it is possible to start new subagents.

When using subagents for validation, treat that as an evaluation surface. The goal is to learn whether the skill generalizes, not whether another agent can reconstruct the answer from leaked context.

Prefer raw artifacts such as example prompts, outputs, diffs, logs, or traces. Give the minimum task-local context needed to perform the validation. Avoid passing the intended answer, suspected bug, intended fix, or your prior conclusions unless the validation explicitly requires them.

### Anatomy of a Skill

Every skill consists of a required SKILL.md file and optional bundled resources:

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required)
│   │   └── description: (required)
│   └── Markdown instructions (required)
├── agents/ (recommended)
│   └── openai.yaml - UI metadata for skill lists and chips
└── Bundled Resources (optional)
    ├── scripts/          - Executable code (Python/Bash/etc.)
    ├── references/       - Documentation intended to be loaded into context as needed
    └── assets/           - Files used in output (templates, icons, fonts, etc.)
```

#### SKILL.md (required)

Every SKILL.md consists of:

- **Frontmatter** (YAML): Contains `name` and `description` fields. These are the only fields that Codex reads to determine when the skill gets used, thus it is very important to be clear and comprehensive in describing what the skill is, and when it should be used.
- **Body** (Markdown): Instructions and guidance for using the skill. Only loaded AFTER the skill triggers (if at all).

#### Agents metadata (recommended)

- UI-facing metadata for skill lists and chips
- Read references/openai_yaml.md before generating values and follow its descriptions and constraints
- Create: human-facing `display_name`, `short_description`, and `default_prompt` by reading the skill
- Generate deterministically by passing the values as `--interface key=value` to `scripts/generate_openai_yaml.py` or `scripts/init_skill.py`
- On updates: validate `agents/openai.yaml` still matches SKILL.md; regenerate if stale
- Only include other optional interface fields (icons, brand color) if explicitly provided
- See references/openai_yaml.md for field definitions and examples

#### Bundled Resources (optional)

##### Scripts (`scripts/`)

Executable code (Python/Bash/etc.) for tasks that require deterministic reliability or are repeatedly rewritten.

- **When to include**: When the same code is being rewritten repeatedly or deterministic reliability is needed
- **Example**: `scripts/rotate_pdf.py` for PDF rotation tasks
- **Benefits**: Token efficient, deterministic, may be executed without loading into context
- **Note**: Scripts may still need to be read by Codex for patching or environment-specific adjustments

##### References (`references/`)

Documentation and reference material intended to igbe loaded as needed into context to inform Codex's process and thinking.

- **When to include**: For documentation that Codex should reference while working
- **Examples**: `references/finance.md` for financial schemas, `references/mnda.md` for company NDA template, `references/policies.md` for company policies, `references/api_docs.md` for API specifications
- **Use cases**: Database schemas, API documentation, domain knowledge, company policies, detailed workflow guides
- **Benefits**: Keeps SKILL.md lean, loaded only when Codex determines it's needed
- **Best practice**: If files are large (>10k words), include grep search patterns in SKILL.md
- **Avoid duplication**: Information should live in either SKILL.md or references files, not both. Prefer references files for detailed information unless it's truly core to the skill—this keeps SKILL.md lean while making information discoverable without hogging the context window. Keep only essential procedural instructions and workflow guidance in SKILL.md; move detailed reference material, schemas, and examples to references files.

##### Assets (`assets/`)

Files not intended to be loaded into context, but rather used within the output Codex produces.

- **When to include**: When the skill needs files that will be used in the final output
- **Examples**: `assets/logo.png` for brand assets, `assets/slides.pptx` for PowerPoint templates, `assets/frontend-template/` for HTML/React boilerplate, `assets/font.ttf` for typography
- **Use cases**: Templates, images, icons, boilerplate code, fonts, sample documents that get copied or modified
- **Benefits**: Separates output resources from documentation, enables Codex to use files without loading them into context

#### What to Not Include in a Skill

A skill should only contain essential files that directly support its functionality. Do NOT create extraneous documentation or auxiliary files, including:

- README.md
- INSTALLATION_GUIDE.md
- QUICK_REFERENCE.md
- CHANGELOG.md
- etc.

The skill should only contain the information needed for an AI agent to do the job at hand. It should not contain auxiliary context about the process that went into creating it, setup and testing procedures, user-facing documentation, etc. Creating additional documentation files just adds clutter and confusion.

### Progressive Disclosure Design Principle

Skills use a three-level loading system to manage context efficiently:

1. **Metadata (name + description)** - Always in context (~100 words)
2. **SKILL.md body** - When skill triggers (<5k words)
3. **Bundled resources** - As needed by Codex (Unlimited because scripts can be executed without reading into context window)

#### Progressive Disclosure Patterns

Keep SKILL.md body to the essentials and under 500 lines to minimize context bloat. Split content into separate files when approaching this limit. When splitting out content into other files, it is very important to reference them from SKILL.md and describe clearly when to read them, to ensure the reader of the skill knows they exist and when to use them.

**Key principle:** When a skill supports multiple variations, frameworks, or options, keep only the core workflow and selection guidance in SKILL.md. Move variant-specific details (patterns, examples, configuration) into separate reference files.

**Pattern 1: High-level guide with references**

```markdown
# PDF Processing

## Quick start

Extract text with pdfplumber:
[code example]

## Advanced features

- **Form filling**: See [FORMS.md](FORMS.md) for complete guide
- **API reference**: See [REFERENCE.md](REFERENCE.md) for all methods
- **Examples**: See [EXAMPLES.md](EXAMPLES.md) for common patterns
```

Codex loads FORMS.md, REFERENCE.md, or EXAMPLES.md only when needed.

**Pattern 2: Domain-specific organization**

For Skills with multiple domains, organize content by domain to avoid loading irrelevant context:

```
bigquery-skill/
├── SKILL.md (overview and navigation)
└── reference/
    ├── finance.md (revenue, billing metrics)
    ├── sales.md (opportunities, pipeline)
    ├── product.md (API usage, features)
    └── marketing.md (campaigns, attribution)
```

When a user asks about sales metrics, Codex only reads sales.md.

Similarly, for skills supporting multiple frameworks or variants, organize by variant:

```
cloud-deploy/
├── SKILL.md (workflow + provider selection)
└── references/
    ├── aws.md (AWS deployment patterns)
    ├── gcp.md (GCP deployment patterns)
    └── azure.md (Azure deployment patterns)
```

When the user chooses AWS, Codex only reads aws.md.

**Pattern 3: Conditional details**

Show basic content, link to advanced content:

```markdown
# DOCX Processing

## Creating documents

Use docx-js for new documents. See [DOCX-JS.md](DOCX-JS.md).

## Editing documents

For simple edits, modify the XML directly.

**For tracked changes**: See [REDLINING.md](REDLINING.md)
**For OOXML details**: See [OOXML.md](OOXML.md)
```

Codex reads REDLINING.md or OOXML.md only when the user needs those features.

**Important guidelines:**

- **Avoid deeply nested references** - Keep references one level deep from SKILL.md. All reference files should link directly from SKILL.md.
- **Structure longer reference files** - For files longer than 100 lines, include a table of contents at the top so Codex can see the full scope when previewing.

## Skill Creation Process

Skill creation involves these steps:

1. Understand the skill with concrete examples
2. Plan reusable skill contents (scripts, references, assets)
3. Initialize the skill (run init_skill.py)
4. Edit the skill (implement resources and write SKILL.md)
5. Validate the skill (run quick_validate.py)
6. Iterate based on real usage and forward-test complex skills.
7. Save the skill (install a new skill or update an existing skill)

Follow these steps in order, skipping only if there is a clear reason why they are not applicable.

### Skill Naming

- Use lowercase letters, digits, and hyphens only; normalize user-provided titles to hyphen-case (e.g., "Plan Mode" -> `plan-mode`).
- When generating names, generate a name under 64 characters (letters, digits, hyphens).
- Prefer short, verb-led phrases that describe the action.
- Namespace by tool when it improves clarity or triggering (e.g., `gh-address-comments`, `linear-address-issue`).
- Before the first save, name a new skill folder exactly after the skill name. After reconciliation renames it to `skill-<id>`, retain that generated directory name; do not rename it back.

### Step 1: Understanding the Skill with Concrete Examples

Skip this step only when the skill's usage patterns are already clearly understood. It remains valuable even when working with an existing skill.

To create an effective skill, clearly understand concrete examples of how the skill will be used. This understanding can come from either direct user examples or generated examples that are validated with user feedback.

For example, when building an image-editor skill, relevant questions include:

- "What functionality should the image-editor skill support? Editing, rotating, anything else?"
- "Can you give some examples of how this skill would be used?"
- "I can imagine users asking for things like 'Remove the red-eye from this image' or 'Rotate this image'. Are there other ways you imagine this skill being used?"
- "What would a user say that should trigger this skill?"

To avoid overwhelming users, avoid asking too many questions in a single message. Start with the most important questions and follow up as needed for better effectiveness.

Conclude this step when there is a clear sense of the functionality the skill should support.

### Step 2: Planning the Reusable Skill Contents

To turn concrete examples into an effective skill, analyze each example by:

1. Considering how to execute on the example from scratch
2. Identifying what scripts, references, and assets would be helpful when executing these workflows repeatedly

Example: When building a `pdf-editor` skill to handle queries like "Help me rotate this PDF," the analysis shows:

1. Rotating a PDF requires re-writing the same code each time
2. A `scripts/rotate_pdf.py` script would be helpful to store in the skill

Example: When designing a `frontend-webapp-builder` skill for queries like "Build me a todo app" or "Build me a dashboard to track my steps," the analysis shows:

1. Writing a frontend webapp requires the same boilerplate HTML/React each time
2. An `assets/hello-world/` template containing the boilerplate HTML/React project files would be helpful to store in the skill

Example: When building a `big-query` skill to handle queries like "How many users have logged in today?" the analysis shows:

1. Querying BigQuery requires re-discovering the table schemas and relationships each time
2. A `references/schema.md` file documenting the table schemas would be helpful to store in the skill

To establish the skill's contents, analyze each concrete example to create a list of the reusable resources to include: scripts, references, and assets.

### Step 3: Initializing the Skill

At this point, it is time to actually create the skill.

Skip this step only if the skill being developed already exists. In this case, continue to the next step.

Create new skills directly under `/root/.codex/skills/remote-skills/<skill-name>`. Use the canonical checkout first. Do not substitute `/workspace/scratch`, `/tmp`, or another scratch directory.

When creating a new skill from scratch, always run the `init_skill.py` script. The script conveniently generates a new template skill directory that automatically includes everything a skill requires, making the skill creation process much more efficient and reliable.

Use the staged skill-creator script and the personal-skills checkout explicitly so the command is independent of the current working directory:

```bash
SKILL_CREATOR_DIR=/root/.codex/skills/oai/skill-creator
SKILLS_ROOT=/root/.codex/skills/remote-skills
skill_name=my-skill

python3 "$SKILL_CREATOR_DIR/scripts/init_skill.py" "$skill_name" \
  --path "$SKILLS_ROOT" \
  --resources scripts,references

test -f "$SKILLS_ROOT/$skill_name/SKILL.md"
```

Use only the resource directories the skill needs, and add `--examples` only when examples are useful. The postcondition must pass before continuing. If initialization returns an actual write or index-lock error, resolve `SKILLS_ROOT` to the writable personal-skills worktree or clone described above and retry there. Never validate a scratch copy in its place.

The script:

- Creates the skill directory at the specified path
- Generates a SKILL.md template with proper frontmatter and TODO placeholders
- Creates `agents/openai.yaml` using agent-generated `display_name`, `short_description`, and `default_prompt` passed via `--interface key=value`
- Optionally creates resource directories based on `--resources`
- Optionally adds example files when `--examples` is set

After initialization, customize the SKILL.md and add resources as needed. If you used `--examples`, replace or delete placeholder files.

Generate `display_name`, `short_description`, and `default_prompt` by reading the skill, then pass them as `--interface key=value` to `init_skill.py` or regenerate with:

```bash
python3 "$SKILL_CREATOR_DIR/scripts/generate_openai_yaml.py" \
  "$SKILLS_ROOT/$skill_name" --interface key=value
```

Only include other optional interface fields when the user explicitly provides them. For full field descriptions and examples, see references/openai_yaml.md.

### Step 4: Edit the Skill

Only personal skills resolved within `SKILLS_ROOT` or its top-level `uninstalled/` directory are editable. Edit the skill in its existing directory, using the writable-worktree fallback above only after an actual write failure. Filesystem writability of a materialized checkout is not evidence of ownership. If the user asks to edit any other skill, explain that it was provided directly by a plugin or OpenAI and cannot be edited.

When editing the (newly-generated or existing) skill, remember that the skill is being created for another instance of Codex to use. Include information that would be beneficial and non-obvious to Codex. Consider what procedural knowledge, domain-specific details, or reusable assets would help another Codex instance execute these tasks more effectively.

After substantial revisions, or if the skill is particularly tricky, you should use subagents to forward-test the skill on realistic tasks or artifacts. When doing so, pass the artifact under validation rather than your diagnosis of what is wrong, and keep the prompt generic enough that success depends on transferable reasoning rather than hidden ground truth.

#### Start with Reusable Skill Contents

To begin implementation, start with the reusable resources identified above: `scripts/`, `references/`, and `assets/` files. Note that this step may require user input. For example, when implementing a `brand-guidelines` skill, the user may need to provide brand assets or templates to store in `assets/`, or documentation to store in `references/`.

Added scripts must be tested by actually running them to ensure there are no bugs and that the output matches what is expected. If there are many similar scripts, only a representative sample needs to be tested to ensure confidence that they all work while balancing time to completion.

If you used `--examples`, delete any placeholder files that are not needed for the skill. Only create resource directories that are actually required.

#### Update SKILL.md

**Writing Guidelines:** Always use imperative/infinitive form.

##### Frontmatter

Write the YAML frontmatter with `name` and `description`:

- `name`: The skill name
- `description`: This is the primary triggering mechanism for your skill, and helps Codex understand when to use the skill.
  - Include both what the Skill does and specific triggers/contexts for when to use it.
  - Include all "when to use" information here - Not in the body. The body is only loaded after triggering, so "When to Use This Skill" sections in the body are not helpful to Codex.
  - Example description for a `docx` skill: "Comprehensive document creation, editing, and analysis with support for tracked changes, comments, formatting preservation, and text extraction. Use when Codex needs to work with professional documents (.docx files) for: (1) Creating new documents, (2) Modifying or editing content, (3) Working with tracked changes, (4) Adding comments, or any other document tasks"

Do not include any other fields in YAML frontmatter.

##### Body

Write instructions for using the skill and its bundled resources.

### Step 5: Validate the Skill

Once development of the skill is complete, validate the skill folder to catch basic issues early:

```bash
test -f "$SKILLS_ROOT/$skill_name/SKILL.md"
python3 "$SKILL_CREATOR_DIR/scripts/quick_validate.py" \
  "$SKILLS_ROOT/$skill_name"
```

The validation script checks YAML frontmatter format, required fields, and naming rules. Validation must target the same personal-skills directory that was initialized or resolved for editing; validation of a scratch copy does not satisfy this step. If validation fails, fix the reported issues and run the command again.

### Step 6: Iterate

After testing the skill, you may detect the skill is complex enough that it requires forward-testing; or users may request improvements.

User testing often this happens right after using the skill, with fresh context of how the skill performed.

**Forward-testing and iteration workflow:**

1. Use the skill on real tasks
2. Notice struggles or inefficiencies
3. Identify how SKILL.md or bundled resources should be updated
4. Implement changes and test again
5. Forward-test if it is reasonable and appropriate

#### Forward-testing

To forward-test, launch subagents as a way to stress test the skill with minimal context.
Subagents should *not* know that they are being asked to test the skill.  They should be treated as
an agent asked to perform a task by the user.  Prompts to subagents should look like:
  `Use @skill-x at /path/to/skill-x to solve problem y`
Not:
  `Review the skill at /path/to/skill-x; pretend a user asks you to...`

Decision rule for forward-testing:
  - Err on the side of forward-testing
  - Ask for approval if you think there's a risk that forward-testing would:
    * take a long time,
    * require additional approvals from the user, or
    * modify live production systems

  In these cases, show the user your proposed prompt and request (1) a yes/no decision, and
  (2) any suggested modifictions.

Considerations when forward-testing:
   - use fresh threads for independent passes
   - pass the skill, and a request in a similar way the user would.
   - pass raw artifacts, not your conclusions
   - avoid showing expected answers or intended fixes
   - rebuild context from source artifacts after each iteration
   - review the subagent's output and reasoning and emitted artifacts
   - avoid leaving artifacts the agent can find on disk between iterations;
     clean up subagents' artifacts to avoid additional contamination.

If forward-testing only succeeds when subagents see leaked context, tighten the skill or the
forward-testing setup before trusting the result.

### Step 7: Save the Skill

Before saving, confirm that the skill being staged is the same personal-skills directory that was initialized and validated:

```bash
test -f "$SKILLS_ROOT/$skill_name/SKILL.md"
git -C "$SKILLS_ROOT" status --short -- "$skill_name"
```

For each skill, stage only that skill directory from `SKILLS_ROOT`, commit, push, and verify reconciliation using the workflow above. Skill syncing is handled by Arcade; do not separately save the skill in the user's library. Do not say that a skill is created and installed until the push and post-reconciliation verification succeed, and do not report that the personal-skills directory is unwritable unless an actual write or index-lock operation failed.

For a new skill, reconciliation changes `<skill-name>` to `skill-<id>`; find the reconciled `SKILL.md` by its exact frontmatter name, verify the rewritten path, and use its `<id>` for the skill detail-page link. For an update, verify that the expected skill remains present.

Confirm the result to the user, summarizing what a new skill does and its structure or what changed in an existing skill, and link to the skill’s `SKILL.md` and its detail page in their directory; for a new skill, also say that it is installed and available in their skill directory.

If the operation still fails after the bounded retry, preserve the local skill files and explain that it could not be completed. Treat only a remote 403 as a likely shared-skill permission failure.

The user’s request to edit an existing skill is sufficient confirmation to update and push—you do not need to ask them for further confirmation.

## Uploaded Skill Installation Process

When the user asks to add, import, or install an uploaded skill—such as a ZIP, skill folder, `SKILL.md`, or related skill files:

1. Prepare and validate each skill.
2. Check that the skill does what it says it does. If discrepancies are found, flag them to the user and ask whether they want them fixed and the skill installed. If no discrepancies are found, continue without mentioning this check.
3. Place the skill directly under `"$SKILLS_ROOT"/<skill-name>`, starting with `SKILLS_ROOT=/root/.codex/skills/remote-skills`; do not use an ordinary scratch directory. Confirm that `"$SKILLS_ROOT"/<skill-name>/SKILL.md` exists.
4. Stage only that skill directory from `SKILLS_ROOT`, commit it, and push the repository's current branch.
5. For multiple skills, complete a separate save and push for each one.
6. For each installed skill, confirm to the user that it is available in their skill directory, summarize what it does and its structure, mention notable included files or capabilities, and link to its `SKILL.md` and the skill's detail page in their directory.

The user’s request to install the uploaded skill is sufficient confirmation. If the user uploads skill files without asking to install them, inspect or summarize them as requested but ask before permanent installation.

## After Installing a Skill

After synchronizing a newly created or installed skill, its directory will change from `<skill-name>` to `skill-<id>`. The system-message skill list and Skills UI may be stale in the current conversation. Locate the rewritten directory by matching its name in `"$SKILLS_ROOT"/*/SKILL.md` frontmatter and treat the verified remote-tree state as authoritative; a refresh or new conversation may be needed before the UI reflects it.

If the user explicitly asks to use a named skill that is not in the system message's skill list, search both `"$SKILLS_ROOT"/skill-*/SKILL.md` and `"$SKILLS_ROOT"/uninstalled/skill-*/SKILL.md` by frontmatter name. If found under `uninstalled/`, treat it as installed but disabled; otherwise, use it directly. If not, tell the user to check their skill directory and that recently installed skills may only be available in new conversations.

## User-facing language

- Only return a packaged ZIP when the user explicitly asks for one.
- Keep responses non-technical and refer to skills by their names; do not mention internal skill IDs.
- Never mention Arcade, Git, repositories, commits, pushes, or synchronization operations to the user when using Arcade. Use language such as **create**, **install**, **save**, **update**, **uninstall**, and **delete**.
- Never instruct the user to restart Codex. If a verified install or uninstall is not yet reflected in the Skills UI, suggest refreshing or reopening the Skills page.
- If an installation, update, uninstall, or deletion fails, explain it plainly without exposing internal identifiers or infrastructure errors.
- When linking directly to a skill’s detail page in the user’s directory, use `https://chatgpt.com/skills?skill_id=<id>`, where `<id>` is the skill’s directory name in `remote-skills` without the `skill-` prefix.
- When the user is on mobile, do not link to the skill directory or skill detail pages. Tell them to open the sidebar, select **Plugins**, then open the **Skills** tab.
