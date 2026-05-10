# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** before disclosing publicly.

**Preferred:** GitHub Security Advisory — https://github.com/Amitshukla2308/omnigraph/security/advisories/new

**Alternative:** email amitshukla2308+security@gmail.com with the subject `[security] <short title>`.

We aim to acknowledge within 72 hours and provide a remediation timeline within 7 days.

## Scope

In scope:

- The OmniGraph Python package (`src/`) and CLI (`omnigraph`).
- The ingest path (transcript adapters under `src/sources/`).
- The compiler outputs and the file-drop contract (`docs/FILE_DROP_CONTRACT.md`).
- The ETL daemon (`scripts/etl_daemon.py`) and its pause/resume control plane.

Out of scope (file with the upstream project or operator):

- Provider transcript formats (Claude Desktop, Gemini CLI, Cline, Antigravity, ChatGPT exports). Format issues belong upstream.
- Python interpreter, `pip`, or third-party dependency vulnerabilities.
- LM Studio, llama.cpp, or any local-LLM runtime OmniGraph talks to over `/v1/chat/completions`.
- Operator-side filesystem permissions on `~/ai_conversations/` or `~/.informedvibe/og_artifacts/`.

## Data handling

OmniGraph is designed to process **your** AI-collaboration transcripts. That makes data-handling boundaries part of the security model:

- **Local-only.** OmniGraph has no telemetry. It does not call out to any hosted service except a local-LLM endpoint you configure (default: `http://localhost:1234/v1`, LM Studio compatible). No transcript content is sent anywhere else.
- **No bundled credentials.** OmniGraph never stores provider API keys. The local-LLM endpoint expects an unauthenticated or trivially-keyed connection on `localhost` / your LAN.
- **What ends up in `og_artifacts/`.** Compiled artifacts contain entity names, decisions, concerns, and rule patterns distilled from your transcripts. Sanitization levels (`named_stripped`, `entities_removed`, `aggregated`) exist in `src/compiler/sanitize.py` for share-safe outputs. The default profile is **not** share-safe — treat `~/.informedvibe/og_artifacts/` as personal data.
- **You are the sole custodian.** OmniGraph reads from `~/ai_conversations/` and writes to `~/.informedvibe/og_artifacts/`. Disk encryption, backup policy, and access control on those paths are your responsibility.

If you find a way for OmniGraph to exfiltrate transcript content over the network without explicit configuration, that's a security bug — report it.
