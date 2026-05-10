# OmniGraph → Atelier file-drop contract

_Written 2026-04-24. This document specifies what OmniGraph drops into Atelier's filesystem, and what Atelier backend + frontend must build to consume those drops. Pattern C (file drop) is the current integration mode; Pattern A (worker subprocess) comes later after PoC._

## Layering reminder

OmniGraph is the **upstream supplier**. It lives at `~/projects/omnigraph/` (separate git repo: `https://github.com/the foundershukla2308/human-ai-brain-graph`). It extracts meta-learning from historical AI-coding sessions, aggregates cross-session patterns, and writes compiled artifacts to Atelier's filesystem. Atelier is the **downstream consumer**. It reads those artifacts and surfaces them in its UI + system-prompt stack.

No Python runs inside Atelier. No TypeScript runs inside OmniGraph. The contract is filesystem paths + file schemas.

## Drop locations (under `~/atelier/`)

All drops land inside the Atelier project tree. OmniGraph owns these subtrees and may overwrite files in them; Atelier owns everything else.

```
atelier/
├── data/                                      Atelier runtime data root
│   ├── atelier.db                             SQLite (users, orgs, sessions, ...)
│   ├── sessions/<sid>/                        Atelier-owned PTY transcripts
│   │   └── raw.log                            (OmniGraph reads for reflect)
│   └── users/<atelier_user_id>/               ← canonical user root (PTY HOME target)
│       ├── .claude/                           Atelier-owned (HOME isolation)
│       ├── data/
│       │   └── events/<YYYY-MM>.jsonl         raw MentionEvent stream (user-scoped;
│       │                                      each record carries `project: <slug>`)
│       └── brain/personal/                    compiled + authored Personal Brain
│       ├── _meta.json                         drop metadata (when, schema_version, counts)
│       ├── global_profile.json                full Stage-2 aggregate (source of truth)
│       ├── compiled/                          consumer-shaped projections
│       │   ├── light_ir.xml                   system-prompt injection (load-bearing)
│       │   ├── claude.md                      CLAUDE.md-style markdown
│       │   ├── boot_context.json              structured cards for onboarding / PPF
│       │   ├── cursor.rules                   .cursorrules format
│       │   └── gemini.md                      GEMINI_SYSTEM_MD format
│           ├── entities/                      per-entity Vault pages (Obsidian-style)
│           │   ├── <slug>.md                  one page per canonical target_id
│           │   └── INDEX.md                   alphabetical index
│           ├── events/
│           │   └── index.json                 compiled index (target_id → [(ts, file, line)])
│           │                                  raw JSONL lives under data/events/ above
│           └── graph/                         (optional) HR structural signals
│               ├── cochange.json
│               ├── communities.json
│               └── criticality.json
│
└── projects/<ProjectName>/
    ├── canvas/                                Atelier-owned; OmniGraph reads for dedup
    │   └── nodes/<node_id>.json               NodeMeta: {raw_title, slug_canonical, canonicalized_at, …}
    ├── sessions/<sid>.md                      6-lens reflection (OmniGraph writes)
    ├── domain_brain/                          shared across users in the project
    │   ├── <kind>.md                          authored (founder or agent)
    │   ├── <kind>.draft.md                    OmniGraph-proposed update; Atelier banners on detection
    │   └── history/                           Atelier-owned on accept; OmniGraph never touches
    └── brain/personal/                        LEGACY Phase A — symlink after `omnigraph migrate`
                                               points to data/users/<uid>/brain/personal/
```

Schemas for each file are below.

## What Atelier needs to build (backend)

### Required (for Pattern C to be useful)

