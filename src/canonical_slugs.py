#!/usr/bin/env python3
"""Canonical-slug registry for OmniGraph target_ids.

Collapses near-duplicate slugs into a canonical form so Stage-2 aggregates
(co-mention, convergence, cross-provider) don't fragment signal across
near-identical tokens.

Operates at two levels:
  1. Deterministic normalization — lowercase, strip version suffixes,
     strip filler suffix tokens, collapse whitespace to hyphens.
  2. Human-overridable alias table at pilot/slug_aliases.yaml, loaded once
     per process. File is `canonical: [alias1, alias2]`; both canonical
     and aliases are pre-normalized at load time so the YAML can be
     written loosely.

Public API:
    canonicalize_slug(raw) -> str
    canonicalize_mention_events(events) -> list[dict]   (non-destructive)
    canonicalize_session(session) -> dict               (mutates)
    load_alias_table(path=None) -> dict[alias, canonical]

The pre-canonical target_id is preserved under `mentioned_as` on each
event / object when rewriting, so downstream consumers can audit merges.

CLI:
    python canonical_slugs.py check <slug>
    python canonical_slugs.py apply <indir>       # retroactively rewrite
    python canonical_slugs.py dump-aliases
    python canonical_slugs.py self-test
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


DEFAULT_ALIAS_PATH = (
    Path(__file__).resolve().parent.parent / "pilot" / "slug_aliases.yaml"
)

# -v1, -v2.3, _v6_1, -2025, etc.
VERSION_RE = re.compile(r"[-_ ]v?\d+(?:[._-]\d+)*$", re.IGNORECASE)

# Filler suffix tokens that don't change the canonical identity.
FILLER_SUFFIXES: tuple[str, ...] = (
    "-bridge", "-core", "-mcp", "-service", "-svc", "-daemon", "-server",
)

# Standalone tokens to strip when separator-bounded.
STRIP_TOKENS: tuple[str, ...] = ("the",)

_ALIAS_TABLE: dict[str, str] | None = None


def _normalize(raw: str) -> str:
    """Deterministic normalization. Independent of alias table."""
    if not raw:
        return raw
    s = str(raw).strip().lower()
    s = re.sub(r"\s+", "-", s)
    # Iterate until fixed point: version suffixes and filler suffixes can
    # uncover each other (e.g. "foo-mcp-v2" → "foo-mcp" → "foo").
    prev = None
    while prev != s:
        prev = s
        s = VERSION_RE.sub("", s)
        for suf in FILLER_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[: -len(suf)]
    for tok in STRIP_TOKENS:
        s = re.sub(rf"(^|[-_ ]){tok}([-_ ]|$)", r"\1\2", s)
    s = re.sub(r"[-_]{2,}", "-", s).strip("-_")
    return s


def load_alias_table(path: Path | None = None, force: bool = False) -> dict[str, str]:
    """Load slug_aliases.yaml → {alias: canonical}. Cached per-process."""
    global _ALIAS_TABLE
    if _ALIAS_TABLE is not None and not force and path is None:
        return _ALIAS_TABLE
    p = Path(path) if path else DEFAULT_ALIAS_PATH
    table: dict[str, str] = {}
    if not p.exists():
        _ALIAS_TABLE = table
        return table
    if yaml is None:
        print(
            f"[canonical_slugs] PyYAML not installed; alias file {p} ignored.",
            file=sys.stderr,
        )
        _ALIAS_TABLE = table
        return table
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        print(f"[canonical_slugs] failed to parse {p}: {e}", file=sys.stderr)
        _ALIAS_TABLE = table
        return table
    if not isinstance(raw, dict):
        _ALIAS_TABLE = table
        return table
    for canonical, aliases in raw.items():
        c = _normalize(str(canonical))
        if not c:
            continue
        table[c] = c
        if not aliases:
            continue
        if isinstance(aliases, str):
            aliases = [aliases]
        for a in aliases:
            na = _normalize(str(a))
            if na:
                table[na] = c
    _ALIAS_TABLE = table
    return table


def canonicalize_slug(raw: str) -> str:
    """Return canonical form for a raw target_id."""
    if not raw:
        return raw
    norm = _normalize(raw)
    return load_alias_table().get(norm, norm)


def canonicalize_mention_events(events) -> list:
    """Non-destructively rewrite target_id on each event to canonical form.

    Preserves pre-canonical value under `mentioned_as` when the rewrite
    actually changes something. Non-dict / invalid items pass through.
    """
    if not isinstance(events, list):
        return events
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        orig = ev.get("target_id")
        if not isinstance(orig, str) or not orig:
            out.append(dict(ev))
            continue
        canonical = canonicalize_slug(orig)
        new_ev = dict(ev)
        if canonical != orig:
            new_ev.setdefault("mentioned_as", orig)
            new_ev["target_id"] = canonical
        out.append(new_ev)
    return out


def canonicalize_session(session: dict) -> dict:
    """In-place canonicalization of a full session dict. Returns the session.

    Touches `mention_events` plus any top-level list of objects that carry
    `target_id` or `related_entities` (decisions, artifacts, unresolved).
    """
    if not isinstance(session, dict):
        return session
    mes = session.get("mention_events")
    if isinstance(mes, list):
        session["mention_events"] = canonicalize_mention_events(mes)
    for key in ("decisions", "artifacts", "unresolved"):
        items = session.get(key)
        if not isinstance(items, list):
            continue
        for o in items:
            if not isinstance(o, dict):
                continue
            tid = o.get("target_id")
            if isinstance(tid, str) and tid:
                new_tid = canonicalize_slug(tid)
                if new_tid != tid:
                    o.setdefault("mentioned_as", tid)
                    o["target_id"] = new_tid
            rel = o.get("related_entities")
            if isinstance(rel, list):
                o["related_entities"] = [
                    canonicalize_slug(x) if isinstance(x, str) else x for x in rel
                ]
    return session


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _cli_apply(indir: Path) -> None:
    n_files = 0
    n_rewrites = 0
    for p in sorted(indir.glob("*/*.json")):
        stem = p.stem
        if stem in ("global_profile",) or "_logs" in str(p) or "_run_summary" in stem:
            continue
        try:
            data = json.loads(p.read_text())
        except Exception as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        before = json.dumps(data, sort_keys=True)
        canonicalize_session(data)
        after = json.dumps(data, sort_keys=True)
        if before != after:
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            n_rewrites += 1
        n_files += 1
    print(f"scanned {n_files} files; rewrote {n_rewrites}")


def _self_test() -> int:
    table_for_test = {
        "acmetool": "acmetool",
        "acmetool-mcp": "acmetool",
        "acmetool-bridge": "acmetool",
        "AcmeTool-Core-v2.1": "acmetool",
        "atelier-phase-a": "atelier",
        "The Atelier": "atelier",
        "kimi-k2": "kimi",
        "claude_code": "claude-code",
        "unknown-project-xyz": "unknown-project-xyz",
    }
    # Force-load with bundled alias file
    load_alias_table(force=True)
    fails = 0
    for raw, expected in table_for_test.items():
        got = canonicalize_slug(raw)
        ok = got == expected
        if not ok:
            fails += 1
        print(f"  {'✓' if ok else '✗'} {raw!r:40} → {got!r}  (expected {expected!r})")
    # mention-events idempotence
    evs = [
        {"target_id": "AcmeTool-MCP", "target_type": "Tool", "mention_type": "reference"},
        {"target_id": "atelier-phase-a", "target_type": "Project", "mention_type": "reference"},
    ]
    out = canonicalize_mention_events(evs)
    ok = (
        out[0]["target_id"] == "acmetool"
        and out[0]["mentioned_as"] == "AcmeTool-MCP"
        and out[1]["target_id"] == "atelier"
    )
    print(f"  {'✓' if ok else '✗'} mention_events rewrite preserves mentioned_as")
    if not ok:
        fails += 1
    return fails


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Canonical slug registry")
    sub = ap.add_subparsers(dest="cmd")
    p_apply = sub.add_parser("apply", help="Retroactively canonicalize <indir>/<prov>/<sid>.json")
    p_apply.add_argument("indir", type=Path)
    p_check = sub.add_parser("check", help="Normalize a single slug")
    p_check.add_argument("slug")
    sub.add_parser("dump-aliases", help="Print loaded alias table as JSON")
    sub.add_parser("self-test", help="Run built-in sanity assertions")
    args = ap.parse_args(argv)

    if args.cmd == "apply":
        _cli_apply(args.indir)
        return 0
    if args.cmd == "check":
        print(canonicalize_slug(args.slug))
        return 0
    if args.cmd == "dump-aliases":
        print(json.dumps(load_alias_table(force=True), indent=2))
        return 0
    if args.cmd == "self-test":
        return 1 if _self_test() else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
