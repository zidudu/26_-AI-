# ChatGPT/Codex adaptation

The six original SKILL.md workflows are bundled under runtime/skills and routed by one installed Visual Skills entry. This does not register six separate Claude slash commands in ChatGPT. Use @Visual Skills with the requested workflow name in ordinary language.

The source, renderers, schemas, CSS, and shared guidance are retained. The wrapper replaces the ~/.claude/visual-skills symlink with a runtime path returned by setup.py. Dependencies are reconstructed from package-lock.json in a writable cache. The npm prepare hook is intentionally skipped to avoid changing Git hooks. Optional Excalidraw setup is not run; static D2 diagrams work without it. D2 Linux amd64 v0.9.0 is pinned and checksum-verified unless an existing local D2 is reused.

Read the selected upstream workflow before using it. Resolve its shell variables explicitly in each tool call. Use actual target-repository paths; upstream example machine paths are examples only. Cloud localhost addresses are not user-PC addresses.

Invoke renderers with `node --import tsx bin/<renderer>.ts`, not the tsx CLI. The CLI's IPC listener can fail with EPERM in hosted shells; the import loader has the same TypeScript behavior without that listener.

Upstream MIT license is retained in runtime/LICENSE. See provenance.json for the exact source revision. Generated artifacts must follow the session's persistent storage rules.