| Module | Path | Responsibility |
|---|---|---|
| **brain reader** | `backend/src/brain/personal.ts` | Read `projects/<P>/brain/personal/global_profile.json` on demand. Cache. Invalidate on `_meta.json.mtime` change. |
| **compiled-prompt injector** | extend `backend/src/agent/boot-prompts.ts` | On every `newProjectBootPrompt` / `resumeBootPrompt` call, if `projects/<P>/brain/personal/compiled/light_ir.xml` exists, prepend its contents inside an `[atelier orchestration]` envelope block. One-line change; light-IR is already token-bounded. |
| **entity lookup** | `backend/src/brain/entity.ts` | Resolve `<canonical_slug>` → full entity page (markdown body from `entities/<slug>.md`). Used by Canvas tooltips, related-node badges. |
| **MCP tools** | `backend/src/mcp/brain-tools.ts` | Expose three read-only MCP tools to the CLI subprocess: <br>• `brain.getEntity(slug)` → markdown body + frontmatter<br>• `brain.topMoves(n=5)` → confirmed mental moves<br>• `brain.openConcerns()` → latent_unresolved concerns with age |
| **reflection-worker hook** | extend `backend/src/session/reflection-worker.ts` | After reflect artifact is written, trigger OmniGraph regeneration (initially a shell-out via `omnigraph pipeline --sessions <atelier_sessions_path>`; Pattern A later). Fire-and-forget; reflection doesn't block on it. |

### Optional (Pattern A migration — not now)

`backend/src/brain/omnigraph-worker.ts` — spawn Python worker, stream progress to WS, update UI. Build when file-drop PoC is proven.

## What Atelier needs to build (frontend)

| View | Path | Responsibility |
|---|---|---|
| **Personal Brain view** | `frontend/src/views/PersonalBrain.tsx` | Render `entities/INDEX.md` as a sortable list. Click entity → render `entities/<slug>.md` body. Filter by status (active/dormant/archived), type (Project/Tool/Decision/Concern), provider. |
| **Product Placement Flow cards** (optional) | `frontend/src/views/ProductPlacement.tsx` | On new-project boot, read `compiled/boot_context.json` and render its `cards` as a 6-phase interactive flow (per `omnigraph_product_placement/02_METHODOLOGY_NARROW_FIRST.md`). Skip + "complete later" options. |
| **Canvas node decoration** | extend `frontend/src/views/Canvas.tsx` | For a selected Canvas node matching a canonical slug, show a side panel with: mention count, first/last seen, dominant valence, top co-mentioned, link to `entities/<slug>.md`. |
| **Health strip chip** | extend existing header | Show `omnigraph@<tag>` + last-updated timestamp. One-line badge so the founder knows the substrate's freshness. |

## File schemas (what OmniGraph guarantees)

### `_meta.json`

```json
{
  "schema_version": "0.2.1",
  "omnigraph_version": "2026-04-25-v0.4.1",
  "built_at": "2026-04-24T12:34:56Z",
  "sessions_ingested": 248,
  "mention_events": 6806,
  "distinct_targets": 3668,
  "compilers_emitted": ["light_ir", "claude_md", "boot_context", "cursor_rules", "gemini_md"],
  "sanitize_level": "none"
}
```

### `global_profile.json`

Canonical shape — see `omnigraph/docs/SCHEMA.md` §8.4. Top-level keys:

- `scale` — `{sessions, providers, total_mention_events, total_deltas}`
- `inference_p1_convergence_vs_abandonment` — list
- `inference_p3_decision_load_bearing` — list
- `inference_p5_concern_lifecycle` — list
- `inference_p6_cross_provider_bleed` — list
- `inference_idea_resurrection` — list (new, temporal)
- `inference_decision_half_life` — list (new, temporal)
- `inference_concern_lifetime` — list (new, temporal)
- `inference_provider_cognition` — list (new, temporal)
- `confirmed_mental_moves` — list (`move`, `owner`, `level`, `occurrences`)
- `candidate_mental_moves_single_session` — list
- `entity_frequency_top30` — list (`target_id`, `type`, `events`, `providers`)
- `drift_recurrence_by_trigger` — list
- `rules_collected` — list (`rule_text`, `applies_to`, `level`, `session`, `provider`)
- `affect_events` — list
- `stances_collected_count` — int

### `compiled/light_ir.xml`

XML-tagged compact format. Example:

```xml
<user-profile v="0.2.1">
<mm l="gen" o="user">state-of-reality-audit-before-planning</mm>
<rule>generalist-retrieval fails → pivot to domain MCP tool</rule>
<concern r="recurring">desktop-commander-read-file [t_last: 2026-04-08, n_raised: 3]</concern>
<ent-top n="5">fastbrick:Proj atelier:Proj zeroclaw:Tool carlsbert:Proj kimi:Tool</ent-top>
</user-profile>
```

