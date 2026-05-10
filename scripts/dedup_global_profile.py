"""Post-aggregate dedup pass over global_profile.json.

Two operations:
  (a) Slug normalization — apply canonical_slugs alias table to entity-keyed
      lists (concerns, entity_frequency, cross_provider_bleed, P1 convergence)
      and merge duplicate target_ids by summing their counts.

  (b) Mental-move fuzzy dedup — collapse confirmed_mental_moves whose text
      shares a long normalized prefix or one is a strict substring of another.
      Sums occurrences. Keeps the longest variant as the canonical text.

Non-destructive: reads global_profile.json, writes back in place. Safe to
re-run; idempotent.

Usage:
    python scripts/dedup_global_profile.py
    python scripts/dedup_global_profile.py --state pilot/full
    python scripts/dedup_global_profile.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from canonical_slugs import canonicalize_slug  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# slug dedup
# ----------------------------------------------------------------------

def _merge_count_keys(merged: dict, item: dict, count_keys: list[str]) -> None:
    """Sum count-style fields when merging two entries with the same canonical id."""
    for k in count_keys:
        if k in item:
            merged[k] = (merged.get(k) or 0) + (item.get(k) or 0)


def _merge_set_keys(merged: dict, item: dict, set_keys: list[str]) -> None:
    """Union list-of-strings fields (e.g., providers)."""
    for k in set_keys:
        if k in item:
            cur = set(merged.get(k) or [])
            cur.update(item.get(k) or [])
            merged[k] = sorted(cur)


def dedup_slug_keyed_list(items: list, count_keys: list[str], set_keys: list[str]) -> list:
    """Merge items with the same canonical target_id; preserve order of first occurrence."""
    if not isinstance(items, list):
        return items
    canonical_first_index: dict[str, int] = {}
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it)
            continue
        tid = it.get("target_id")
        if not tid:
            out.append(it)
            continue
        cid = canonicalize_slug(tid) or tid
        if cid in canonical_first_index:
            merged = out[canonical_first_index[cid]]
            _merge_count_keys(merged, it, count_keys)
            _merge_set_keys(merged, it, set_keys)
            # Keep the canonical id even if the first-seen variant was an alias.
            merged["target_id"] = cid
        else:
            new = dict(it)
            new["target_id"] = cid
            canonical_first_index[cid] = len(out)
            out.append(new)
    return out


# ----------------------------------------------------------------------
# mental move fuzzy dedup
# ----------------------------------------------------------------------

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_move(text: str) -> str:
    return _NORM_RE.sub(" ", (text or "").lower()).strip()


def dedup_mental_moves(moves: list) -> list:
    """Collapse near-duplicate mental moves.

    Two moves are considered the same if:
      - one's normalized text is a prefix of the other (length >=20 chars), or
      - they share a normalized prefix of >= 40 chars
    The longer/more-specific text is kept as canonical; occurrences sum.
    """
    if not isinstance(moves, list):
        return moves

    # Sort by occurrences desc so the canonical seed has highest signal.
    sorted_moves = sorted(moves, key=lambda m: -int(m.get("occurrences") or 0))
    canonical: list[dict] = []

    for m in sorted_moves:
        if not isinstance(m, dict):
            continue
        text = m.get("move") or ""
        norm = _normalize_move(text)
        if not norm:
            canonical.append(m)
            continue
        merged_into = None
        for c in canonical:
            cnorm = _normalize_move(c.get("move") or "")
            shared = _common_prefix_len(norm, cnorm)
            if (norm in cnorm or cnorm in norm) and min(len(norm), len(cnorm)) >= 20:
                merged_into = c
                break
            if shared >= 40:
                merged_into = c
                break
        if merged_into is None:
            canonical.append(dict(m))
        else:
            merged_into["occurrences"] = int(merged_into.get("occurrences") or 0) + int(m.get("occurrences") or 0)
            # Prefer the longer text (more specific wording).
            if len(text) > len(merged_into.get("move") or ""):
                merged_into["move"] = text
    return canonical


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# ----------------------------------------------------------------------
# orchestrator
# ----------------------------------------------------------------------

DEFAULT_PROFILE = ROOT / "pilot" / "full" / "global_profile.json"

# Each entry: (key in profile, count_keys, set_keys)
SLUG_KEYED_FIELDS = [
    ("inference_p5_concern_lifecycle", ["raised_count", "resolved_count", "event_count"], ["raised_in", "resolved_in", "providers"]),
    ("inference_concern_lifetime", ["raised_count", "event_count"], ["raised_in", "providers"]),
    ("entity_frequency_top30", ["events"], ["providers"]),
    ("inference_p6_cross_provider_bleed", ["event_count"], ["providers"]),
    ("inference_p1_convergence_vs_abandonment", ["event_count"], ["providers"]),
]


def run(profile_path: Path, dry_run: bool = False) -> dict:
    d = json.loads(profile_path.read_text())
    stats = {"slug_dedup": {}, "mental_moves": {}}

    for key, count_keys, set_keys in SLUG_KEYED_FIELDS:
        before = len(d.get(key) or [])
        if before == 0:
            continue
        deduped = dedup_slug_keyed_list(d[key], count_keys, set_keys)
        # Re-sort by primary count key descending to keep "top" semantics.
        primary = count_keys[0] if count_keys else None
        if primary:
            deduped.sort(key=lambda x: -int(x.get(primary) or 0))
        d[key] = deduped
        stats["slug_dedup"][key] = {"before": before, "after": len(deduped)}

    if "confirmed_mental_moves" in d:
        before = len(d["confirmed_mental_moves"])
        deduped = dedup_mental_moves(d["confirmed_mental_moves"])
        deduped.sort(key=lambda m: -int(m.get("occurrences") or 0))
        d["confirmed_mental_moves"] = deduped
        stats["mental_moves"] = {"before": before, "after": len(deduped)}

    if not dry_run:
        profile_path.write_text(json.dumps(d, indent=2, default=str))

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str((ROOT / "pilot" / "full").resolve()))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    p = Path(args.state) / "global_profile.json"
    if not p.exists():
        print(f"missing {p}", file=sys.stderr)
        return 2
    stats = run(p, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))
    print()
    print(f"{'(dry-run) ' if args.dry_run else ''}wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
