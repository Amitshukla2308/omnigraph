# OmniGraph Extraction Schema (v0.2.1, locked for pilot)

**Status:** v0.2.1 locked for oracle + Qwen pilot runs. Evolved from v0.1 after first oracle extraction revealed a conflation of **session authorship** with **session-referenced context**. §8 introduces the `MentionEvent` atomic unit and the delta-vs-context split. v0.1 sections (§1–§7) are preserved for traceability; where they conflict with §8, §8 wins — specifically:

- §2.1 `Entity` is a **Stage-2 aggregate**, NOT a Stage-1 per-session emission. Stage 1 emits `MentionEvent[]`; Stage 2 collapses them into canonical `Entity` nodes in the Vault.
- §2.5 `CognitiveDual` is **Stage-2 only**. It requires cross-session recurrence; single-session extractors MUST NOT emit it.
- Any conflict between §1–§7 and §8 is resolved in favor of §8.

**Why this exists:** the current `extractor.py` outputs `{entities, meta_learnings_user, meta_learnings_ai}` — a flat bag. The atelier journal demonstrates that the load-bearing signal is *relational*: drifts, stances, dual-pairs, level-tagging, affect. This doc defines the richer target so extractor iterations aim at the right object.

**Change log:**
- v0.1 (initial): 10 object types, flat extraction per session.
- v0.2: `Entity` top-level list replaced by `MentionEvent[]` to preserve chronology. Delta-gated objects (`Decision`, `Drift`, `Rule`, `MentalMove`, `Stance`, `Affect`, `MetaMoment`) extracted only when authored-this-session. Stage 2 inference patterns defined (§8.3).
- v0.2.1 (current, pre-pilot patch from Qwen review): clarified that §2.1 `Entity` and §2.5 `CognitiveDual` are Stage-2 aggregates not Stage-1 emissions. Explicit note that `MentionEvent{target_type: Decision}` is a *reference edge* (a session mentioning a prior decision), distinct from a `Decision[]` *delta object* (a decision authored this session). `MentionEvent.timestamp` inherits from the cited `Turn.timestamp`.

**Deferred to v0.3** (post-pilot, driven by Oracle↔Qwen comparison):
- `co_mentioned_with` deduplication at scale via Stage-2 turn-level indexing (pilot keeps inline).
- `has_reflection_markers: bool` as secondary reflection-density signal.
- `reliability_weight` per provider in `session_meta` for Stage-2 skew correction.
- Confidence scoring on Stage-2 MentalMove promotion (replacing the arbitrary N=3 threshold).
- Embedding-based `mention_clusters` (replacing surface-string matching for rename detection).

---

## 1. Top-level containers

### `Session`
Root unit. One per conversation file.

| Field | Type | Notes |
|---|---|---|
| `session_id` | string | provider-native id or uuid |
| `provider` | enum | `claude_code` \| `claude_desktop` \| `antigravity` \| `gemini_cli` \| `cline` |
| `started_at` | datetime\|null | best-effort from file metadata |
| `ended_at` | datetime\|null | |
| `project_hint` | string\|null | cwd, repo name, or inferred topic |
| `reflection_density` | enum | `low` \| `medium` \| `high` — see §4 |
| `turn_count` | int | |

### `GlobalProfile` (aggregated, one per corpus)
Rolled up across all sessions. See §3.6.

---

## 2. Observation objects (produced per session)

Every observation carries `session_id` for back-linking and a `level` tag (see §3.7).

### 2.1 `Entity`
Things that exist in the user's world.

```
{
  "id": string,                    // slugified name
  "type": "Project"|"Tool"|"Technology"|"Artifact"|"Concept"|"Error",
  "name": string,
  "description": string,
  "aliases": [string],
  "first_seen_session": session_id,
  "sessions": [session_id]         // bidirectional link
}
```

### 2.2 `Decision`
A locked choice with motivation.

