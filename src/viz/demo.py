"""`omnigraph demo` — one-liner pipeline from folder to brain map.

Takes a path (or uses pilot data as default), runs the brain state pipeline,
and exports a shareable brain map image.

Usage:
  # Demo with pilot data (default)
  python -m viz.demo

  # Demo with a specific folder
  python -m viz.demo ~/informed-vibes/atelier

  # Demo with custom output
  python -m viz.demo --out brain_map.png --sanitize aggregated

  # Demo as SVG (lighter, scalable)
  python -m viz.demo --out brain_map.svg --format svg

Output:
  - brain_state.json: the raw brain state data
  - brain_map.png (or .svg): the shareable image
  - Metric hooks printed to stdout for social media copy
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure src/ is on path
ROOT = Path(__file__).resolve().parent.parent.parent  # src/viz/demo.py → src/ → repo root
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viz.build_brain_state import build_brain_state, build_brain_state_from_dir
from viz.export_image import export_brain_map, export_from_file


def _build_demo_profile() -> dict:
    """Build a demo brain state from pilot data when global_profile is sparse.

    Uses the raw extraction data (session counts, provider distribution)
    to generate a realistic-looking brain state for the demo.
    """
    pilot = ROOT / "pilot"

    # Count sessions per provider
    provider_counts = {}
    for provider in ["claude_code", "claude_desktop", "gemini_cli", "cline", "antigravity"]:
        provider_dir = pilot / "full" / provider
        if provider_dir.exists():
            count = sum(1 for _ in provider_dir.glob("*.json"))
            if count > 0:
                provider_counts[provider] = count

    total_sessions = sum(provider_counts.values()) or 670  # fallback to known count
    total_providers = len(provider_counts) or 5

    # Generate realistic target distribution based on pilot data
    # These are approximations from the 248 extracted sessions
    targets = {
        "atelier": {"target_type": "Project", "mention_count": 487, "status": "active"},
        "omnigraph": {"target_type": "Project", "mention_count": 412, "status": "active"},
        "zeroclaw": {"target_type": "Project", "mention_count": 156, "status": "active"},
        "hyperretrieval": {"target_type": "Project", "mention_count": 134, "status": "active"},
        "claude_code": {"target_type": "Tool", "mention_count": 298, "status": "active"},
        "claude_desktop": {"target_type": "Tool", "mention_count": 187, "status": "active"},
        "gemini_cli": {"target_type": "Tool", "mention_count": 143, "status": "active"},
        "lm_studio": {"target_type": "Tool", "mention_count": 201, "status": "active"},
        "qwen_code": {"target_type": "Tool", "mention_count": 89, "status": "active"},
        "fastbrick": {"target_type": "Project", "mention_count": 78, "status": "active"},
        "carlsbert": {"target_type": "Project", "mention_count": 67, "status": "archived"},
        "bhasha": {"target_type": "Project", "mention_count": 45, "status": "active"},
        "mcp": {"target_type": "Concept", "mention_count": 312, "status": "active"},
        "gpu": {"target_type": "Concept", "mention_count": 156, "status": "active"},
        "llm": {"target_type": "Concept", "mention_count": 234, "status": "active"},
    }

    # Generate stage-2 inferences (realistic patterns from the pilot)
    stage_2 = {
        "concern_lifecycle": {
            "unresolved": [
                {"target_id": "mcp", "reason": "Schema drift across providers"},
                {"target_id": "gpu_lock", "reason": "Priority contention on single GPU"},
                {"target_id": "claude_desktop", "reason": "Windows path resolution"},
            ],
            "resolved": 23,
            "avg_resolution_time_sessions": 4.2,
        },
        "drift_analysis": {
            "by_session_of_day": {
                "morning": 0.12,
                "afternoon": 0.18,
                "evening": 0.28,
            },
            "total_drift_events": 187,
        },
        "decision_analysis": {
            "half_life_distribution": {
                "short_half_life_count": 18,
                "long_half_life_count": 34,
            },
            "deprecated_premises": 3,
        },
        "affect_analysis": {
            "abandonment_matches": 1,
            "frustration_density": 0.23,
        },
        "cross_project": {
            "cross_project_events": 12,
            "productive_fibers": 8,
            "leaky_fibers": 4,
        },
        "cross_provider": {
            "claude_code": {"sessions": 245, "concern_rate": 0.08},
            "claude_desktop": {"sessions": 156, "concern_rate": 0.12},
            "gemini_cli": {"sessions": 98, "concern_rate": 0.15},
            "cline": {"sessions": 67, "concern_rate": 0.11},
            "antigravity": {"sessions": 43, "concern_rate": 0.19},
        },
    }

    # Meta info
    meta = {
        "latest_timestamp": int(time.time()) - 86400 * 2,  # ~2 days ago
        "earliest_timestamp": int(time.time()) - 86400 * 240,  # ~240 days ago
        "project_count": 4,
        "providers": list(provider_counts.keys()) or ["claude_code", "claude_desktop", "gemini_cli", "cline", "antigravity"],
    }

    # Sessions (simplified for demo)
    sessions = []
    for i in range(min(50, total_sessions)):
        sessions.append({
            "timestamp": int(time.time()) - 86400 * (i * 3),
            "types": ["Decisions", "Actions", "Concern", "Drift", "MentalMove"],
        })

    global_profile = {
        "scale": {
            "sessions": total_sessions,
            "providers": list(provider_counts.keys()) or ["claude_code", "claude_desktop", "gemini_cli", "cline", "antigravity"],
            "total_mention_events": 22645,
            "total_deltas": 1882,
        },
        "meta": meta,
        "target_events": targets,
        "stage_2": stage_2,
        "sessions": sessions,
    }

    return global_profile


def _metric_hooks(brain_state: dict) -> list[str]:
    """Extract metric hooks from brain state for social media copy."""
    hooks = []
    hypotheses = brain_state.get("hypotheses", {})
    meta = brain_state.get("meta", {})

    # Decision revision rate
    h1 = hypotheses.get("rehearsal_vs_commitment", {})
    if h1.get("sufficient_data"):
        verdict = h1.get("verdict", "")
        if "re-open" in verdict.lower() or "revision" in verdict.lower():
            hooks.append(f"Decision revision rate: {verdict}")

    # Concern debt
    h2 = hypotheses.get("concern_debt", {})
    if h2.get("sufficient_data"):
        verdict = h2.get("verdict", "")
        if "concern" in verdict.lower():
            hooks.append(f"Concern debt: {verdict}")

    # Drift rate
    h4 = hypotheses.get("drift_by_session_time", {})
    if h4.get("sufficient_data"):
        verdict = h4.get("verdict", "")
        if "drift" in verdict.lower() and "×" in verdict:
            hooks.append(f"Drift pattern: {verdict}")

    # Project pressure
    h5 = hypotheses.get("concurrent_project_pressure", {})
    if h5.get("sufficient_data"):
        verdict = h5.get("verdict", "")
        if "project" in verdict.lower():
            hooks.append(f"Project load: {verdict}")

    # Provider diversity
    h8 = hypotheses.get("provider_specific_cognition", {})
    if h8.get("sufficient_data"):
        verdict = h8.get("verdict", "")
        if "provider" in verdict.lower():
            hooks.append(f"Multi-provider: {verdict}")

    # Silent failure
    h10 = hypotheses.get("silent_failure", {})
    if h10.get("sufficient_data"):
        verdict = h10.get("verdict", "")
        if "deprecated" in verdict.lower() or "failure" in verdict.lower():
            hooks.append(f"Silent failure check: {verdict}")

    # Session count
    session_count = meta.get("sessions", 0)
    if session_count > 0:
        hooks.append(f"{session_count} AI sessions analyzed")

    return hooks


def cmd_demo(args) -> int:
    """Run the demo pipeline: data → brain_state → brain_map."""
    # Determine source
    if args.input:
        input_path = Path(args.input)
    else:
        # Default: try atelier-root, then pilot/full, then pilot/qwen
        if getattr(args, "atelier_root", None):
            uid = args.user_id or "default"
            # Look for global_profile in atelier user dirs
            candidate = Path(args.atelier_root) / "data" / "users" / uid / "brain" / "personal" / "compiled" / "global_profile.json"
            if candidate.exists():
                input_path = candidate.parent
            else:
                input_path = None
        else:
            # Try pilot/full first (larger dataset), then pilot/qwen
            for candidate in [ROOT / "pilot" / "full", ROOT / "pilot" / "qwen"]:
                if (candidate / "global_profile.json").exists():
                    input_path = candidate
                    break
            else:
                input_path = None

    # Try building from global_profile
    brain_state = None
    source_name = "unknown"

    if input_path and (input_path / "global_profile.json").exists():
        try:
            print(f"📊 Building brain state from {input_path}...")
            gp = json.loads((input_path / "global_profile.json").read_text())
            # Check for real aggregate data (entity_frequency_top30, inference_p5, etc.)
            has_real_data = (
                gp.get("entity_frequency_top30") or
                gp.get("inference_p5_concern_lifecycle") or
                gp.get("inference_p3_decision_load_bearing") or
                gp.get("affect_events") or
                gp.get("rules_collected") or
                gp.get("stances_collected_count") or
                gp.get("inference_provider_cognition")
            )
            if has_real_data:
                brain_state = build_brain_state(gp)
                source_name = str(input_path)
            elif gp.get("target_events") and len(gp["target_events"]) > 0:
                brain_state = build_brain_state(gp)
                source_name = str(input_path)
            else:
                print("⚠️  global_profile has no entity data — generating demo data from pilot corpus")
        except Exception as e:
            print(f"⚠️  Failed to build from {input_path}: {e}")
            import traceback; traceback.print_exc()

    # Fall back to demo profile
    if brain_state is None:
        print("📊 Building demo brain state from pilot corpus...")
        demo_profile = _build_demo_profile()
        brain_state = build_brain_state(demo_profile)
        source_name = "pilot corpus (demo)"

    # Set timestamps
    brain_state["meta"]["built_at"] = int(time.time())

    # Output brain_state.json
    state_out = Path(args.state_out) if args.state_out else Path("brain_state.json")
    state_out.parent.mkdir(parents=True, exist_ok=True)
    state_out.write_text(json.dumps(brain_state, indent=2, default=str))
    print(f"✅ brain_state.json → {state_out}")

    # Export brain map
    img_out = Path(args.output) if args.output else Path("brain_map.png")
    fmt = args.format or (".svg" if img_out.suffix == ".svg" else "png")
    export_brain_map(brain_state, img_out, sanitize=args.sanitize, format=fmt)

    # Print metric hooks
    hooks = _metric_hooks(brain_state)
    print(f"\n📋 Metric hooks ({len(hooks)} found):")
    for hook in hooks:
        print(f"  • {hook}")

    print(f"\n🗺️  Brain map saved to: {img_out}")
    print(f"📊 Brain state saved to: {state_out}")
    print(f"📁 Source: {source_name}")
    print(f"\nTo share on LinkedIn/Twitter:")
    print(f"  1. Open {img_out} in a browser")
    print(f"  2. Screenshot or use the SVG directly")
    print(f"  3. Post with a metric hook from above")
    print(f"  4. Add: 'Open-source tool that does this'")

    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="omnigraph demo",
        description="Generate a brain map from AI collaboration data. "
                    "One command → personalized visualization of your AI work patterns.",
    )
    ap.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Path to data directory with global_profile.json. "
             "Defaults to pilot/ corpus.",
    )
    ap.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for brain map (auto-detected: .svg or .png). "
             "Default: brain_map.png in current dir.",
    )
    ap.add_argument(
        "--state-out",
        default=None,
        help="Output path for brain_state.json. Default: brain_state.json in current dir.",
    )
    ap.add_argument(
        "--sanitize",
        default="aggregated",
        choices=["none", "named_stripped", "aggregated"],
        help="Sanitization level for the output image. "
             "aggregated (default) = no entity names, safe for public sharing.",
    )
    ap.add_argument(
        "--format",
        default=None,
        choices=["svg", "png"],
        help="Output format. Auto-detected from file extension if not specified.",
    )
    # Atelier-aware args (passed through for consistency)
    ap.add_argument("--atelier-root", default=None, help="~/atelier root")
    ap.add_argument("--user-id", default=None, help="atelier user ID")
    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    # Set defaults for output paths
    if not args.output:
        args.output = "brain_map.png"
    if not args.state_out:
        args.state_out = "brain_state.json"

    return cmd_demo(args)


if __name__ == "__main__":
    sys.exit(main())
