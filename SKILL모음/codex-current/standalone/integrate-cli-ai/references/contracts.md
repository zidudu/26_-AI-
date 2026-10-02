# Adapter lifecycle
Capability discovery must use the exact executable and environment later used by workers. Record executable path, version and supported flags without serializing the entire environment. Avoid an independent health-check path that passes while worker PATH resolves a different binary.

States: absent → installed unauthenticated → authenticating → ready; ready → busy → ready; failures distinguish auth_required, timeout, rate_limited, malformed_response, provider_error and cancelled. Prefer provider-supported status commands to guessing from files. A timeout is unknown status, not logged-out proof.

Invocation result: request_id, provider/model as actually reported, started/completed times, exit code, stdout artifact, redacted stderr, usage, parsed output, validation result and timeout phase. Map process exit errors even if some stdout is parseable. Drain pipes concurrently to prevent deadlock. Use process groups/job objects where available to contain descendants, not global taskkill by executable name.

Windows startup and process cleanup can outlast a synthetic executable's work. Set production timeouts from measurements and test thresholds from meaningful tolerances; do not solve a flaky test by silently shortening real auth status budgets.

Prefer preflight read-only operations before paid inference. User authorization for an analysis task can authorize the necessary inference, but an adapter smoke test should use synthetic responses unless real inference is needed/authorized.

Origin: V10 codex_auth.py, CLI provider adapter and analysis_stage.py. Original default model names and CLI flags are deliberately not copied as universal recommendations.