```
{
  "session_id": string,
  "proposition": string,           // "use React Flow as Canvas substrate"
  "status": "locked"|"pending"|"drifted_from"|"revisited",
  "why": string,                   // motivation in user's words if possible
  "alternatives_considered": [string],
  "decided_by": "user"|"ai"|"mutual",
  "related_entities": [entity_id]
}
```

### 2.3 `Drift`
Directional correction. The highest-value signal — a drift names both a failure mode and the rule that corrects it.

```
{
  "session_id": string,
  "proposed": string,              // what ai or user drifted toward
  "corrected_to": string,
  "trigger": "user_redirect"|"user_confusion"|"user_affect"|"mismatch_observation"|"self_catch",
  "rule_generated": string|null,   // derived guidance, if any
  "evidence_turn": int|null
}
```

### 2.4 `MentalMove`
How the user thinks — observed reasoning move.

```
{
  "session_id": string,
  "move": string,                  // "state-of-reality before plans"
  "level": "axiom"|"generalizable"|"user_specific",
  "evidence": string               // short quote or paraphrase
}
```

### 2.5 `CognitiveDual`
Paired disposition tradeoff. Emerges across sessions, not within one.

```
{
  "pair": [string, string],        // ["flow capture", "structured categorization"]
  "when_a": string,                // conditions favoring side a
  "when_b": string,
  "user_default": "a"|"b"|"contextual"|null,
  "supporting_sessions": [session_id]
}
```

### 2.6 `Stance`
Agreement/disagreement signal on a proposition.

```
{
  "session_id": string,
  "proposition": string,
  "stance": "agree"|"disagree"|"redirect"|"dismiss"|"confirm"|"defer",
  "evidence": string,
  "target": "ai_claim"|"own_prior"|"external"   // what's being stanced on
}
```

### 2.7 `Affect`
Emotional/state markers treated as signal, not noise. Affect tells you what the *real goal* is.

```
{
  "session_id": string,
  "marker": "fatigue"|"sourness"|"excitement"|"frustration"|"satisfaction"|"restlessness",
  "trigger": string,
  "implication": string            // "sourness → shipping, not learning, is the goal"
}
```

### 2.8 `Rule`
Candidate guidance for a future agent, synthesized from drifts or decisions.

```
{
  "session_id": string,            // where it was generated
  "rule_text": string,
  "applies_to": "soul"|"principle"|"brain_general"|"brain_personal",
  "source": {"type": "drift"|"decision"|"meta_moment", "id": string}
}
```

### 2.9 `MetaMoment`
Session reflecting on itself. Rare but high-value.

```
{
  "session_id": string,
  "observation": string            // "we are demonstrating Atelier without Atelier existing"
}
```

### 2.10 `Artifact`
Files/outputs produced during the session.

```
{
  "session_id": string,
  "path": string,
  "purpose": string,
  "status": "drafted"|"committed"|"discarded"
}
```

---

## 3. Cross-cutting concerns

### 3.1 Session linking
Every object references `session_id`. Sessions get markdown files in `/vault/sessions/{session_id}.md` with Obsidian `[[entity_id]]` wiki-links to every entity they mention. Entity files back-link to sessions.

### 3.2 Entity deduplication
Across sessions, entities collapse by canonical name + alias list. A semantic filter (embedding cosine, or LLM yes/no) disambiguates near-duplicates ("Claude Code" vs "claude-code-cli").

### 3.3 Provider normalization
A pre-extractor layer converts each provider's raw format to a canonical `Turn[]` shape: `{role, content, timestamp, turn_index, tool_calls?}`. Extractor reads only canonical turns, never raw files.

Provider coverage (current reality):
- `claude_code` — jsonl, handled by existing extractor.py
- `claude_desktop` — unknown format, 38 files
- `antigravity` — **protobuf (.pb)**, 3562 files (80% of corpus, currently unreachable)
- `gemini_cli` — unknown format, 226 files
- `cline` — unknown format, 107 files

