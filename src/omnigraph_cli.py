#!/usr/bin/env python3
"""`omnigraph` — unified CLI over Phases 0-5a.

Subcommands:
  status       — summarize pilot/ state (sessions, events, vault, aggregate)
  ingest       — enumerate sessions available via source adapters
  extract      — run qwen pipeline on one session (by sid) or many
  events       — materialize pilot/events/*.jsonl + index.json
  vault        — materialize pilot/vault/*.md
  aggregate    — stage-2 aggregation (full or --incremental)
  compile      — run a projection compiler (light_ir / claude_md / ...)
  index        — export HR git_history + run applicable HR build stages
  query        — ask a natural-language question over the indexed corpus
                 (stub — requires HR serve; prints a helpful message)
  pipeline     — sugar: events → vault → aggregate (idempotent)

Each subcommand wraps an existing module so nothing is duplicated. The CLI
exists to make the surface unified and teachable.

Usage (from repo root):
  python src/omnigraph_cli.py status
  python src/omnigraph_cli.py extract <session_id>
  python src/omnigraph_cli.py pipeline --sessions pilot/qwen --sessions pilot/full
  python src/omnigraph_cli.py compile claude_md --state pilot/qwen --out CLAUDE.md
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PILOT = ROOT / "pilot"

# Ensure src/ is on path for direct-imports below.
sys.path.insert(0, str(SRC))

# ---- user/atelier-root resolution (v0.2+) ----------------------------------
# Every subcommand that writes artifacts honors --atelier-root + --user-id.
# When absent, legacy pilot/ paths are used — single-founder local-dev mode.
from paths import (  # type: ignore  # noqa: E402
    DEFAULT_USER_ID,
    resolve_events_dir,
    resolve_vault_dir,
    resolve_global_profile_path,
    resolve_compiled_dir,
    resolve_meta_path,
)


def _resolve_events_out(args) -> Path:
    if getattr(args, "atelier_root", None):
        return resolve_events_dir(args.atelier_root, args.user_id or DEFAULT_USER_ID)
    return Path(getattr(args, "out", None) or (PILOT / "events"))


def _resolve_vault_out(args) -> Path:
    if getattr(args, "atelier_root", None):
        return resolve_vault_dir(args.atelier_root, args.user_id or DEFAULT_USER_ID)
    return Path(getattr(args, "out", None) or (PILOT / "vault"))


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------

def _count_session_files(d: Path) -> int:
    if not d.exists():
        return 0
    n = 0
    for p in d.glob("*/*.json"):
        if p.stem == "global_profile" or "_logs" in str(p):
            continue
        n += 1
    return n


def cmd_status(args) -> int:
    rows = []
    rows.append(("extracted (pilot/qwen)", _count_session_files(PILOT / "qwen")))
    rows.append(("extracted (pilot/full)", _count_session_files(PILOT / "full")))
    rows.append(("normalized_full", _count_session_files(PILOT / "normalized_full")))
    events_dir = PILOT / "events"
    n_events = 0
    months = 0
    if events_dir.exists():
        months = sum(1 for _ in events_dir.glob("*.jsonl"))
        for f in events_dir.glob("*.jsonl"):
            n_events += sum(1 for _ in open(f))
    rows.append(("events", n_events))
    rows.append(("event months", months))
    vault_dir = PILOT / "vault"
    rows.append(("vault entities", sum(1 for _ in vault_dir.glob("*.md")) if vault_dir.exists() else 0))
    state = PILOT / "_aggregate_state.json"
    rows.append(("aggregate state", "present" if state.exists() else "absent"))
    gp_qwen = PILOT / "qwen" / "global_profile.json"
    rows.append(("global_profile.json (qwen)", "present" if gp_qwen.exists() else "absent"))
    print("OmniGraph status")
    for k, v in rows:
        print(f"  {k:32s} {v}")
    return 0


# ----------------------------------------------------------------------
# ingest
# ----------------------------------------------------------------------

def cmd_ingest(args) -> int:
    from sources import get_adapter, list_adapters  # type: ignore
    names = [args.provider] if args.provider else list_adapters()
    for name in names:
        adapter = get_adapter(name)
        sessions = list(adapter.iter_sessions())
        print(f"[{name}] {len(sessions)} sessions available")
        if args.verbose:
            for s in sessions[: args.limit]:
                print(f"  {s.session_id}  turns={len(s.turns)} arts={len(s.artifacts)}")
    return 0


# ----------------------------------------------------------------------
# extract
# ----------------------------------------------------------------------

def cmd_extract(args) -> int:
    # Delegate to qwen_pipeline's CLI — it already knows how to look up the
    # normalized session by sid. No re-imports to avoid initializing the
    # LM Studio client in this process when we don't need it.
    py = sys.executable
    script = SRC / "qwen_pipeline.py"
    rc = 0
    for sid in args.session_id:
        r = subprocess.run([py, str(script), sid])
        rc |= r.returncode
    return rc


# ----------------------------------------------------------------------
# events / vault / aggregate / compile / index  (thin wrappers)
# ----------------------------------------------------------------------

def cmd_events(args) -> int:
    from build_events_stream import build as build_events  # type: ignore
    indirs = [Path(p) for p in (args.sessions or [PILOT / "qwen"])]
    out = _resolve_events_out(args)
    meta = build_events(indirs, out)
    print(f"✅ {meta['events_total']} events, {meta['distinct_targets']} targets → {out}")
    return 0


def cmd_vault(args) -> int:
    from build_vault import build as build_vault_fn  # type: ignore
    sessions_dirs = [Path(p) for p in (args.sessions or [PILOT / "qwen"])]
    # Events dir: atelier-path-aware or CLI --events
    if getattr(args, "atelier_root", None):
        events_dir = resolve_events_dir(args.atelier_root, args.user_id or DEFAULT_USER_ID)
    else:
        events_dir = Path(args.events)
    out = _resolve_vault_out(args)
    r = build_vault_fn(events_dir, sessions_dirs, out)
    print(f"✅ {r['entities_written']} entities → {out}")
    return 0


def cmd_aggregate(args) -> int:
    from stage2_aggregate import aggregate_full, aggregate_incremental, DEFAULT_STATE_PATH  # type: ignore
    indir = Path(args.indir)
    state_path = Path(args.state) if args.state else DEFAULT_STATE_PATH
    if args.full:
        gp = aggregate_full(indir)
        n_new = None
    else:
        gp, n_new = aggregate_incremental(indir, state_path)
    out = indir / "global_profile.json"
    out.write_text(json.dumps(gp, indent=2, ensure_ascii=False, default=str))
    print(f"✅ {out}  sessions={gp['scale']['sessions']}")
    if n_new is not None:
        print(f"   incremental: +{n_new}")
    return 0


def cmd_compile(args) -> int:
    from compiler import get_compiler, list_targets  # type: ignore
    from compiler.base import VaultState  # type: ignore
    from compiler.sanitize import sanitize_global_profile, VALID_LEVELS  # type: ignore
    if args.target not in list_targets():
        print(f"unknown target {args.target!r}; have {list_targets()}", file=sys.stderr)
        return 2
    # State-dir resolution: explicit --state wins; else atelier-aware lookup.
    if args.state:
        state_dir = Path(args.state)
        state = VaultState.from_dir(state_dir)
    elif getattr(args, "atelier_root", None):
        # Read global_profile from atelier users/<uid>/brain/personal/
        gp_path = resolve_global_profile_path(args.atelier_root, args.user_id or DEFAULT_USER_ID)
        gp = json.loads(gp_path.read_text()) if gp_path.exists() else {}
        state = VaultState(global_profile=gp)
    else:
        state = VaultState.from_dir(PILOT / "qwen")

    sanitize = getattr(args, "sanitize", "none") or "none"
    if sanitize not in VALID_LEVELS:
        print(f"bad --sanitize {sanitize!r}; have {VALID_LEVELS}", file=sys.stderr)
        return 2
    if sanitize != "none":
        state.global_profile = sanitize_global_profile(state.global_profile, sanitize)
    if getattr(args, "project", None):
        state.project_name = args.project
    text = get_compiler(args.target).compile(state, max_tokens=args.max_tokens)

    # Output path: --out wins, else atelier-aware compiled dir, else stdout.
    out_path: Path | None = None
    if args.out:
        out_path = Path(args.out).expanduser()
    elif getattr(args, "atelier_root", None):
        compiled = resolve_compiled_dir(args.atelier_root, args.user_id or DEFAULT_USER_ID)
        fname = {
            "light_ir": "light_ir.xml",
            "claude_md": "claude.md",
            "boot_context": "boot_context.json",
            "cursor_rules": "cursor.rules",
            "gemini_md": "gemini.md",
            "brain_view": "brain_view.json",
            "light_ir_global": "light_ir.global.xml",
            "light_ir_personal": "light_ir.personal.xml",
            "light_ir_project": (
                f"projects/{args.project.lower().replace(' ','-')}/brain.xml"
                if getattr(args, "project", None) else "light_ir.project.xml"
            ),
        }.get(args.target, f"{args.target}.out")
        out_path = compiled / fname

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text)
        print(f"✅ {args.target} → {out_path} ({len(text)} chars)")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def cmd_index(args) -> int:
    """In-process HR build. No subprocess."""
    from hr import build_all, load_sessions_from_extractions  # type: ignore
    dirs = args.sessions or [str(PILOT / "qwen")]
    sessions = load_sessions_from_extractions(dirs)
    print(f"📦 loaded {len(sessions)} sessions")
    bundle = build_all(
        sessions,
        min_weight=args.min_weight,
        max_targets_per_session=args.max_targets,
    )
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "cochange.json").write_text(json.dumps(bundle.cochange, indent=2))
    (out / "communities.json").write_text(json.dumps(bundle.communities, indent=2))
    (out / "criticality.json").write_text(json.dumps(bundle.criticality, indent=2))
    (out / "index_bundle.json").write_text(json.dumps(bundle.to_json(), indent=2))
    m = bundle.meta
    print(f"✅ hr bundle → {out}")
    print(f"   sessions={m['sessions']} cochange_modules={m['cochange_modules']} "
          f"cochange_pairs={m['cochange_pairs']} communities={m['communities']} "
          f"critical={m['critical_modules']}")
    for e in bundle.criticality["meta"]["top_10"]:
        print(f"   [{e['score']:.3f}] {e['module']}")
    return 0


# ----------------------------------------------------------------------
# query (stub)
# ----------------------------------------------------------------------

def cmd_query(args) -> int:
    hr_serve = Path(os.environ.get("HR_ROOT", str(Path.home() / "hyperretrieval"))) / "serve"
    print(f"`omnigraph query` requires the relationship-graph serve to be running.")
    print(f"HR serve dir: {hr_serve}")
    print("Stub for now. Once HR serve exposes an HTTP/MCP endpoint, this")
    print("subcommand will POST the question there with the HR index directory")
    print("produced by `omnigraph index`, then pretty-print results.")
    print(f"question: {args.question!r}")
    return 0


# ----------------------------------------------------------------------
# pipeline (sugar)
# ----------------------------------------------------------------------

def cmd_pipeline(args) -> int:
    # events → vault → aggregate (on the first --sessions dir by convention).
    sessions = args.sessions or [str(PILOT / "qwen")]
    # Resolve output dirs with atelier-awareness.
    uid = args.user_id or DEFAULT_USER_ID
    if args.atelier_root:
        events_dir = resolve_events_dir(args.atelier_root, uid)
        vault_dir = resolve_vault_dir(args.atelier_root, uid)
    else:
        events_dir = Path(args.events_dir) if args.events_dir else (PILOT / "events")
        vault_dir = Path(args.vault_dir) if args.vault_dir else (PILOT / "vault")
    aggregate_indir = Path(sessions[0])

    # 1. events
    from build_events_stream import build as build_events  # type: ignore
    m = build_events([Path(s) for s in sessions], events_dir)
    print(f"[events] {m['events_total']} events, {m['distinct_targets']} targets")

    # 2. vault
    from build_vault import build as build_vault_fn  # type: ignore
    r = build_vault_fn(events_dir, [Path(s) for s in sessions], vault_dir)
    print(f"[vault] {r['entities_written']} entities")

    # 3. aggregate (incremental against first sessions dir)
    from stage2_aggregate import aggregate_incremental, DEFAULT_STATE_PATH  # type: ignore
    gp, n_new = aggregate_incremental(aggregate_indir, DEFAULT_STATE_PATH)
    (aggregate_indir / "global_profile.json").write_text(
        json.dumps(gp, indent=2, ensure_ascii=False, default=str)
    )
    print(f"[aggregate] sessions={gp['scale']['sessions']} (+{n_new} new)")
    return 0


# ----------------------------------------------------------------------
# dispatch
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="omnigraph")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Summarize pilot/ state")

    p = sub.add_parser("ingest", help="Enumerate sessions via source adapters")
    p.add_argument("--provider", default=None, help="Limit to one adapter")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("extract", help="Run qwen pipeline on session(s)")
    p.add_argument("session_id", nargs="+")

    p = sub.add_parser("events", help="Build event stream")
    p.add_argument("--sessions", action="append")
    p.add_argument("--out", default=None)
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)

    p = sub.add_parser("vault", help="Build per-entity Vault")
    p.add_argument("--events", default=str(PILOT / "events"))
    p.add_argument("--sessions", action="append")
    p.add_argument("--out", default=None)
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)

    p = sub.add_parser("aggregate", help="Stage-2 aggregation")
    p.add_argument("--indir", default=str(PILOT / "qwen"))
    p.add_argument("--full", action="store_true")
    p.add_argument("--state", default=None)

    p = sub.add_parser("compile", help="Run a projection compiler")
    p.add_argument("target")
    p.add_argument("--state", default=None, help="Aggregate dir with global_profile.json (or use --atelier-root)")
    p.add_argument("--out", default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--sanitize", default="none",
                   help="Sugar ladder: none | named_stripped | entities_removed | aggregated")
    p.add_argument("--atelier-root", default=None, help="~/atelier — enables user-scoped paths")
    p.add_argument("--user-id", default=None, help="atelier_user_id (UUID); default 'default'")
    p.add_argument("--project", default=None,
                   help="Project name for light_ir_project (required for that target)")

    # compile-projects — sweep that emits one project brain per discovered scope
    p = sub.add_parser("compile-projects",
                       help="Emit per-project brain artifacts for every scope found in rules_classified.json")
    p.add_argument("--state", default=None, help="Aggregate dir with global_profile.json + rules_classified.json")
    p.add_argument("--out-dir", default=None,
                   help="Write to <out-dir>/<project-slug>/brain.xml (default: <state>/compiled/projects/)")
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--min-rules", type=int, default=2,
                   help="Skip scopes with fewer than this many project_domain rules")

    p = sub.add_parser("index", help="In-process cochange + communities + criticality")
    p.add_argument("--sessions", action="append")
    p.add_argument("--out", default=str(PILOT / "hr_out"))
    p.add_argument("--min-weight", type=int, default=None)
    p.add_argument("--max-targets", type=int, default=60)
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)

    # migrate — v0.2
    p = sub.add_parser("migrate", help="Move legacy project-scoped Personal Brain into user-scoped layout")
    p.add_argument("--atelier-root", required=True)
    p.add_argument("--user-id", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-symlink", action="store_true")

    # canonicalize — v0.2
    p = sub.add_parser("canonicalize", help="Reconcile Canvas node slugs via alias table (idempotent)")
    p.add_argument("--atelier-root", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--rewrite-canvas", action="store_true", default=True,
                   help="Write slug_canonical + canonicalized_at back into node files (default)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")

    # domain-brain audit + draft (TODO-6 minimal build)
    p = sub.add_parser("domain-brain", help="Audit a project's domain_brain/ (default) or draft a lens with Qwen")
    p.add_argument("--project-root", required=True, help="Path to ~/atelier/projects/<ProjectName>/")
    p.add_argument("--draft", default=None,
                   help="If set, generate <draft>.draft.md (kinds: industry_map, customer_personas, "
                        "current_conditions, viability_verdict, open_questions)")
    p.add_argument("--dry-run", action="store_true", help="With --draft: show prompt size without calling Qwen")
    p.add_argument("--json", action="store_true")

    # reflect — v0.3 (replaces Atelier's broken claude --print reflection-worker)
    p = sub.add_parser("reflect", help="Session-end reflection: extract + 6-lens synthesis + events write")
    p.add_argument("--session-dir", default=None, help="atelier/data/sessions/<sid>/")
    p.add_argument("--session-json", default=None, help="Alternate: pre-normalized session JSON")
    p.add_argument("--claude-jsonl", default=None,
                   help="Claude Code per-session JSONL (e.g. ~/.claude/projects/<slug>/<sid>.jsonl)")
    p.add_argument("--session-id", default=None)
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--project", default=None)
    p.add_argument("--provider", default="atelier_pty")
    p.add_argument("--lenses", type=int, default=6)
    p.add_argument("--skip-synthesis", action="store_true")
    p.add_argument("--skip-extraction", action="store_true")
    p.add_argument("--canon-only", action="store_true")
    p.add_argument("--also-compile", action="store_true")

    p = sub.add_parser("query", help="(stub) Query the indexed corpus via HR serve")
    p.add_argument("question")

    # gpu — Phase -1
    p = sub.add_parser("gpu", help="GPU lock control (status / release / pause / resume)")
    p.add_argument("action", choices=["status", "release", "pause", "resume"])

    # temporal — TODO-4
    p = sub.add_parser("open", help="List still-open decisions and unresolved concerns")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("history", help="Chronological decision history for a target_id")
    p.add_argument("target_id")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("supersession", help="Heuristic supersession chains across decisions")
    p.add_argument("--json", action="store_true")

    # reliability — TODO-5 (analytics-only; no schema mutation)
    p = sub.add_parser("reliability", help="Per-provider reliability metrics + cross-provider volume skew")
    p.add_argument("--json", action="store_true")

    # etl — Phase 0 daemon control surface (lock-aware)
    p = sub.add_parser("etl", help="ETL daemon control (pause / resume / status)")
    p.add_argument("action", choices=["pause", "resume", "status"])

    p = sub.add_parser("pipeline", help="events → vault → aggregate in one go")
    p.add_argument("--sessions", action="append")
    p.add_argument("--events-dir", default=None)
    p.add_argument("--vault-dir", default=None)
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)

    # demo — brain map generation (distribution pipeline)
    p = sub.add_parser("demo", help="Generate a brain map from AI collaboration data")
    p.add_argument("input", nargs="?", default=None, help="Path to data dir with global_profile.json (default: pilot/)")
    p.add_argument("--output", "-o", default=None, help="Output path for brain map (auto: .svg or .png)")
    p.add_argument("--state-out", default=None, help="Output path for brain_state.json")
    p.add_argument("--sanitize", default="aggregated", choices=["none", "named_stripped", "aggregated"],
                   help="Sanitization level (default: aggregated — safe for public sharing)")
    p.add_argument("--format", default=None, choices=["svg", "png"], help="Output format (auto-detected from extension)")
    p.add_argument("--atelier-root", default=None)
    p.add_argument("--user-id", default=None)

    return ap


def cmd_demo(args) -> int:
    """Run the demo pipeline: data → brain_state → brain_map."""
    from viz.demo import cmd_demo as _demo
    return _demo(args)


def cmd_migrate(args) -> int:
    from migrate import migrate  # type: ignore
    r = migrate(
        atelier_root=Path(args.atelier_root),
        user_id=args.user_id,
        project=args.project,
        dry_run=args.dry_run,
        leave_symlink=not args.no_symlink,
    )
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}target: {r['target']}")
    print(f"{tag}migrated {len(r['projects_migrated'])}: {r['projects_migrated']}")
    if r["skipped"]:
        print(f"{tag}skipped: {r['skipped']}")
    return 0


def cmd_canonicalize(args) -> int:
    from canonicalize_canvas import rewrite_canvas  # type: ignore
    r = rewrite_canvas(args.atelier_root, args.project, args.dry_run, args.force)
    if "error" in r:
        print(f"❌ {r['error']}", file=sys.stderr)
        return 2
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}project={r['project']}  scanned={r['scanned']}  rewritten={r['rewritten']}  skipped={r['skipped']}")
    for c in r["changes"][:10]:
        print(f"{tag}  {c['node_id']}: {c['raw_title']!r} → {c['new_slug']!r}")
    return 0


def cmd_domain_brain(args) -> int:
    proj = Path(args.project_root).expanduser()
    # Draft mode (TODO-6 minimal build)
    if getattr(args, "draft", None):
        from domain_brain.draft_generator import draft_one  # type: ignore
        result = draft_one(proj, args.draft, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("status") in ("drafted", "dry_run") else 1
    # Default: audit
    from domain_brain.researcher import audit_project_domain  # type: ignore
    report = audit_project_domain(proj)
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(f"project: {report.project}")
        print(f"coverage_score: {report.coverage_score:.2f}")
        print(f"next_action: {report.next_action}")
        for a in report.artifacts:
            mark = "✓" if a.exists else "✗"
            extra = []
            if a.stale: extra.append("stale")
            if a.founder_authored: extra.append("founder")
            ex = f" [{', '.join(extra)}]" if extra else ""
            print(f"  {mark} {a.kind:22s} {a.line_count if a.exists else 'missing'}{ex}")
        if report.gaps:
            print(f"gaps ({len(report.gaps)}):")
            for g in sorted(report.gaps, key=lambda x: {"blocker":0,"high":1,"medium":2,"low":3}[x.severity]):
                print(f"  [{g.severity:7s}] {g.artifact:22s} {g.question}")
    return 0


def cmd_reflect(args) -> int:
    from reflect import reflect as _reflect  # type: ignore
    return _reflect(args)


def cmd_gpu(args) -> int:
    import gpu_lock
    if args.action == "status":
        print(json.dumps(gpu_lock.status(), indent=2, default=str))
    elif args.action == "release":
        print(json.dumps(gpu_lock.force_release(), indent=2, default=str))
    elif args.action == "pause":
        print(json.dumps(gpu_lock.pause_holder(), indent=2, default=str))
    elif args.action == "resume":
        print(json.dumps(gpu_lock.resume_holder(), indent=2, default=str))
    return 0


def cmd_etl(args) -> int:
    import gpu_lock
    if args.action == "pause":
        print(json.dumps(gpu_lock.pause_holder(), indent=2, default=str))
    elif args.action == "resume":
        print(json.dumps(gpu_lock.resume_holder(), indent=2, default=str))
    elif args.action == "status":
        print(json.dumps(gpu_lock.status(), indent=2, default=str))
    return 0


def cmd_open(args) -> int:
    from temporal import cmd_open as _co
    return _co(limit=args.limit, json_out=args.json)


def cmd_history(args) -> int:
    from temporal import cmd_history as _ch
    return _ch(target_id=args.target_id, limit=args.limit, json_out=args.json)


def cmd_supersession(args) -> int:
    from temporal import cmd_supersession as _cs
    return _cs(json_out=args.json)


def cmd_reliability(args) -> int:
    from reliability import cmd_reliability as _cr
    return _cr(json_out=args.json)


def cmd_compile_projects(args) -> int:
    """Sweep: emit per-project brain.xml for every project scope discovered
    in rules_classified.json. Used by post-ETL hook."""
    from compiler import get_compiler  # type: ignore
    from compiler.base import VaultState  # type: ignore
    from compiler._layers import discovered_project_scopes, slug_for_project, rules_for_project  # type: ignore

    if args.state:
        state_dir = Path(args.state)
    elif getattr(args, "atelier_root", None):
        state_dir = PILOT / "full"   # canonical local source for compilation
    else:
        state_dir = PILOT / "full"
    state = VaultState.from_dir(state_dir)

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser()
    elif getattr(args, "atelier_root", None):
        out_dir = resolve_compiled_dir(args.atelier_root, args.user_id or DEFAULT_USER_ID) / "projects"
    else:
        out_dir = state_dir / "compiled" / "projects"
    out_dir.mkdir(parents=True, exist_ok=True)

    scopes = discovered_project_scopes(state)
    compiler = get_compiler("light_ir_project")
    written = []
    skipped = []
    for scope in scopes:
        n_rules = len(rules_for_project(state, scope))
        if n_rules < args.min_rules:
            skipped.append((scope, n_rules))
            continue
        state.project_name = scope
        text = compiler.compile(state)
        out_path = out_dir / slug_for_project(scope) / "brain.xml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text)
        written.append((scope, str(out_path), len(text)))
        print(f"  ✅ {scope:<28} → {out_path} ({len(text)} chars)")
    print(f"\nwrote {len(written)} project brains; skipped {len(skipped)} (below --min-rules {args.min_rules})")
    return 0


DISPATCH = {
    "status": cmd_status,
    "ingest": cmd_ingest,
    "extract": cmd_extract,
    "events": cmd_events,
    "vault": cmd_vault,
    "aggregate": cmd_aggregate,
    "compile": cmd_compile,
    "index": cmd_index,
    "query": cmd_query,
    "pipeline": cmd_pipeline,
    "demo": cmd_demo,
    "migrate": cmd_migrate,
    "canonicalize": cmd_canonicalize,
    "domain-brain": cmd_domain_brain,
    "reflect": cmd_reflect,
    "gpu": cmd_gpu,
    "etl": cmd_etl,
    "open": cmd_open,
    "history": cmd_history,
    "supersession": cmd_supersession,
    "reliability": cmd_reliability,
    "compile-projects": cmd_compile_projects,
}


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    fn = DISPATCH[args.cmd]
    return fn(args)


def console_main() -> int:
    """Entry point for the pip-installed `omnigraph` console script."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
