#!/usr/bin/env python3
"""`omnigraph compile` — emit a projection to stdout or a file.

Usage:
    python compile_cli.py <target> --state <indir> [--out <path>] [--max-tokens N]

Examples:
    python compile_cli.py light_ir --state pilot/qwen
    python compile_cli.py claude_md --state pilot/qwen --out ~/.claude/CLAUDE.md
    python compile_cli.py boot_context --state pilot/qwen --out /tmp/boot.json

The <state> dir is expected to contain global_profile.json (produced by
stage2_aggregate.py). Vault / events dirs are auto-detected as siblings.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from compiler import get_compiler, list_targets
from compiler.base import VaultState
from compiler.sanitize import sanitize_global_profile, VALID_LEVELS


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help=f"one of: {', '.join(list_targets())}")
    ap.add_argument("--state", required=True, help="Aggregate output dir (containing global_profile.json)")
    ap.add_argument("--out", default=None, help="Write to file instead of stdout")
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--sanitize", default="none", choices=VALID_LEVELS,
                    help="Sugar-ladder sanitization: none | named_stripped | entities_removed | aggregated")
    args = ap.parse_args(argv)

    targets = list_targets()
    if args.target not in targets:
        print(f"unknown target: {args.target!r}; available: {targets}", file=sys.stderr)
        return 2

    state = VaultState.from_dir(Path(args.state))
    if not state.global_profile:
        print(f"⚠  global_profile.json missing or empty in {args.state}", file=sys.stderr)

    if args.sanitize != "none":
        state.global_profile = sanitize_global_profile(state.global_profile, args.sanitize)

    compiler = get_compiler(args.target)
    out = compiler.compile(state, max_tokens=args.max_tokens)

    if args.out:
        path = Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out)
        print(f"✅ {args.target} → {path} ({len(out)} chars)")
    else:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