### 3.4 Reflection density tagging
Set per-session so downstream knows what to trust:
- `low` — tool-use heavy, few user words. Yields Entities, rare Stances.
- `medium` — mixed. Yields Entities, Decisions, Stances.
- `high` — discussion-heavy. Can yield Drifts, MentalMoves, MetaMoments, Affect.

Classifier: ratio of user-text-tokens to tool-call-tokens, plus presence of reflection markers ("why", "I think", "I'm sour", "let's step back").

### 3.5 Two-stage pipeline
- **Stage 1 — per-session extraction:** reads one canonical session, emits `Entity`, `Decision`, `Drift`, `Stance`, `Affect`, `MentalMove(candidate)`, `MetaMoment`, `Artifact`. Bounded by what the transcript contains.
- **Stage 2 — corpus aggregation:** reads all Stage 1 outputs, produces `CognitiveDual` (requires recurrence), confirmed `MentalMove` (repeated across ≥N sessions), `Rule` (synthesized from repeated drifts), and `GlobalProfile`.

### 3.6 `GlobalProfile` (aggregated output)

```
{
  "confirmed_mental_moves": [{move, level, occurrences, example_sessions}],
  "cognitive_duals": [CognitiveDual],
  "rules": [Rule],
  "entity_frequency": {entity_id: count},
  "affect_patterns": [{marker, typical_trigger, implication}],
  "drift_patterns": [{recurrent_drift, rule, occurrences}]
}
```

### 3.7 `level` tagging
Attached to `MentalMove`, `Decision`, `Rule`. Determines which layer of the downstream agent brain consumes it:
- `axiom` — treated as given for this user, not taught back.
- `generalizable` — universal principle, goes into `brain_general`.
- `user_specific` — goes into `brain_personal`.
- `domain_specific` (optional) — tied to a specific Project entity.

### 3.8 Raw transcript backlinks
Every observation stores `evidence_turn` (int, index into canonical turns) and optionally `evidence_quote` (≤200 chars). Enables audit: can I trace this Decision back to the exact exchange? If no → suspect hallucination.

---

## 4. Extractability matrix

What's realistically recoverable from raw transcripts by provider category:

| Object | low-reflection (tool-use sessions) | high-reflection (discussions) |
|---|---|---|
| Entity | ✅ reliable | ✅ reliable |
| Artifact | ✅ (from tool calls) | △ (must be mentioned) |
| Decision | △ only if explicit ("let's go with X") | ✅ |
| Stance | △ | ✅ |
| Drift | ❌ | △ requires user redirect turn |
| MentalMove | ❌ | △ candidate-only; confirmed at Stage 2 |
| Affect | ❌ | △ if emotion words present |
| MetaMoment | ❌ | ❌ rare even in high-reflection |
| CognitiveDual | ❌ | ❌ Stage 2 only |
| Rule | ❌ | △ Stage 2 preferred |