Tag vocabulary + semantics: `omnigraph/omnigraph_product_placement/05_LIGHT_IR_OUTPUT_FORMAT.md`.

Token budget: 2000 by default. Atelier can re-request at different budgets by invoking OmniGraph's compile CLI with `--max-tokens N`.

### `compiled/claude.md`

YAML-frontmatter markdown. Sections: `## Mental moves`, `## Standing rules`, `## Latent/unresolved concerns`, `## Load-bearing decisions (active)`, `## Recurring drift triggers`, `## Top entities`, `## Concerns open longest`, `## Ideas recently resurrected`, `## Decisions showing thrashing`.

### `compiled/boot_context.json`

```json
{
  "schema": "0.2.1",
  "source": "omnigraph",
  "scale": { "...": "..." },
  "cards": {
    "mental_moves": [ {"move": "...", "level": "gen", "owner": "user", "occurrences": 3 } ],
    "rules": [ {"rule": "...", "applies_to": "...", "level": "..."} ],
    "latent_concerns": [ {"target_id": "...", "raised_count": 3, "type": "Tool"} ],
    "drift_warnings": [ {"trigger": "...", "count": 3} ],
    "load_bearing_decisions": [ {"target_id": "...", "sessions_referenced": 5} ],
    "top_entities": [ {"target_id": "...", "type": "Project", "events": 47, "providers": ["claude_code", "antigravity"]} ]
  }
}
```

### `entities/<slug>.md`

Obsidian-compatible. YAML frontmatter includes `target_id`, `target_type`, `aliases`, `first_seen`, `last_seen`, `mention_count`, `providers`, `co_mentioned_top`, `status`. Body has `## Summary`, `## Load-bearing decisions`, `## Concerns`, `## Rules touching this entity`, `## Mention log`. Backlinks use `[[session_<sid>]]` — these point to Atelier's session artifacts if present, otherwise resolve to Atelier's sessions view by sid.

### `events/<YYYY-MM>.jsonl`

One MentionEvent per line, timestamp-sorted within the month. Shape:

```json
{"ts":"2026-04-24T06:15:00Z","session_id":"...","provider":"claude_code","target_id":"zeroclaw","target_type":"Tool","mention_type":"reference","authorship":"user","valence":"confident","evidence_quote":"...","mentioned_as":"zeroclaw-mcp"}
```

`events/index.json` — `target_id → [{ts, file, line}]` for O(log N) lookups.

### `graph/*.json` (optional, Pattern C opt-in)

- `cochange.json` — `{meta, edges: {target: [{module, weight}]}}`
- `communities.json` — `{meta, communities: {id: {size, label, members}}, module_to_community: {target: id}}`
- `criticality.json` — `{meta, modules: {target: {score, rank, signals, reasons, providers}}}`

## Lifecycle contract

### When OmniGraph writes

- **Initial bootstrap:** once, `omnigraph pipeline --sessions ~/ai_conversations/... --out ~/atelier/projects/<P>/brain/personal/` populates everything.
- **On session end:** Atelier's `reflection-worker` invokes `omnigraph ingest-session <sid>` + `omnigraph aggregate --incremental` + `omnigraph compile --all`. OmniGraph writes atomically (tmp file + rename) so Atelier never reads a partial file.
- **On founder edit:** if the founder edits `entities/<slug>.md` in Atelier's UI, OmniGraph treats it as authoritative — next rebuild preserves user edits via frontmatter field `founder_edited: true` (schema addition forthcoming).

### When Atelier reads

- **Boot prompt assembly:** on every CLI spawn (`agent/boot-prompts.ts`).
- **Personal Brain view:** on view mount + on filesystem change event.
- **Canvas node click:** on demand.
- **MCP tool call from agent:** on demand.

## Versioning

OmniGraph tags releases `omnigraph-YYYY-MM-DD-vN`. The tag is written to `brain/_omnigraph_version.txt`. Atelier can check this on boot and warn if major-version skew between expected and installed. Schema-breaking changes (rare) bump to a new major; Atelier's reader code pins a compatible range.

