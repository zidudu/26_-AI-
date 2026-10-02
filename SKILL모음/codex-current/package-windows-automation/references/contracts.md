# Distribution and acceptance
Use allowlisted paths, not a broad archive of a working user profile. Check hidden and nested files explicitly for state/config contents without printing secret values. Exclusion patterns alone are not proof of a clean package. Ensure old engine modules still imported by current code remain included.

Separate commands: setup (create venv, install requirements); preflight (platform/dependency/auth status); verify (offline tests); run (actual application). Each BAT should anchor cwd to its own directory and quote paths, propagate exit codes and present actionable failures. Document whether setup runs tests and whether Node/Office/CLI are separately required.

ZIP verification helper normalizes POSIX archive paths and rejects backslashes, absolute paths, drive-like paths, '..', duplicate files, encrypted entries and symlinks. It sets entry-count and total-uncompressed-size limits. It hashes streamed content; it does not execute files or prove malware absence. Source traversal rejects symlinks as well. Zip comparison requires exact file membership including empty files; directory-only entries carry no content identity.

Evidence levels: syntax/static; offline tests; mock integration; actual OS/runtime preflight; end-to-end data collection; rendered outputs visually checked; authorized mail submission/sent-item observation. State which levels passed on which build/environment. Do not label a Linux mock as a Windows end-to-end pass or a submitted message as confirmed delivery.

When updating README, compare claims to current source, defaults and observed results. Track screenshots by feature/version and ensure relative links resolve. Mention that schedules can skip busy/missed slots if that is actual behavior. Preserve original ZIP and source until equivalence is verified.

Origin: V10 source/ZIP distribution review, setup/verify scripts, docs and artifact hash verification. The helper operates locally and does not upload or publish a release.