**Implication:** Antigravity's 3562 protobuf files — even once decoded — will mostly yield Entities + Artifacts. That's still valuable (they anchor the user's project graph), but the META-PROFILE signal will come from the ~600 Claude Desktop + Claude Code + Gemini CLI sessions where actual discussion happens.

---

## 5. Hand-trace validation (three journal paragraphs → schema objects)

### Trace A — "React Flow locked"

> **Journal:** *"Canvas decision locked. React Flow (MIT) + invest extra to make it SOTA. Commitment: the aesthetic/interaction work goes into Atelier's own node-card design language, making it feel distinctly Atelier-shaped rather than tldraw-shaped with our data."*

Decomposes to:
- `Entity{type: Tool, name: "React Flow", description: "MIT canvas library, Atelier Canvas substrate"}`
- `Entity{type: Tool, name: "tldraw", aliases: []}` (negative reference)
- `Decision{proposition: "Use React Flow as Canvas substrate", status: locked, why: "MIT license, invest to make SOTA, avoid tldraw-shaped feel", alternatives_considered: ["tldraw"], decided_by: mutual, related_entities: ["react-flow", "tldraw"]}`
- `Stance{proposition: "Atelier should inherit tldraw aesthetics", stance: dismiss, target: ai_claim}`

✅ Clean decomposition. No information lost.

### Trace B — "Lovable drift"

> **Journal:** *"I read 'people are restless, Lovable is booming' as a goal → concluded 'compress onboarding Lovable-style.' Wrong leap. Lovable's pattern is **opposite** of Atelier's purpose... Atelier's goal is shipping, not speed-to-demo. My proposal inherited Lovable's failure mode."*

Decomposes to:
- `Drift{proposed: "compress onboarding Lovable-style", corrected_to: "shipping-first onboarding, not speed-to-demo", trigger: user_redirect, rule_generated: "Anti-pattern: optimizing for speed-to-scaffold. Atelier's bar is speed-to-ship."}`
- `Rule{rule_text: "When a competitor's pattern is cited, verify its goal aligns with Atelier's goal before inheriting its UX.", applies_to: principle, source: {type: drift, id: ...}}`
- `Stance{proposition: "Lovable's onboarding pattern is applicable to Atelier", stance: disagree, target: own_prior}`
- `MentalMove{move: "Pattern-match on goal-mismatch between adopted pattern and own product", level: generalizable}`

✅ Clean. Drift is the load-bearing object here; the rule falls out of it naturally.

### Trace C — "Sourness as data"

> **Journal:** *"Sourness as data. Named his own emotional state ('I'm sour about this') as a signal about what the real goal is (shipping, not learning). → Generalizable."*

Decomposes to:
- `Affect{marker: sourness, trigger: "unshipped work accumulating", implication: "real goal is shipping, not learning"}`
- `MentalMove{move: "Sourness as data — read own affect as signal of actual goal", level: generalizable, evidence: "I'm sour about this"}`
- `Rule{rule_text: "When user expresses emotional state, treat it as signal about goal-orientation, not noise to deflect.", applies_to: soul}`

✅ Affect + MentalMove + Rule chain together cleanly. This is exactly the kind of signal the current extractor flattens into a generic meta_learnings bullet.

**Validation result:** three diverse paragraphs decomposed without loss. Schema holds. Ready to iterate extractor.py against it.

---

## 6. Deltas from current `extractor.py`

Current output → new schema:

| Current | Replaced by |
|---|---|
| `entities[]` | `Entity[]` (richer, typed aliases, back-linking) |
| `meta_learnings_user[]` | split into `MentalMove[]`, `Affect[]`, `Decision[]`, `Drift[]`, `Stance[]` |
| `meta_learnings_ai[]` | folded into `Drift[]` (trigger=self_catch or tool-failure) + `Rule[]` |
| — | `Artifact[]`, `MetaMoment[]` (new) |
| — | `reflection_density`, `level` tags (new, cross-cutting) |

---

## 7. Open questions before implementation

1. **Embedding model choice** for Entity dedup (Stage 2) — local (bge-small?) or rely on Qwen?
2. **Storage format** — `/vault/*.md` (markdown with YAML frontmatter) vs `/vault/*.json` vs both (markdown for human-readable entities, JSONL for observation streams). README commits to markdown; recommend observations stay JSONL, markdown is rendered from them.
3. **Protobuf decoding for Antigravity** — .proto schema availability? 80% of corpus blocked until this is resolved.
4. **Session chunking** — long sessions may exceed model context. Chunk by turn windows and merge at session level, or treat chunks as pseudo-sessions?
5. **Stage 2 recurrence threshold** — how many sessions must a MentalMove appear in before it's promoted from candidate to confirmed? (Propose N=3 as starting default, revisit.)

---

## 8. v0.2 update — `MentionEvent`, delta/context split, chronological inference

### 8.1 The problem v0.1 missed

The first oracle extraction (Gemini CLI session `2026-04-22T08-14-f90cf5b2`) surfaced a structural error: a session that *references* pre-existing state was indistinguishable in the output from a session that *authors* state. The session recited MyProject's canvas (built in prior sessions) and the extractor recorded 4 `Decision` entries + 19 `Entity` definitions as if this session had produced them. Fed into OmniGraph's Vault, that becomes **false authorship replicated across every repeat-context session** — worse than noise.

Skipping context entirely would also be wrong: the *chronological pattern of mentions* (how often a target is referenced, when it first appears, when it goes silent, how its valence shifts) is the richest signal we have for inferring what the user actually cares about, what was internalized, what was abandoned. v0.2 preserves every mention but changes the atomic unit so time is first-class.

### 8.2 `MentionEvent` — the new atomic unit

Every session emits a stream of mention events instead of a flat `Entity[]` list.

```
MentionEvent {
  session_id:     string
  timestamp:      iso8601          // INHERITED from the cited Turn.timestamp — never extractor wall-clock
  target_id:      string           // slugified; may collide across sessions (Stage 2 dedups)
  target_type:    enum             // Project | Tool | Technology | Concept | Artifact | Error | Decision
                                   // When target_type == Decision, this event is a REFERENCE EDGE to a
                                   // Decision authored in a prior session — NOT a delta-gated Decision
                                   // object (those live in the session's top-level `decisions[]`).
  mention_type:   enum             // see §8.2.1
  authorship:     "reader" | "writer"
  valence:        enum             // neutral | positive | confident | uncertain | frustrated | negative
  evidence_turn:  int
  evidence_quote: string           // ≤200 chars
  mentioned_as:   string           // the exact surface phrase used (for Stage 2 rename detection)
  co_mentioned_with: [target_id]   // optional: other targets in the same turn (Stage 2 coupling analysis)
                                   // SCALE NOTE: for large corpora, Stage 2 can reconstruct this from
                                   // turn_index alone. Pilot keeps inline for simplicity.
}
```

#### 8.2.1 `mention_type` — the taxonomy

| Type | Meaning | Example |
|---|---|---|
| `first_introduction` | Target appears for the first time, with enough framing to define it | "We're going to build MyProject — a SaaS for X" |
| `reference` | Target is named/used without adding new definition | "Looking at the MyProject canvas…" |
| `re_decision` | A prior decision is re-opened, re-affirmed, or overturned | "Actually, let's switch from tldraw to React Flow" |
| `concern_raised` | Target is named as a problem, risk, or blocker | "External-API scrape is fragile" |
| `concern_resolved` | A previously-raised concern is closed out | "Parse node is stable now, 8/10 test agreements pass" |
| `rename` | Target is referred to under a new surface name | "It's called `bhasha` now, not `carlsbert`" |
| `pivot` | Target is abandoned in favor of a replacement | "Dropping v1 auth middleware, writing new one" |
| `deprecation` | Target is retained but marked inactive | "property_advocate is v1, shelved" |
| `revisit` | Target is explicitly recalled from prior session | "Remember the hub-and-spoke decision? Let's refine it." |

`authorship: writer` applies when this session created, modified, or decided the target. `authorship: reader` when it only referenced.

### 8.3 Delta-gated vs context-mention split

| Object type | Extraction rule |
|---|---|
| `MentionEvent` | **Always emitted** for every Entity/Artifact/Decision reference. Authorship + type flags carry the semantics. |
| `Decision` | **Delta-gated.** Record only if authored-this-session (new, revisited, or overturned). A Decision whose `origin` is `carried_forward_as_constraint` is recorded as a `MentionEvent{target_type:Decision, mention_type:reference}` instead. |
| `Drift` | **Delta-gated.** Inherently session-originated (the drift is what this session caught/corrected). |
| `Rule` | **Delta-gated.** Synthesized from this session's drifts/decisions/moves. |
| `MentalMove` | **Delta-gated.** Observed behavior during this session's turns. |
| `Stance` | **Delta-gated.** This session's agreement/disagreement on a proposition. |
| `Affect` | **Delta-gated.** Emotional markers observed in this session. |
| `MetaMoment` | **Delta-gated.** Session-level reflection produced in this session. |

Rule of thumb: anything that describes *how the user/AI operated during this session* → delta-gated. Anything that names *things that exist in the user's world* → MentionEvent.

### 8.4 Stage 2 inference patterns (what chronology unlocks)

Stage 2 ingests the full MentionEvent stream + delta objects across all sessions, ordered by timestamp, and produces the following longitudinal signals. These are **queries over the mention stream**, not static fields.

1. **Convergence vs abandonment** — a target mentioned N times over window W then silent for ≥2W. If last 3 events had `valence ∈ {positive, confident}` or `mention_type ∈ {concern_resolved}`, → `settled`. If last events had `valence ∈ {frustrated, negative}` or raised concerns never resolved, → `abandoned_or_stuck`. Ambiguous cases trigger cross-check with filesystem mtime on the target's directory (if known) — still-active dir → silent work continues; cold dir → full abandonment.

2. **Internalization / teaching ceiling** — on the same target, measure token-length of the first 3 vs last 3 mentions. Dramatic terseness reduction → shared vocabulary established, AI learned. Length stable or growing → AI keeps losing context (diagnostic signal that the user's memory system is load-bearing for this concept — which is exactly omnigraph's own value prop).

3. **Decision load-bearing score** — for each authored `Decision`, count referencing MentionEvents in subsequent sessions. Referenced ≥5 subsequent sessions → load-bearing. Referenced 0-1 times then silent → either fully internalized (implicit reuse, hard to distinguish) or irrelevant. Use valence on the references to disambiguate.

4. **Rename / pivot detection** — two targets A and B where A's mention frequency decays while B's first_introduction happens in overlapping window + high co-occurrence in the crossover sessions → likely rename (`A → B`) or concept-replacement. Output as a derived `RenameChain` edge in the graph.

5. **Concern lifecycle** — `concern_raised` events followed by `concern_resolved` on the same target within ≤3 sessions → worked-through-cleanly. Raised then silent for ≥5 sessions → **latent unresolved** — these are the highest-value surfacing candidates for "what you haven't looked at in a while".

6. **Cross-provider bleed** — target appears in ≥3 providers → persistent concern in the user's world, independent of tool. Single-provider targets are tool-session artifacts, lower priority for the Meta-Profile.

### 8.5 Per-session output shape (v0.2)

```json
{
  "session_id": "...",
  "provider": "...",
  "extractor": "oracle_claude_opus | qwen_3.6_35b_a3b",
  "schema_version": "0.2",
  "session_meta": {
    "turn_count": int,
    "user_turns": int,
    "assistant_turns": int,
    "reflection_density": "low|medium|high",
    "project_hint": "...",
    "timestamp_start": iso8601,
    "timestamp_end": iso8601
  },
  "mention_events": [MentionEvent, ...],
  "decisions":     [Decision,   ...],  // delta only
  "drifts":        [Drift,      ...],
  "rules":         [Rule,       ...],
  "mental_moves":  [MentalMove, ...],
  "stances":       [Stance,     ...],
  "affect":        [Affect,     ...],
  "meta_moments":  [MetaMoment, ...],
  "artifacts":     [Artifact,   ...],  // files produced THIS session (referenced files → MentionEvents)
  "unresolved":    [string,     ...],
  "verification_notes": [string, ...]
}
```

Stage 2 produces `/pilot/oracle/global_profile.json` and a `/vault/<target_id>.md` node per merged target, with the MentionEvent stream as the node's history.

### 8.6 Lock boundary

v0.2 is locked for the pilot oracle + Qwen runs. Do not refine the schema until empirical oracle↔Qwen comparison surfaces a *structurally impossible* case. Minor field rewording or inference-pattern tuning is post-pilot work.
