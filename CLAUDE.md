> ⚠ THIS IS THE PUBLIC OSS REPO.
> All commits push to github.com/Amitshukla2308/omnigraph.
> NEVER add personal data, real conversation transcripts, real `brain_state.json`,
> personal brain visualizations, founder names, customer names, `.env` content,
> or anything under `vault/`, `pilot/`, `meta_profiles/`.
> The `.gitignore` is the second line of defense, not the first.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What this project is

OmniGraph is a Python ETL pipeline. It ingests historical AI-collaboration
transcripts (Claude Desktop, Claude Code, Gemini CLI, Cline, Antigravity,
ChatGPT exports), distills them into a **3-layer brain** (global / personal /
project), and drops compiled artifacts under `~/.informedvibe/og_artifacts/`
for any compatible reader (Informed Vibe Atelier, Cursor, Continue.dev, or
your own).

## Run / dev

```bash
# Editable install — exposes the `omnigraph` CLI on $PATH
pip install -e .

# Or directly invoke the CLI from the repo
python src/omnigraph_cli.py status

# Status, harvest, compile, daemon
omnigraph status
omnigraph ingest
omnigraph pipeline --sessions pilot/qwen
omnigraph compile light_ir_global
omnigraph etl status
```

There is no test harness wired up yet. Adding one is welcome; a few `test_*.py`
files in `scripts/` exercise the GPU lock and the alias-table self-test.

## Architecture (the parts that span files)

```
                  ┌───────────────────────────┐
                  │ Provider transcript dirs  │  (Claude Desktop,
                  │  (read-only, host-local)  │   Gemini, Cline, etc.)
                  └────────────┬──────────────┘
                               │ src/sources/<provider>.py
                               ▼
                  ┌───────────────────────────┐
                  │ Stage 1: per-session JSON │  src/qwen_pipeline.py
                  │  (MentionEvents + facts)  │  (Qwen 5-phase extractor)
                  └────────────┬──────────────┘
                               │
                               ▼
                  ┌───────────────────────────┐
                  │ Stage 2: aggregation      │  src/stage2_aggregate.py
                  │  (global_profile.json,    │
                  │   Entity, CognitiveDual)  │
                  └────────────┬──────────────┘
                               │
                               ▼
                  ┌───────────────────────────┐
                  │ Compilers                 │  src/compiler/*
                  │  light_ir / claude_md /   │
                  │  cursor_rules / gemini /  │
                  │  boot_context / brain_view│
                  └────────────┬──────────────┘
                               │
                               ▼
                  ┌───────────────────────────┐
                  │ ~/.informedvibe/og_artifacts/  │  (the contract — any
                  │   global/   personal/   project/ │   reader can consume)
                  └───────────────────────────┘
```

The pipeline is **multi-phase grounded**: every claim extracted in Phase 1 is
verified against transcript turns in Phase 2 before downstream phases run.
That grounding loop is the load-bearing anti-hallucination step.

## Module map

- `src/omnigraph_cli.py` — the `omnigraph` entry point. All subcommands route here.
- `src/qwen_pipeline.py` — the 5-phase extractor that talks to a local LLM (LM Studio compatible OpenAI API).
- `src/stage2_aggregate.py` — cross-session rollup into `global_profile.json`.
- `src/compiler/` — projection compilers (light_ir, claude_md, cursor_rules, gemini_md, boot_context, brain_view) with sanitization levels.
- `src/sources/` — per-provider transcript adapters (claude_code, claude_desktop, gemini, cline, antigravity).
- `src/hr/` + `src/hr_adapter/` — relationship-graph math (co-change, communities, criticality).
- `src/canonical_slugs.py` / `src/canonicalize_canvas.py` — slug normalization. **Critical** for entity dedup across sessions.
- `src/gpu_lock.py` — single-GPU coordination (priority-based preemption, advisory file lock).
- `src/viz/` — brain-map renderer (SVG → PNG via cairosvg).
- `scripts/etl_daemon.py` — long-running ETL loop with on-disk pause/resume control.

## Data on disk

Input (read-only, host-local — not in this repo):

- Default: `~/ai_conversations/` (override with `OMNIGRAPH_AI_CONV`)
- On WSL2, provider sources also probe `/mnt/c/Users/$OMNIGRAPH_WIN_USER/...` for Cline / Antigravity / Claude Desktop dirs.

Output (the contract):

- `~/.informedvibe/og_artifacts/global/`   — `light_ir.xml`, `claude.md`, `cursor.rules`, `gemini.md`, `boot_context.json`
- `~/.informedvibe/og_artifacts/personal/` — per-founder mental moves, drift patterns
- `~/.informedvibe/og_artifacts/projects/<slug>/` — per-project entity facts

Working trees (gitignored — NEVER commit):

- `pilot/` — validation sandbox and operational output root
- `vault/` — per-entity Markdown pages
- `meta_profiles/` — interim aggregation state
- `brain_state*.json`, `brain_map*.png` — generated visualizations

## Conventions

- Discuss before code on non-trivial changes (open an issue).
- Run `python -m py_compile $(find src -name '*.py')` before pushing — keep the source parsing.
- Schema is locked at **v0.2.1** (see `docs/SCHEMA.md`). Schema changes require a migration plan in `src/migrate.py`.
- The 3-layer split (global / personal / project) is intentional — do not reintroduce a unified `brain.xml`. Mixing layers caused hallucinations in earlier iterations.
- Personal data is **input**, not source. Anything under `vault/`, `pilot/`, `meta_profiles/`, `brain_state*.json`, `brain_map*.png`, `amit-*`, `*-conversation.md` is ignored by git and must never be committed.

## Critical operational rules

- **Never load, swap, or unload models on the LM Studio server from automation.** It's single-GPU on most setups; mid-pipeline model changes risk OOM and trash the cache. Treat `/v1/models` as read-only metadata.
- **Don't undersize `max_tokens`.** Qwen 3 thinking models need `max_tokens >= 8192`; below that, the model returns `finish_reason: length` with empty content.
- **Slug normalization is load-bearing.** When editing `canonical_slugs.py` or `canonicalize_canvas.py`, preserve `.ts` suffixes and `/`-paths — historic bugs broke cross-linking.
- **All Qwen consumers go through the GPU lock** (`~/.omnigraph/gpu.lock`, built into `qwen_pipeline.qwen_call`). The ETL daemon runs at priority 9 (lowest); ad-hoc work preempts.

## When in doubt

- `README.md` for the public-facing what + why
- `docs/SCHEMA.md` for the v0.2.1 data model
- `docs/FILE_DROP_CONTRACT.md` for the output contract that downstream readers consume
- `SECURITY.md` for vuln reporting + data-handling boundaries
- `CONTRIBUTING.md` for the personal-data sweep checklist before sending a PR