## Failure modes Atelier must handle

1. `brain/personal/` doesn't exist → fallback: render Atelier's existing prompt without OmniGraph injection. No error.
2. `compiled/light_ir.xml` missing or malformed → fallback: use `compiled/claude.md`. If both missing, fall through to no injection.
3. `global_profile.json` present but empty (new founder, no history) → render Personal Brain view with a "No extracted history yet — run `omnigraph pipeline`" CTA. No broken UI.
4. `events/` or `entities/` missing → the compiled/ files still work; graph decoration and entity lookup are just unavailable.

## Out of scope for this contract

- UI for **running** OmniGraph from inside Atelier (Pattern A migration — later).
- Domain Brain generation (OmniGraph's `src/domain_brain/` is separate; it writes to `projects/<P>/domain_brain/`, not `brain/personal/`). See companion contract when that lands.
- Multi-founder / team sharing (sugar ladder sanitization exists in OmniGraph — `sanitize=named_stripped` etc. — but cross-founder UX is a separate design).
- Real-time PTY streaming (for now, extraction happens at session end via reflection-worker).

## CLI commands (v0.2+)

All subcommands honor `--atelier-root <path> --user-id <uuid>`. When both are
given, OmniGraph writes to the canonical user-scoped layout. When absent,
falls back to local `pilot/` (single-user dev mode).

```bash
# Bootstrap a user's Personal Brain from their Qwen-extracted sessions.
omnigraph pipeline \
  --sessions pilot/qwen --sessions pilot/full \
  --atelier-root ~/atelier --user-id <uuid>

# Compile a system-prompt block into Atelier's canonical path.
omnigraph compile light_ir --atelier-root ~/atelier --user-id <uuid>
omnigraph compile claude_md --atelier-root ~/atelier --user-id <uuid>
omnigraph compile boot_context --atelier-root ~/atelier --user-id <uuid>
# (any compile subcommand auto-resolves the output path to
#  atelier/users/<uuid>/brain/personal/compiled/<target>.<ext>)

# Move legacy Phase A project-scoped brain into the user-scoped canonical location.
# Idempotent. Leaves a symlink at the old path during transition.
omnigraph migrate --atelier-root ~/atelier --user-id <uuid>
omnigraph migrate --atelier-root ~/atelier --user-id <uuid> --dry-run

# Post-hoc Canvas slug reconciliation (idempotent, no Qwen needed).
# Scans atelier/projects/<P>/canvas/nodes/*.json and fills in
# slug_canonical + canonicalized_at fields via the alias table.
omnigraph canonicalize --atelier-root ~/atelier --project MyProject
omnigraph canonicalize --atelier-root ~/atelier --project MyProject --dry-run

# Gap audit of Atelier's project domain_brain/ (scores coverage, flags gaps).
omnigraph domain-brain --project-root ~/atelier/projects/MyProject
omnigraph domain-brain --project-root ~/atelier/projects/MyProject --json
```

## How to validate the contract

Once Atelier builds the backend reader + at least one consumer (Personal Brain view OR boot-prompt injection), run:

```bash
cd ~/projects/omnigraph
# End-to-end bootstrap into Atelier's canonical layout:
python3 src/omnigraph_cli.py pipeline \
  --sessions pilot/qwen --sessions pilot/full \
  --atelier-root ~/atelier --user-id "$(cat ~/atelier/data/current_user_uuid 2>/dev/null || echo default)"
python3 src/omnigraph_cli.py compile light_ir \
  --atelier-root ~/atelier --user-id "$(cat ~/atelier/data/current_user_uuid 2>/dev/null || echo default)"
```

Boot Atelier against MyProject. Confirm: (a) system prompt contains the `<user-profile>` block, (b) Personal Brain view lists the top entities, (c) no crash when `brain/personal/` is emptied.

---

**Point person on the OmniGraph side:** this doc, plus `omnigraph/docs/SCHEMA.md`, plus `omnigraph_product_placement/05_LIGHT_IR_OUTPUT_FORMAT.md`.

**Questions / schema requests from Atelier side:** file an issue at https://github.com/the foundershukla2308/human-ai-brain-graph/issues.

---

## Multi-user evolution

### OmniGraph is auth-agnostic

OmniGraph never touches the Claude CLI, Anthropic API, or any provider auth. Extraction runs on local Qwen3.6 via LM Studio; compilation is deterministic text templating. **Whose Claude account funded the token spend is irrelevant to OmniGraph.** What matters to OmniGraph is **whose cognitive substrate a session builds** — i.e. the Atelier-user-id — which is independent of Claude auth.

That distinction is load-bearing. Today (Phase A, single-founder-per-machine) Atelier implicitly treats the one human on disk as the sole owner. Any move toward multi-founder — options 0/1/2/3 from the auth discussion — converges on the same requirement on OmniGraph's side: **index Personal Brain by `atelier_user_id`, not by project and not by Anthropic account**.

### Session metadata addition (required before Phase B)

Every session artifact Atelier writes must carry:

```json
{
  "session_id": "...",
  "atelier_user_id": "amit",          // NEW — stable per human, not per auth
  "project": "MyProject",
  "provider": "claude_code",
  "claude_account": "subscription-owner-acct-id",   // optional, for audit
  // ...
}
```

Without `atelier_user_id`, OmniGraph cannot correctly segregate Personal Brain in a multi-user future. Field is optional in Phase A (defaults to `"default"`); required in Phase B.

### Evolved drop layout

```
atelier/
├── projects/<ProjectName>/
│   ├── canvas/                        ← project-scoped, unchanged
│   ├── domain_brain/                  ← project-scoped, unchanged
│   │                                     (industry facts are shared across users)
│   └── sessions/<sid>.json            ← must carry atelier_user_id
│
└── users/<atelier_user_id>/           ← NEW canonical Personal Brain location
    └── brain/personal/                ← this user's cognitive substrate
        ├── _meta.json
        ├── global_profile.json
        ├── compiled/{light_ir.xml, claude.md, boot_context.json, cursor.rules, gemini.md}
        ├── entities/
        ├── events/
        └── graph/
```

The path `projects/<P>/brain/personal/` from Phase A remains **deprecated but readable** during transition. OmniGraph writes to both for one release, so Atelier's Phase A reader keeps working while Phase B code comes online.

### Migration sequence

1. **Phase A (now, single founder).** OmniGraph writes under both `projects/<P>/brain/personal/` and `users/<user_id>/brain/personal/`. Atelier's reader can consume either. `atelier_user_id` defaults to `"default"` — a named slot that will become the founder's real id.
2. **Phase A.1 (before Phase B lands).** Atelier adds the `atelier_user_id` field to session.json. OmniGraph stops writing the legacy project-scoped path; all reads now resolve via `users/<id>/`.
3. **Phase B (multi-founder).** Each Atelier user authenticates (SSO / per-user API key / per-user CLI isolation — Atelier's choice). Atelier's reader gates Personal Brain view by current session's `atelier_user_id`. Only Domain Brain is shared across users in a project.
4. **Cross-user visibility (optional).** If one user needs to see the *shape* of another user's collaboration patterns (onboarding, team health), the sanitize levels (`named_stripped` / `entities_removed`) already exist in OmniGraph. Atelier wires a "team view" that compiles at `sanitize=named_stripped` — user-A sees user-B's mental moves + drift patterns, not her specific project entities or concerns.

### What Atelier owes the migration

- Decide on the `atelier_user_id` namespace (lowercase slug? uuid? email-hash?). Recommend: short slug, human-readable, stable-per-human (not per-login).
- Add the field to `sessions/<sid>.json` writer in `backend/src/session/`.
- Plumb it through `reflection-worker.ts` so it's available when OmniGraph is invoked.
- Gate Personal Brain views in the frontend by current user.

### What OmniGraph owes the migration

- Accept `--user-id <slug>` on all ingest / aggregate / compile commands; default to `"default"` in its absence.
- Write to `users/<user_id>/brain/personal/` (canonical) and `projects/<P>/brain/personal/` (legacy mirror) for one release.
- Provide a one-command migration: `omnigraph migrate --from projects --to users --user-id <slug>` that moves a legacy Personal Brain into the new layout.
- Never cross-contaminate: canonicalize_slugs / global_profile / entities are strictly per-user; a target_id named `"fastbrick"` in the founder's brain and `"fastbrick"` in user-B's brain are independent records with independent mention histories.

### Claude-auth specifically (for completeness)

Whatever Atelier chooses for auth (options 0/1/2/3 in the earlier discussion), OmniGraph is unaffected at the code level. The only integration touchpoint is: **does the session artifact tell OmniGraph which human this was?** If yes, Personal Brain segregation works regardless of whose Claude account paid the bill. If no, OmniGraph cannot correctly segregate and will collapse multi-user traffic into one brain — silent wrong-answer mode, worst possible failure.

**Recommended:** `atelier_user_id` lands in session.json **before** Phase B onboards a second human, not on the day Phase B ships. The cost is one field; the cost of retrofitting is re-extracting every historical session to re-attribute ownership.

---

## v0.4.1 contract drift (added 2026-04-25 — Turn 19)

The following changes since the original spec are now part of the contract. They were validated against the 24/24-passing `scripts/test_atelier_integration.py` suite on 2026-04-25 and against a real publish into `~/atelier/data/users/<uid>/brain/personal/compiled/`.

### Reflection frontmatter additions
Reflection markdown under `atelier/projects/<P>/sessions/<sid>.md` now carries:
- `lens_count: <int>` — number of lenses used in synthesis (default 6).
- `produced_by: "omnigraph/reflect (qwen-3.6-35b-a3b)"` — provenance string.

Atelier's reader (`frontend/src/Reflection.tsx`) already parses these and renders the metadata strip.

### Ring vocabulary (Atelier-side)
Atelier renamed Project > **Theme** > **Story** > Task > Subtask. Track / Module are deprecated but still accepted in OmniGraph extractions for backwards-compatibility with historical sessions. New `NodeMeta` fields surfaced in extractions where applicable: `priority` (P0–P3, distinct from confidence), `cycle`, `outcome`, `target_date`. New node kind `Milestone` and edge kind `ships-in`.

OmniGraph does NOT need to round-trip these into compiled artifacts in v0.4.1 — they live in the project canvas, not in Personal Brain. If the founder asks downstream for ring-aware projections, that's a future compiler target.

### MCP tool addition (Atelier-side)
`project_complete_onboarding(target_ship_date)` flips a project's stage from `"onboarding"` → `"pre-mvp"`. OmniGraph does not call this; it is informational.

**Implication for OmniGraph extractions**: when a session has `atelier_user_id` set AND project stage is `"onboarding"`, the extracted `decisions/themes/risks` are formative rather than refinements. Stage-2 should optionally weight these differently (deferred — TODO-4 territory).

### Per-user CLI HOME isolation (Atelier-side)
Atelier scopes `HOME` per non-legacy user so their `.credentials.json` and CLI state don't collide. OmniGraph reads `atelier_user_id` from session.json regardless of whose `HOME` produced the session, so this is transparent to us.

### Continuous-publish flow (Phase 0 daemon)
The ETL daemon (`scripts/etl_daemon.py`) calls a post-ETL hook on every successful provider batch:
1. `omnigraph aggregate --full --indir pilot/full --state pilot/full`
2. Compile each target locally to `pilot/full/compiled/`
3. For each `~/atelier/data/users/<uid>/`, compile each target with `--atelier-root ~/atelier --user-id <uid>` so artifacts land in `brain/personal/compiled/`.

Atelier should expect the brain to refresh on a poll interval (currently 600s default). No filesystem-watcher events are emitted — Atelier polls the directory mtime or reads on every boot.

### Required `--state` argument when atelier-root is used
Compile commands invoked with `--atelier-root` write to the user's brain dir but READ from `--state`. If `--state` is omitted, the compiler reads from the (likely empty) target user dir and emits a near-empty artifact. **Always pass `--state pilot/full` (or wherever the canonical aggregate lives) when publishing to Atelier.** This is enforced in `scripts/etl_daemon.py:post_etl_hook`.
