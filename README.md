# OmniGraph

**Your AI-collaboration cortex, visualized.**

OmniGraph is a Python ETL pipeline. It reads the conversation transcripts your AI coding tools leave on disk, distills them into a structured **3-layer brain**, and drops compiled artifacts that any compatible tool can read at session boot — so you stop re-explaining yourself to every new chat window.

- [Why](#why)
- [What it is](#what-it-is)
- [What's in v0](#whats-in-v0)
- [Install](#install)
- [How to use](#how-to-use)
- [Output contract](#output-contract)
- [Reading the artifacts](#reading-the-artifacts)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## Why

Chat windows forget. Every new Claude / Gemini / Cline / Antigravity session starts cold: you re-paste the same standing rules, the same project context, the same "this is what I tried last week and it didn't work."

The fix isn't a bigger context window — it's a substrate. Your past sessions already contain the knowing. They're sitting on disk in five different formats. OmniGraph turns them into one structured, queryable brain that future sessions read at boot.

This is the **writer**. Any tool that reads `~/.informedvibe/og_artifacts/` is a valid **reader**.

## What it is

```
                  ┌───────────────────────────┐
                  │ Provider transcript dirs  │  (Claude Desktop / Code,
                  │  (read-only, host-local)  │   Gemini CLI, Cline,
                  └────────────┬──────────────┘   Antigravity, ChatGPT exports)
                               │
                               ▼
                  ┌───────────────────────────┐
                  │ Stage 1: per-session JSON │  Local LLM (LM Studio
                  │  (MentionEvents + facts)  │   compatible) — Qwen 3
                  └────────────┬──────────────┘   thinking model, 5 phases
                               │
                               ▼
                  ┌───────────────────────────┐
                  │ Stage 2: aggregation      │  Pure Python — no LLM
                  │  (global_profile.json)    │   in the cross-session
                  └────────────┬──────────────┘   rollup
                               │
                               ▼
                  ┌───────────────────────────┐
                  │ Compilers                 │  light_ir.xml, claude.md,
                  │  (sanitization-aware)     │   cursor.rules, gemini.md,
                  └────────────┬──────────────┘   boot_context.json
                               │
                               ▼
                  ┌───────────────────────────────┐
                  │ ~/.informedvibe/og_artifacts/ │   The contract.
                  └───────────────────────────────┘
```

**Key design choices:**

- **Local-only.** No telemetry. The only network call is to a local-LLM endpoint you configure (default `http://localhost:1234/v1`, LM Studio compatible).
- **Grounded extraction.** Every claim Phase 1 extracts is verified against transcript turns in Phase 2 before it survives. That loop is the load-bearing anti-hallucination step.
- **3-layer split.** Global (cross-project habits), Personal (one founder's mental moves), Project (per-project facts) are separate files. Mixing them caused hallucinations in earlier iterations.
- **Schema-locked.** Data model is v0.2.1 (see `docs/SCHEMA.md`). Schema changes ship with migrations.

## What's in v0

Concrete capabilities that work today:

- **Harvest.** Symlinks/mirrors transcripts from five providers into a canonical `~/ai_conversations/` layout. WSL2-aware (probes `/mnt/c/Users/$OMNIGRAPH_WIN_USER/...` for Windows-host stores).
- **Extract.** A 5-phase Qwen pipeline (narrow extract → ground → critique → synthesize → assemble) produces per-session JSON conforming to the v0.2.1 schema.
- **Aggregate.** Cross-session rollup into `global_profile.json` with entity dedup, decision history, drift tracking, and supersession heuristics.
- **Compile.** Six projection targets:
  - `light_ir_global` — ~2000-token system-prompt injection
  - `light_ir_personal` — per-founder mental moves
  - `light_ir_project` — per-project entity facts
  - `claude_md`, `cursor_rules`, `gemini_md` — provider-shaped configs
  - `boot_context` — generic JSON for any reader
  - `brain_view` — UI-shaped JSON for a brain visualizer
- **Daemon.** A long-running ETL loop (`scripts/etl_daemon.py`) with on-disk pause/resume and a priority-based GPU lock so ad-hoc work preempts the daemon.
- **Visualize.** A brain-map renderer (`src/viz/`) emits an SVG/PNG of decisions, concerns, and entities across anatomical regions.

What v0 explicitly does **not** do: hosted multi-tenant service, web UI, cloud sync, model-agnostic extraction (it expects Qwen 3 family or another thinking model with ≥262k context).

## Install

Requires **Python 3.10+** and a local LLM endpoint that speaks OpenAI-compatible chat completions (LM Studio, llama.cpp `--api`, vLLM, Ollama with the OpenAI shim — anything reachable at `http://<host>:1234/v1`).

```bash
git clone https://github.com/Amitshukla2308/omnigraph.git
cd omnigraph
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If editable install errors on a missing `pyproject.toml` (one is on the v0.1 milestone), run the CLI directly:

```bash
python src/omnigraph_cli.py --help
```

Optional: `cairosvg` for PNG export from the visualizer.

```bash
pip install cairosvg
```

### Configure the LLM endpoint

Copy `.env.example` (if present) or export directly:

```bash
export QWEN_BASE_URL="http://localhost:1234/v1"
export QWEN_MODEL="qwen3-thinking"   # or whatever your endpoint reports under /v1/models
```

## How to use

```bash
# 1. Harvest transcripts into the canonical input layout
omnigraph ingest

# 2. Run the pipeline on a batch of sessions
omnigraph pipeline --sessions pilot/qwen

# 3. Compile artifacts to the default output root
omnigraph compile light_ir_global
omnigraph compile claude_md
omnigraph compile cursor_rules

# 4. (Optional) Run the daemon in the background
python scripts/etl_daemon.py \
  --providers gemini_cli claude_desktop cline antigravity \
  --interval 600

# 5. Check GPU-lock and daemon state
omnigraph gpu status
omnigraph etl status
```

Output lands at `~/.informedvibe/og_artifacts/{global,personal,projects}/...`.

## Output contract

OmniGraph writes to `~/.informedvibe/og_artifacts/` (override via `--output-root` on most subcommands). Layout:

```
og_artifacts/
├── global/
│   ├── light_ir.xml              ~2000-token system-prompt injection
│   ├── claude.md                 Claude-shaped session config
│   ├── cursor.rules              Cursor IDE rules file
│   ├── gemini.md                 Gemini CLI shape
│   └── boot_context.json         generic JSON for any reader
├── personal/
│   ├── global_profile.json       Stage-2 source of truth
│   ├── _meta.json                schema version, counts, generated_at
│   └── compiled/
│       └── light_ir.xml          personal mental moves only
├── projects/
│   └── <project-slug>/
│       └── brain.xml             project-scoped facts
└── entities/
    └── <slug>.md                 per-entity Vault pages (optional)
```

Full spec: [`docs/FILE_DROP_CONTRACT.md`](./docs/FILE_DROP_CONTRACT.md).

Schema: [`docs/SCHEMA.md`](./docs/SCHEMA.md) (locked at v0.2.1).

## Reading the artifacts

Any tool that reads `~/.informedvibe/og_artifacts/` is a valid reader. OmniGraph itself only writes.

- **Informed Vibe Atelier** ([atelier-oss](https://github.com/Amitshukla2308/informed-vibe-atelier-prod)) — a founder-facing UI + agent runtime that injects `light_ir.xml` into its CLI subprocess at session boot. See its `docs/BRAIN_INTEGRATION.md`.
- **Cursor / Continue.dev / Aider** — point them at the provider-shaped configs (`claude.md`, `cursor.rules`, `gemini.md`).
- **Your own reader.** The format is documented; the boot_context.json is a flat-ish JSON anyone can consume. Build what you need.

## Roadmap

Short and honest. No v3 cloud-tenant fantasies.

- **v0.0.1 (this release).** Source code published clean. Local-only, single-founder, CLI-driven. Schema v0.2.1 locked.
- **v0.1.** `pyproject.toml` so `pip install omnigraph` works directly. CI for the `py_compile` smoke + the canonical-slug self-test. One worked example with synthetic transcripts so contributors can run the full pipeline without their own data.
- **v0.2.** Provider adapters re-shaped behind a small interface so a new provider (ChatGPT export JSON, Replit Agent, etc.) is a single file. Extraction-model abstraction so non-Qwen thinking models can drive Stage 1.

Anything beyond v0.2 is unfunded speculation — propose it in an issue if you have a use case.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). The personal-data sweep is mandatory before every PR — OmniGraph processes personal data as input, so contributor hygiene matters more than usual.

By contributing you agree your changes are licensed under Apache 2.0. No CLA.

## License

[Apache 2.0](./LICENSE).
