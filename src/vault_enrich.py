"""Vault semantic enrichment.

For each top-N entity (default top-30 by mention count), gather mention
contexts from the events stream and ask Qwen for a 3-line summary plus
3 representative quotes. Append the result to the entity's Vault page
under a new `## Semantic summary` section.

Pilot first (--limit 5), inspect quality, then scale.

Usage:
    python src/vault_enrich.py --limit 5                 # pilot
    python src/vault_enrich.py --limit 30                # full top-N
    python src/vault_enrich.py --target-id zeroclaw      # one entity
    python src/vault_enrich.py --dry-run                 # show what would run

GPU coordination: each entity = 1 Qwen call at priority 5 (batch). The
ETL daemon at priority 9 is preempted; interactive / atelier reflect
preempt this.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from collections import defaultdict
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PILOT = ROOT / "pilot"
GLOBAL_PROFILE = PILOT / "full" / "global_profile.json"
EVENTS_FILE = PILOT / "events" / "2026-04.jsonl"
VAULT_DIR = PILOT / "vault"

ENRICH_MARKER = "## Semantic summary"


# ----------------------------------------------------------------------
# data gathering
# ----------------------------------------------------------------------

def load_top_entities(limit: int) -> list[dict]:
    d = json.loads(GLOBAL_PROFILE.read_text())
    rows = d.get("entity_frequency_top30", [])
    return rows[:limit]


def gather_mention_contexts(target_id: str, max_quotes: int = 12) -> list[dict]:
    """Walk events file, return up to max_quotes events for this target_id.

    Selection is diversity-first: try to span providers and authorships.
    """
    rows = []
    with EVENTS_FILE.open() as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("target_id") != target_id:
                continue
            quote = e.get("evidence_quote") or e.get("quote")
            if not quote:
                continue
            rows.append({
                "ts": e.get("ts"),
                "provider": e.get("provider"),
                "session_id": e.get("session_id"),
                "valence": e.get("valence"),
                "authorship": e.get("authorship"),
                "mentioned_as": e.get("mentioned_as"),
                "quote": quote,
            })

    if len(rows) <= max_quotes:
        return rows

    # Diversity sampling: bucket by (provider, valence) then round-robin.
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[(r.get("provider"), r.get("valence"))].append(r)
    keys = list(buckets.keys())
    out: list[dict] = []
    while len(out) < max_quotes and any(buckets[k] for k in keys):
        for k in keys:
            if buckets[k]:
                out.append(buckets[k].pop(0))
                if len(out) >= max_quotes:
                    break
    return out


# ----------------------------------------------------------------------
# Qwen call
# ----------------------------------------------------------------------

def build_prompt(entity: dict, contexts: list[dict]) -> tuple[str, str]:
    target = entity["target_id"]
    typ = entity.get("type", "Unknown")
    n = entity.get("events", 0)
    providers = ", ".join(entity.get("providers", []))

    quotes_block = "\n".join(
        f"  [{c['provider']}/{c.get('valence','?')}] \"{c['quote'][:200]}\""
        for c in contexts
    )

    system = (
        "You are summarizing a recurring entity in a developer's AI conversation history. "
        "Output STRICT JSON with this exact shape:\n"
        '{ "what": "<one sentence: what is this entity?>", '
        '"how_used": "<one sentence: how does the user employ it?>", '
        '"status": "<one short phrase: active / dormant / superseded / experimental>", '
        '"representative_quotes": [ {"quote": "...", "provider": "...", "why": "<1 phrase: why this quote>"}, '
        '{"quote": "...", "provider": "...", "why": "..."}, '
        '{"quote": "...", "provider": "...", "why": "..."} ] }\n'
        "Do not include markdown, do not wrap in code blocks, just the JSON object."
    )
    user = (
        f"Entity: {target}\n"
        f"Type: {typ}\n"
        f"Mention count: {n}\n"
        f"Providers seen: {providers}\n\n"
        f"Mention contexts (provider/valence + quote):\n{quotes_block}\n\n"
        "Produce the JSON summary now."
    )
    return system, user


def parse_response(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        # strip code fence
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find first {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass
    return None


# ----------------------------------------------------------------------
# vault rewrite
# ----------------------------------------------------------------------

def vault_path(target_id: str) -> Path:
    safe = target_id.replace("/", "-").replace(" ", "-")
    return VAULT_DIR / f"{safe}.md"


def render_summary_md(summary: dict, contexts_count: int) -> str:
    out = [ENRICH_MARKER, ""]
    out.append(f"**What:** {summary.get('what','—')}")
    out.append("")
    out.append(f"**How used:** {summary.get('how_used','—')}")
    out.append("")
    out.append(f"**Status:** {summary.get('status','—')}")
    out.append("")
    quotes = summary.get("representative_quotes", []) or []
    if quotes:
        out.append("**Representative quotes:**")
        for q in quotes[:3]:
            txt = q.get("quote", "").strip()
            prov = q.get("provider", "?")
            why = q.get("why", "")
            out.append(f"- [{prov}] \"{txt}\" — _{why}_")
    out.append("")
    out.append(f"_Enriched from {contexts_count} sampled mentions, "
               f"{time.strftime('%Y-%m-%d %H:%M:%S')}._")
    out.append("")
    return "\n".join(out)


def merge_summary_into_vault(target_id: str, summary_md: str) -> bool:
    path = vault_path(target_id)
    if not path.exists():
        return False
    body = path.read_text()
    if ENRICH_MARKER in body:
        # Replace existing block.
        before, _ = body.split(ENRICH_MARKER, 1)
        # Find next section header (## ...) to know where the block ends.
        rest = body.split(ENRICH_MARKER, 1)[1]
        next_header_idx = -1
        for i, line in enumerate(rest.splitlines()):
            if i > 0 and line.startswith("## "):
                next_header_idx = i
                break
        if next_header_idx > 0:
            after = "\n".join(rest.splitlines()[next_header_idx:])
            new_body = before + summary_md + "\n" + after
        else:
            new_body = before + summary_md
    else:
        # Insert before "## Mention log" if present, else append.
        if "\n## Mention log" in body:
            i = body.index("\n## Mention log")
            new_body = body[:i] + "\n" + summary_md + body[i:]
        else:
            new_body = body.rstrip() + "\n\n" + summary_md
    path.write_text(new_body)
    return True


# ----------------------------------------------------------------------
# orchestrator
# ----------------------------------------------------------------------

def enrich_one(entity: dict, max_quotes: int = 12, dry_run: bool = False) -> dict:
    target = entity["target_id"]
    contexts = gather_mention_contexts(target, max_quotes=max_quotes)
    out = {"target_id": target, "contexts_used": len(contexts)}
    if not contexts:
        out["status"] = "skipped_no_contexts"
        return out

    system, user = build_prompt(entity, contexts)
    if dry_run:
        out["status"] = "dry_run"
        out["prompt_chars"] = len(system) + len(user)
        return out

    from gpu_lock import gpu_lock
    from qwen_pipeline import qwen_call

    t0 = time.time()
    with gpu_lock(holder="vault_enrich", priority=5):
        # qwen3.6-35b-a3b is a thinking model — it burns budget on
        # reasoning_content before producing output. 8192 is the floor
        # to leave headroom for both reasoning + JSON output.
        resp = qwen_call(system, user, max_tokens=8192)
    elapsed = time.time() - t0
    out["elapsed_s"] = round(elapsed, 1)

    summary = parse_response(resp.get("content", ""))
    if summary is None:
        out["status"] = "parse_failed"
        out["raw"] = resp.get("content", "")[:300]
        return out

    summary_md = render_summary_md(summary, len(contexts))
    merged = merge_summary_into_vault(target, summary_md)
    out["status"] = "merged" if merged else "no_vault_page"
    out["summary"] = summary
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5,
                    help="Top-N entities to enrich (pilot starts at 5)")
    ap.add_argument("--target-id", default=None,
                    help="Enrich one specific entity by id (overrides --limit)")
    ap.add_argument("--max-quotes", type=int, default=12,
                    help="Max mention contexts to send to Qwen per entity")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show prompt size without calling Qwen")
    args = ap.parse_args()

    entities = load_top_entities(args.limit)
    if args.target_id:
        entities = [e for e in entities if e["target_id"] == args.target_id]
        if not entities:
            # not in top-N; try to construct from any data we have
            entities = [{"target_id": args.target_id, "type": "?", "events": 0, "providers": []}]

    print(f"enriching {len(entities)} entit(ies); dry_run={args.dry_run}")
    results = []
    for i, e in enumerate(entities, 1):
        print(f"[{i}/{len(entities)}] {e['target_id']} (events={e.get('events','?')}) ...", flush=True)
        r = enrich_one(e, max_quotes=args.max_quotes, dry_run=args.dry_run)
        results.append(r)
        status = r.get("status")
        elapsed = r.get("elapsed_s", "")
        print(f"  → {status} ({elapsed}s)" if elapsed else f"  → {status}")

    # Summary
    print()
    ok = sum(1 for r in results if r.get("status") == "merged")
    print(f"merged: {ok}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
