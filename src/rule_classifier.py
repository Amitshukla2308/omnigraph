"""Post-hoc rule classifier — splits rules_collected into 4 buckets.

Buckets:
  meta_user       — passive observation about how THIS user thinks/works (gold)
  meta_assistant  — how AI agents should behave with this user
  project_domain  — architectural/code rule scoped to a specific project
  debugging_recipe — situational "when X fails, do Y" patterns

Reads pilot/full/global_profile.json's rules_collected, classifies each via
Qwen at lock priority 5, writes pilot/full/rules_classified.json. The
light_ir compiler then filters to meta_user + meta_assistant only.

Usage:
    python src/rule_classifier.py --limit 5 --dry-run     # show prompt size
    python src/rule_classifier.py --limit 10              # pilot
    python src/rule_classifier.py                          # all 235
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PROFILE = ROOT / "pilot" / "full" / "global_profile.json"
OUT_PATH = ROOT / "pilot" / "full" / "rules_classified.json"

VALID_BUCKETS = {"meta_user", "meta_assistant", "project_domain", "debugging_recipe"}


SYSTEM_PROMPT = """You classify a single mined rule into one of 4 buckets. Output STRICT JSON only:
{ "bucket": "<one of: meta_user | meta_assistant | project_domain | debugging_recipe>",
  "scope": "<for project_domain: short project/product name; else null>",
  "why": "<one short phrase>" }

Bucket definitions:
- meta_user        : a passive observation about how THE USER thinks, works, prefers, decides. Generalizes across all the user's projects.
                     Examples: "User pre-decomposes work into numbered steps and expects strict order"
                               "User catches false simplification — flags collapsed structural distinctions"
                               "User insists on grounding claims against source before merge"

- meta_assistant   : how an AI agent should behave when working with this user. Generalizes across projects.
                     Examples: "Always confirm before destructive git ops"
                               "When a tool path fails, self-catch and pivot rather than retry"

- project_domain   : an architectural/code rule scoped to ONE specific project, file, library, or product.
                     Examples: "All trading thresholds must reside in config/trading_config.json"
                               "Use pdfplumber with Docling fallback for scanned PDFs in Bodyguard"

- debugging_recipe : situational "when X fails, do Y" pattern. Useful but not behavioral or universal.
                     Examples: "When CLI tools are missing from PATH, fall back to language libraries"
                               "When LM Studio rejects thinking flag, disable it for that model variant"

If a rule is mostly project-domain BUT also embeds a debugging tactic, prefer project_domain.
If a rule is debugging-recipe but mentions a specific named library, still call it debugging_recipe (the tactic generalizes).
If unsure, prefer project_domain over meta_* — meta should be high-precision."""


def build_user_prompt(rule_text: str, applies_to: str, level: str, provider: str) -> str:
    return (f"Rule text: {rule_text}\n"
            f"Applies-to category (extractor's tag): {applies_to}\n"
            f"Level (extractor's tag): {level}\n"
            f"Provider (where it was extracted): {provider}\n\n"
            f"Classify now.")


def parse_response(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e+1])
            except json.JSONDecodeError:
                return None
        return None


def classify_one(rule: dict) -> dict:
    from gpu_lock import gpu_lock
    from qwen_pipeline import qwen_call

    user = build_user_prompt(
        rule.get('rule_text', ''),
        rule.get('applies_to', '?'),
        rule.get('level', '?'),
        rule.get('provider', '?'),
    )
    t0 = time.time()
    with gpu_lock(holder="rule_classifier", priority=5):
        resp = qwen_call(SYSTEM_PROMPT, user, max_tokens=4096)
    elapsed = time.time() - t0

    parsed = parse_response(resp.get("content", ""))
    if parsed is None or parsed.get("bucket") not in VALID_BUCKETS:
        return {
            "bucket": "project_domain",  # safe default per the prompt's tiebreak
            "scope": None,
            "why": "parse_failed",
            "elapsed_s": round(elapsed, 1),
            "raw": (resp.get("content") or "")[:200],
        }
    parsed["elapsed_s"] = round(elapsed, 1)
    return parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="Classify only first N rules (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    profile = json.loads(PROFILE.read_text())
    rules = profile['rules_collected']
    if args.limit:
        rules = rules[:args.limit]

    print(f"classifying {len(rules)} rules; dry_run={args.dry_run}")

    if args.dry_run:
        sample = rules[0]
        usr = build_user_prompt(sample.get('rule_text',''), sample.get('applies_to','?'),
                                sample.get('level','?'), sample.get('provider','?'))
        print(f"sample prompt size: sys={len(SYSTEM_PROMPT)} usr={len(usr)}")
        print(f"sample rule: {sample.get('rule_text','')[:120]}")
        return 0

    out = []
    bucket_counts = {b: 0 for b in VALID_BUCKETS}
    t_start = time.time()
    for i, r in enumerate(rules, 1):
        c = classify_one(r)
        bucket_counts[c['bucket']] += 1
        out.append({**r, "classification": c})
        if i % 10 == 0 or i == len(rules):
            elapsed = time.time() - t_start
            rate = i / max(elapsed, 0.1)
            eta = (len(rules) - i) / max(rate, 0.001)
            print(f"  [{i:>3}/{len(rules)}] buckets={bucket_counts}  "
                  f"elapsed={elapsed:.0f}s  eta={eta:.0f}s")

    Path(args.out).write_text(json.dumps({
        "classified_at": time.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "n_rules": len(out),
        "bucket_counts": bucket_counts,
        "rules": out,
    }, indent=2, default=str))
    print(f"\nwrote {args.out}")
    print(f"final buckets: {bucket_counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
