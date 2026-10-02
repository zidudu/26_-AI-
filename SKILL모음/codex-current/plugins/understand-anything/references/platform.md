# ChatGPT/Codex adaptation

The original analysis workflows, specialist-agent instructions, static-analysis source, Tree-sitter assets, core package, and dashboard are bundled under runtime/. The wrapper presents eight workflows through one Understand Anything entry. It does not register Claude slash commands or install an app on the user's PC.

setup.py rebuilds locked dependencies, core, and dashboard in a writable cache. Never modify the saved runtime bundle to add node_modules, generated dist files, project graphs, or credentials. Read the selected original workflow and replace root discovery with setup.py's returned path. Role names such as Bash or Task describe upstream tools; map them to the available shell and agent interfaces without inventing capability.

Default Korean requests to --language ko. Do not enable optional hooks or post-commit automation during setup. Use UNDERSTAND_NO_WORKTREE_REDIRECT=1 to keep the requested project as the analysis target. Respect session instructions for storing generated data and delivering cloud previews. A built dashboard alone does not prove that a target project has been fully analyzed.

The upstream Figma workflow is bundled but not configured or routed by default; it requires a separately requested, connected integration. MIT license is retained in runtime/LICENSE. See provenance.json for the exact source revision.
