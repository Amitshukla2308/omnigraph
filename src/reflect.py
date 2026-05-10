#!/usr/bin/env python3
"""`omnigraph reflect` — cycle-end / session-end reflection backend for Atelier.

Replaces Atelier's `claude --print`-based reflection-worker (which has been
failing with repeated timeouts per backend/PHASE_A_BLOCKERS.md). Runs local
Qwen — no network, no Anthropic tokens, no auth plumbing.

Pipeline:
  1. Input:  either a normalized session JSON or a raw PTY log + session
             metadata under `atelier/data/sessions/<sid>/`.
  2. Extract: 7-phase Qwen extraction (existing `qwen_pipeline.run_session`).
  3. Synth:   6-lens synthesis pass (new — Engineer / Architect / Strategist /
              Economist / Scientist / Product).
  4. Write:
       - `atelier/projects/<P>/sessions/<sid>.md`       — 6-lens reflection
       - `atelier/users/<uid>/data/events/<YYYY-MM>.jsonl` — raw mention events
       - triggers async aggregate + compile (if --also-compile)

Exit codes (per omnigraph↔atelier Turn 5):
  0  all stages wrote successfully
  1  Qwen / LM Studio unreachable (retry-eligible on Atelier's side)
  2  session malformed or empty (do NOT retry; mark failed)
  3  extraction OK, downstream aggregate/compile failed (retry aggregate alone)
  4  canonicalize-only mode success (--skip-synthesis), partial output

Stderr on failure is a single-line JSON object:
    {"error": "<short msg>", "phase": "<phase where it failed>"}
so Atelier's reflection-worker can forward it over WebSocket without parsing.

Usage:
    omnigraph reflect --session-dir atelier/data/sessions/<sid> \\
                      --atelier-root ~/atelier \\
                      --user-id <uuid> \\
                      --project MyProject
    # Options:
    #   --lenses 6            (default; set 0 to skip synthesis)
    #   --skip-synthesis      (equivalent to --lenses 0)
    #   --skip-extraction     (reuse existing pilot/qwen/<sid>.json if present)
    #   --also-compile        (run aggregate + compile after reflect — fire-and-forget)
    #   --session-json <path> (alternate: give me a pre-normalized session JSON directly)
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

# Ensure our package paths resolve whether run as `python src/reflect.py ...`
# or as `python -m reflect ...`
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from paths import (  # type: ignore  # noqa: E402
    DEFAULT_USER_ID,
    atelier_project_root,
    atelier_session_raw_dir,
    atelier_sessions_dir,
    resolve_hr_events_jsonl_month,
)
from lenses import LENSES, lens_prompt, render_session_brief  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# Exit codes + stderr protocol
# ----------------------------------------------------------------------

EXIT_OK = 0
EXIT_QWEN_UNREACHABLE = 1
EXIT_SESSION_MALFORMED = 2
EXIT_DOWNSTREAM_FAILED = 3
EXIT_CANON_ONLY = 4


def _fail(phase: str, error: str, code: int) -> int:
    """Emit a one-line JSON to stderr and return the exit code."""
    print(json.dumps({"error": error, "phase": phase}), file=sys.stderr, flush=True)
    return code


# ----------------------------------------------------------------------
# Raw PTY parser — best-effort, used when no structured session JSON exists
# ----------------------------------------------------------------------

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_USER_MARKER_RE = re.compile(r"^\s*(?:>|\$|user:)\s*", re.IGNORECASE | re.MULTILINE)
_ASSISTANT_MARKER_RE = re.compile(r"^\s*(?:assistant:|claude:|gemini:)\s*", re.IGNORECASE | re.MULTILINE)


def _strip_ansi(s: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", s)


def _parse_raw_pty_log(raw_log_path: Path, session_id: str, provider: str = "atelier_pty") -> dict:
    """Best-effort PTY parse: raw.log → normalized session dict.

    The heuristic: strip ANSI, split into paragraphs, classify by loose markers.
    Atelier is expected to emit structured session.json alongside raw.log when
    possible — this parser is a fallback so reflect doesn't block on missing
    structure.
    """
    try:
        raw = raw_log_path.read_text(errors="replace")
    except Exception as e:
        raise ValueError(f"unreadable raw.log: {e}")
    if not raw.strip():
        raise ValueError("empty raw.log")

    clean = _strip_ansi(raw)
    # Split on blank lines; classify each chunk.
    chunks = [c.strip() for c in re.split(r"\n\s*\n", clean) if c.strip()]
    turns: list[dict] = []
    idx = 0
    for c in chunks:
        role = "assistant"
        if _USER_MARKER_RE.search(c):
            role = "user"
        elif _ASSISTANT_MARKER_RE.search(c):
            role = "assistant"
        # Strip leading markers
        body = _USER_MARKER_RE.sub("", c, count=1)
        body = _ASSISTANT_MARKER_RE.sub("", body, count=1).strip()
        if not body:
            continue
        turns.append({
            "index": idx,
            "role": role,
            "timestamp": None,
            "text": body[:8000],
            "thinking": "",
        })
        idx += 1

    if not turns:
        raise ValueError("parsed zero turns from raw.log (unknown format)")

    return {
        "session_id": session_id,
        "provider": provider,
        "source_path": str(raw_log_path),
        "input_type": "dialog",
        "turns": turns,
    }


def _load_from_claude_jsonl(jsonl_path: Path, sid: str) -> dict:
    """Normalize a Claude Code per-session JSONL through the existing adapter.

    Claude Code writes ~/.claude/projects/<slugified-cwd>/<sid>.jsonl with
    one line per turn (user / assistant / system). Our ClaudeCodeAdapter
    already handles this shape; we just need to read the single file.
    """
    import io
    from sources.base import flatten_content, coerce_ts  # type: ignore

    if not jsonl_path.exists():
        raise ValueError(f"--claude-jsonl not found: {jsonl_path}")

    turns: list[dict] = []
    idx = 0
    for line in jsonl_path.open(errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        if t not in ("user", "assistant"):
            continue
        msg = o.get("message", {}) or {}
        role = msg.get("role", t)
        if role in {"tool", "tool_result", "function"}:
            continue
        fc = flatten_content(msg.get("content", ""))
        if not (fc["text"] or fc["thinking"]):
            continue
        turns.append({
            "index": idx,
            "role": role,
            "timestamp": coerce_ts(o.get("timestamp")),
            "text": fc["text"],
            "thinking": fc["thinking"],
        })
        idx += 1

    if not turns:
        raise ValueError(f"zero usable turns in {jsonl_path}")

    return {
        "session_id": sid,
        "provider": "claude_code",
        "source_path": str(jsonl_path),
        "input_type": "dialog",
        "turns": turns,
    }


def _load_session(args) -> dict:
    """Load the normalized session dict via one of four paths (in priority order):

      1. --claude-jsonl <path>              Claude Code per-session JSONL (preferred;
                                            ANSI-free; normalized via existing adapter)
      2. --session-json <path>              explicit normalized session JSON
      3. <session_dir>/session.json         Atelier's structured output if present
      4. <session_dir>/raw.log              last resort: ANSI-strip PTY parser

    Raises ValueError on malformed/empty input.
    """
    sid_hint = args.session_id
    if not sid_hint and args.session_dir:
        sid_hint = Path(args.session_dir).name
    elif not sid_hint and args.claude_jsonl:
        sid_hint = Path(args.claude_jsonl).stem

    if getattr(args, "claude_jsonl", None):
        return _load_from_claude_jsonl(Path(args.claude_jsonl), sid_hint or "unknown")

    if args.session_json:
        p = Path(args.session_json)
        if not p.exists():
            raise ValueError(f"--session-json not found: {p}")
        try:
            return json.loads(p.read_text())
        except Exception as e:
            raise ValueError(f"--session-json invalid: {e}")

    if not args.session_dir:
        raise ValueError("one of --claude-jsonl, --session-json, or --session-dir is required")

    sess_dir = Path(args.session_dir)
    if not sess_dir.exists():
        raise ValueError(f"--session-dir not found: {sess_dir}")

    sid = sid_hint or sess_dir.name
    structured = sess_dir / "session.json"
    if structured.exists():
        try:
            d = json.loads(structured.read_text())
            d.setdefault("session_id", sid)
            d.setdefault("provider", args.provider or "atelier_pty")
            return d
        except Exception as e:
            raise ValueError(f"session.json malformed: {e}")

    raw_log = sess_dir / "raw.log"
    if raw_log.exists():
        return _parse_raw_pty_log(raw_log, sid, provider=args.provider or "atelier_pty")

    raise ValueError(f"no session.json or raw.log under {sess_dir}")


# ----------------------------------------------------------------------
# Extraction + 6-lens synthesis
# ----------------------------------------------------------------------

def _run_extraction(normalized_session: dict, sid: str) -> dict:
    """Invoke qwen_pipeline.run_session-equivalent in-process.

    Returns the extracted object graph (mention_events, decisions, etc.).
    """
    # Import locally to keep startup fast when reflect is invoked with --skip-extraction.
    from qwen_pipeline import run_session as pipeline_run  # type: ignore

    # qwen_pipeline expects a normalized JSON file on disk. Write it to a tmp
    # location the pipeline can read.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        norm_path = Path(td) / f"{sid}.json"
        norm_path.write_text(json.dumps(normalized_session))
        out_path = Path(td) / f"{sid}.extracted.json"
        log_path = Path(td) / f"{sid}.log.json"
        try:
            pipeline_run(norm_path, out_path, log_path)
        except Exception as e:
            # Best-effort classification of Qwen-unreachability
            msg = str(e).lower()
            if "connection" in msg or "timeout" in msg or "refused" in msg or "unreachable" in msg:
                raise _QwenUnreachable(str(e))
            raise
        return json.loads(out_path.read_text())


class _QwenUnreachable(RuntimeError):
    pass


def _run_lens_synthesis(extracted: dict) -> dict[str, str]:
    """Run each of the 6 lens prompts and return {lens_name: markdown_body}.

    Qwen 3.6 is a thinking model: it spends a large chunk of max_tokens on
    internal reasoning before emitting the output. Budgeting 2048 tokens
    total caused 5/6 lenses to return empty content on the pilot project
    session (thinking-overrun, finish_reason=length). v0.4.1 fix: start at
    8192, retry once at 16384 on length-overrun with empty content.
    """
    from qwen_pipeline import qwen_call  # type: ignore

    LENS_BASE_TOKENS = 8192
    LENS_RETRY_TOKENS = 16384

    brief = render_session_brief(extracted)
    out: dict[str, str] = {}
    for lens in LENSES:
        system, user = lens_prompt(lens, brief)
        try:
            resp = qwen_call(system, user, max_tokens=LENS_BASE_TOKENS)
            content = (resp.get("content") or "").strip()
            # Thinking-overrun retry: empty content + finish_reason=length means
            # the model burned all budget on reasoning before emitting output.
            if not content and resp.get("finish_reason") == "length":
                resp = qwen_call(system, user, max_tokens=LENS_RETRY_TOKENS)
                content = (resp.get("content") or "").strip()
        except Exception as e:
            msg = str(e).lower()
            if "connection" in msg or "timeout" in msg or "refused" in msg or "unreachable" in msg:
                raise _QwenUnreachable(str(e))
            raise

        if not content:
            content = f"### {lens.title()}\n\n_(lens produced no content — extraction may be thin)_\n"
        out[lens] = content
    return out


def _compose_reflection_md(
    extracted: dict,
    lens_sections: dict[str, str],
    user_id: str,
    project: str,
) -> str:
    sid = extracted.get("session_id", "?")
    provider = extracted.get("provider", "?")
    now_iso = _dt.datetime.utcnow().isoformat() + "Z"

    fm = [
        "---",
        f"session_id: {sid}",
        f"project: {project}",
        f"atelier_user_id: {user_id}",
        f"provider: {provider}",
        f"produced_at: {now_iso}",
        f"produced_by: omnigraph/reflect (qwen-3.6-35b-a3b)",
        f"lens_count: {len(lens_sections)}",
        "---",
        "",
    ]

    body = ["# Session reflection\n"]
    for lens in LENSES:
        section = lens_sections.get(lens)
        if not section:
            continue
        # Ensure each lens block starts with its H3; prepend if not.
        if not section.lstrip().startswith("###"):
            section = f"### {lens.title()}\n\n{section}"
        body.append(section.rstrip() + "\n")

    return "\n".join(fm) + "\n".join(body) + "\n"


# ----------------------------------------------------------------------
# Events writer
# ----------------------------------------------------------------------

def _write_events_month(
    extracted: dict,
    atelier_root: Path,
    user_id: str,
    project: str,
) -> int:
    """Append this session's mention_events into the user-scoped month-jsonl.
    Returns count of events written."""
    evs = extracted.get("mention_events") or []
    if not evs:
        return 0
    meta = extracted.get("session_meta") or {}
    ts_fallback = meta.get("timestamp_start") or meta.get("timestamp_end") or ""
    sid = extracted.get("session_id", "?")
    provider = extracted.get("provider", "?")

    # Bucket by month derived from event ts (fall back to session ts)
    def _ym(ts: str) -> str:
        return ts[:7] if ts and len(ts) >= 7 else _dt.datetime.utcnow().strftime("%Y-%m")

    by_month: dict[str, list[dict]] = {}
    for ev in evs:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("timestamp") or ts_fallback or ""
        ym = _ym(ts)
        record = {
            "ts": ts,
            "session_id": sid,
            "provider": provider,
            "project": project,
            "target_id": ev.get("target_id"),
            "target_type": ev.get("target_type"),
            "mention_type": ev.get("mention_type"),
            "authorship": ev.get("authorship"),
            "valence": ev.get("valence"),
            "evidence_quote": (ev.get("evidence_quote") or "")[:400],
            "mentioned_as": ev.get("mentioned_as"),
        }
        by_month.setdefault(ym, []).append(record)

    total = 0
    for ym, recs in by_month.items():
        out_path = resolve_hr_events_jsonl_month(atelier_root, user_id, ym)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                total += 1
    return total


# ----------------------------------------------------------------------
# Orchestrator (callable from Python too)
# ----------------------------------------------------------------------

def reflect(args) -> int:
    # ---- 0. Resolve inputs
    atelier_root = Path(args.atelier_root).expanduser() if args.atelier_root else None
    user_id = args.user_id or DEFAULT_USER_ID
    project = args.project or "default"

    # ---- 1. Load session
    try:
        normalized = _load_session(args)
    except Exception as e:
        return _fail("load", str(e), EXIT_SESSION_MALFORMED)

    sid = normalized.get("session_id") or args.session_id or "unknown"

    # ---- 2. Extract (unless --skip-extraction and existing extraction found)
    extracted: dict | None = None
    if args.skip_extraction:
        # Look under the canonical pilot/qwen path for an existing extraction.
        from paths import PILOT_ROOT  # type: ignore
        for cand in (PILOT_ROOT / "qwen").glob(f"*/{sid}.json"):
            try:
                extracted = json.loads(cand.read_text())
                break
            except Exception:
                continue
        if extracted is None:
            return _fail("extract", f"--skip-extraction set but no existing extraction for {sid}", EXIT_SESSION_MALFORMED)
    else:
        try:
            extracted = _run_extraction(normalized, sid)
        except _QwenUnreachable as e:
            return _fail("extract", f"qwen unreachable: {e}", EXIT_QWEN_UNREACHABLE)
        except Exception as e:
            return _fail("extract", str(e), EXIT_SESSION_MALFORMED)

    if not isinstance(extracted, dict):
        return _fail("extract", "extractor returned non-dict", EXIT_SESSION_MALFORMED)

    # ---- 3. 6-lens synthesis (unless skipped)
    lens_sections: dict[str, str] = {}
    if args.lenses and args.lenses > 0 and not args.skip_synthesis:
        try:
            lens_sections = _run_lens_synthesis(extracted)
        except _QwenUnreachable as e:
            return _fail("synthesis", f"qwen unreachable: {e}", EXIT_QWEN_UNREACHABLE)
        except Exception as e:
            # Synthesis is non-fatal for events + reflection scaffolding.
            print(json.dumps({"warning": f"lens synthesis failed: {e}", "phase": "synthesis"}),
                  file=sys.stderr, flush=True)

    # ---- 4. Write outputs
    wrote_paths: list[str] = []

    # 4a. Reflection markdown
    if atelier_root and project:
        reflections_dir = atelier_sessions_dir(atelier_root, project)
        reflections_dir.mkdir(parents=True, exist_ok=True)
        refl_path = reflections_dir / f"{sid}.md"
        md = _compose_reflection_md(extracted, lens_sections, user_id, project)
        refl_path.write_text(md)
        wrote_paths.append(str(refl_path))

    # 4b. Events jsonl (user-scoped)
    events_written = 0
    if atelier_root:
        try:
            events_written = _write_events_month(extracted, atelier_root, user_id, project)
        except Exception as e:
            return _fail("write_events", str(e), EXIT_DOWNSTREAM_FAILED)

    # 4c. Optional: aggregate + compile trigger (fire-and-forget via subprocess)
    if args.also_compile and atelier_root:
        import subprocess
        py = sys.executable
        cli = _THIS_DIR / "omnigraph_cli.py"
        try:
            subprocess.Popen(
                [py, str(cli), "aggregate", "--indir", str(atelier_root / "users" / user_id / "brain" / "personal")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            # Don't fail the whole reflect on this — log and continue.
            print(json.dumps({"warning": f"aggregate trigger failed: {e}", "phase": "trigger"}),
                  file=sys.stderr, flush=True)

    # ---- 5. Final report
    summary = {
        "ok": True,
        "session_id": sid,
        "project": project,
        "user_id": user_id,
        "lens_count": len(lens_sections),
        "events_written": events_written,
        "artifacts": wrote_paths,
    }
    if args.canon_only:
        summary["canonicalize_only"] = True
        print(json.dumps(summary), file=sys.stdout, flush=True)
        return EXIT_CANON_ONLY
    print(json.dumps(summary), file=sys.stdout, flush=True)
    return EXIT_OK


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="OmniGraph reflection backend for Atelier")
    ap.add_argument("--session-dir", default=None, help="atelier/data/sessions/<sid>/")
    ap.add_argument("--session-json", default=None, help="Alternate: pre-normalized session JSON")
    ap.add_argument("--claude-jsonl", default=None,
                    help="Claude Code per-session JSONL (e.g. ~/.claude/projects/<slug>/<sid>.jsonl)")
    ap.add_argument("--session-id", default=None, help="Override sid (else derived from dir/name)")
    ap.add_argument("--atelier-root", default=None)
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--provider", default="atelier_pty")
    ap.add_argument("--lenses", type=int, default=6, help="0 to skip synthesis, else 6 (default)")
    ap.add_argument("--skip-synthesis", action="store_true", help="Equivalent to --lenses 0")
    ap.add_argument("--skip-extraction", action="store_true",
                    help="Reuse existing pilot/qwen/<sid>.json instead of re-extracting")
    ap.add_argument("--canon-only", action="store_true",
                    help="Stop after canonicalize + events write; skip synthesis + reflection md")
    ap.add_argument("--also-compile", action="store_true", help="Trigger aggregate+compile post-reflect")
    args = ap.parse_args(argv)

    if args.skip_synthesis:
        args.lenses = 0

    if not (args.session_dir or args.session_json or args.claude_jsonl):
        return _fail("args", "one of --session-dir, --session-json, or --claude-jsonl is required", EXIT_SESSION_MALFORMED)

    return reflect(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
