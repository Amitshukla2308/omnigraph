#!/usr/bin/env python3
"""Materialize per-entity Vault pages from the event stream + per-session JSONs.

Inputs:
  <events_dir>/<YYYY-MM>.jsonl       (from build_events_stream.py)
  <events_dir>/index.json
  <sessions_dir>/<provider>/<sid>.json   (original Qwen extractions — for
                                          Decisions, Concerns, Rules context)

Output:
  <vault_dir>/<canonical_target_id>.md   — one per entity
  <vault_dir>/INDEX.md                   — top-level listing

Each entity page:
  YAML frontmatter (target_id, type, aliases, first/last seen, counts, status)
  # <target_id>
  ## Summary (derived — top co-mentions + dominant valence)
  ## Load-bearing decisions
  ## Concerns
  ## Rules touching this entity
  ## Mention log (chronological, [[session_...]] backlinks)

Usage:
  python build_vault.py <events_dir> <sessions_dir> <vault_dir>
  python build_vault.py pilot/events pilot/qwen pilot/vault
"""
from __future__ import annotations
import datetime as _dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from canonical_slugs import canonicalize_session, canonicalize_slug  # type: ignore
except Exception:
    def canonicalize_session(s):
        return s
    def canonicalize_slug(x):
        return x


SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
try:
    NOW = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)  # py3.11+
except AttributeError:  # pragma: no cover
    NOW = _dt.datetime.utcnow()


def _safe_name(tid: str) -> str:
    return SAFE_FILENAME_RE.sub("-", tid).strip("-") or "unnamed"


def _days_since(ts: str) -> float:
    if not ts:
        return 1e9
    try:
        t = _dt.datetime.fromisoformat(ts.replace("Z", ""))
        return (NOW - t).total_seconds() / 86400
    except Exception:
        return 1e9


def _status_from_last_seen(last_seen: str) -> str:
    d = _days_since(last_seen)
    if d <= 30:
        return "active"
    if d <= 180:
        return "dormant"
    return "archived"


def _load_events(events_dir: Path) -> list[dict]:
    events: list[dict] = []
    for f in sorted(events_dir.glob("*.jsonl")):
        for line in f.open():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def _load_sessions(sessions_dirs) -> dict[str, dict]:
    """session_id -> full session dict (canonicalized). Accepts one or many dirs."""
    if isinstance(sessions_dirs, (str, Path)):
        sessions_dirs = [Path(sessions_dirs)]
    else:
        sessions_dirs = [Path(p) for p in sessions_dirs]
    out: dict[str, dict] = {}
    for d in sessions_dirs:
        for f in sorted(d.glob("*/*.json")):
            stem = f.stem
            if stem == "global_profile" or "_logs" in str(f) or "_run_summary" in stem:
                continue
            try:
                s = json.loads(f.read_text())
            except Exception:
                continue
            canonicalize_session(s)
            sid = str(s.get("session_id") or stem)
            if sid not in out:
                out[sid] = s
    return out


def _co_mentions_by_session(events: list[dict]) -> dict[str, Counter]:
    """For each target_id, count co-occurring target_ids across shared sessions."""
    by_session: dict[str, set] = defaultdict(set)
    for e in events:
        by_session[e["session_id"]].add(e["target_id"])
    co: dict[str, Counter] = defaultdict(Counter)
    for sid, tids in by_session.items():
        tids_list = list(tids)
        for i, a in enumerate(tids_list):
            for b in tids_list[i + 1:]:
                co[a][b] += 1
                co[b][a] += 1
    return co


def _collect_aliases(events: list[dict]) -> dict[str, set]:
    aliases: dict[str, set] = defaultdict(set)
    for e in events:
        ma = e.get("mentioned_as")
        if ma and ma != e["target_id"]:
            aliases[e["target_id"]].add(ma)
    return aliases


def _dominant(seq: list[str]) -> str | None:
    if not seq:
        return None
    c = Counter(x for x in seq if x)
    return c.most_common(1)[0][0] if c else None


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, str):
        # Quote only if strictly necessary.
        if re.search(r"[:#\[\]{}&*!|>'\"%@`]", v) or "\n" in v:
            return json.dumps(v)
        return v
    if isinstance(v, (list, tuple)):
        if not v:
            return "[]"
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"
    return json.dumps(v)


def _frontmatter(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _build_one_page(tid: str, events_for_tid: list[dict], all_sessions: dict[str, dict],
                    aliases: set, co_counter: Counter) -> str:
    events_for_tid = sorted(events_for_tid, key=lambda e: e.get("ts") or "")
    target_type = _dominant([e.get("target_type") for e in events_for_tid]) or "Unknown"
    first_seen = events_for_tid[0].get("ts") or ""
    last_seen = events_for_tid[-1].get("ts") or ""
    mention_count = len(events_for_tid)
    providers = sorted({e.get("provider") for e in events_for_tid if e.get("provider")})
    valence_counts = Counter(e.get("valence") for e in events_for_tid if e.get("valence"))
    top_co = [x for x, _ in co_counter.most_common(6)]

    fm = _frontmatter({
        "target_id": tid,
        "target_type": target_type,
        "aliases": sorted(aliases),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "mention_count": mention_count,
        "providers": providers,
        "co_mentioned_top": top_co,
        "status": _status_from_last_seen(last_seen),
    })

    out = [fm, f"# {tid}\n"]

    # Summary
    dominant_val = valence_counts.most_common(1)[0][0] if valence_counts else "—"
    out.append("## Summary\n")
    out.append(
        f"Dominant valence: **{dominant_val}**. "
        f"Seen across providers: {', '.join(providers) or '—'}. "
        f"Status: **{_status_from_last_seen(last_seen)}** "
        f"(last seen {last_seen or '—'}).\n\n"
    )

    # Load-bearing decisions (from session.decisions where related_entities include tid)
    decisions_lines: list[str] = []
    for e in events_for_tid:
        sid = e["session_id"]
        sess = all_sessions.get(sid)
        if not sess:
            continue
        for dec in (sess.get("decisions") or []):
            if not isinstance(dec, dict):
                continue
            rel = dec.get("related_entities") or []
            if tid in rel or dec.get("target_id") == tid:
                text = dec.get("decision") or dec.get("text") or ""
                why = dec.get("why") or ""
                marker = f"- [[session_{sid}]] — {text[:180]}"
                if why:
                    marker += f" _(why: {why[:100]})_"
                if marker not in decisions_lines:
                    decisions_lines.append(marker)
    if decisions_lines:
        out.append("## Load-bearing decisions\n")
        out.extend([l + "\n" for l in decisions_lines[:15]])
        out.append("\n")

    # Concerns (from mention_events of type concern_*)
    concerns = [e for e in events_for_tid if (e.get("mention_type") or "").startswith("concern")]
    if concerns:
        out.append("## Concerns\n")
        for e in concerns[:20]:
            sid = e["session_id"]
            mt = e.get("mention_type")
            q = (e.get("evidence_quote") or "").strip().replace("\n", " ")[:180]
            out.append(f"- [[session_{sid}]] — **{mt}** — {q}\n")
        out.append("\n")

    # Rules touching this entity
    rules_lines: list[str] = []
    for e in events_for_tid:
        sid = e["session_id"]
        sess = all_sessions.get(sid)
        if not sess:
            continue
        for r in (sess.get("rules") or []):
            if not isinstance(r, dict):
                continue
            applies = r.get("applies_to") or ""
            text = r.get("rule_text") or ""
            if tid in applies or tid in text:
                line = f"- [[session_{sid}]] — {text[:180]}"
                if line not in rules_lines:
                    rules_lines.append(line)
    if rules_lines:
        out.append("## Rules touching this entity\n")
        out.extend([l + "\n" for l in rules_lines[:15]])
        out.append("\n")

    # Mention log (chronological)
    out.append("## Mention log\n")
    for e in events_for_tid:
        ts = (e.get("ts") or "")[:10] or "—"
        sid = e["session_id"]
        mt = e.get("mention_type") or "reference"
        a = e.get("authorship") or "?"
        out.append(f"- {ts} — [[session_{sid}]] — {mt} ({a})\n")
    out.append("\n")

    return "".join(out)


def _build_index(vault_dir: Path, written: list[tuple[str, str]]) -> None:
    written_sorted = sorted(written, key=lambda t: t[0].lower())
    lines = ["# Vault index\n", f"_{len(written_sorted)} entities_\n\n"]
    for tid, fname in written_sorted:
        lines.append(f"- [[{fname[:-3]}]] — `{tid}`\n")
    (vault_dir / "INDEX.md").write_text("".join(lines))


def build(events_dir: Path, sessions_dirs, vault_dir: Path) -> dict:
    events_dir = Path(events_dir)
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    # Clean previous pages (idempotent full rebuild).
    for old in vault_dir.glob("*.md"):
        old.unlink()

    events = _load_events(events_dir)
    sessions = _load_sessions(sessions_dirs)
    aliases_by_tid = _collect_aliases(events)
    co = _co_mentions_by_session(events)

    events_by_tid: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        events_by_tid[e["target_id"]].append(e)

    written: list[tuple[str, str]] = []
    for tid, ev_list in events_by_tid.items():
        md = _build_one_page(
            tid=tid,
            events_for_tid=ev_list,
            all_sessions=sessions,
            aliases=aliases_by_tid.get(tid, set()),
            co_counter=co.get(tid, Counter()),
        )
        fname = f"{_safe_name(tid)}.md"
        (vault_dir / fname).write_text(md)
        written.append((tid, fname))

    _build_index(vault_dir, written)
    return {
        "entities_written": len(written),
        "events_read": len(events),
        "sessions_loaded": len(sessions),
    }


def main(argv: list[str]) -> int:
    # build_vault.py <events_dir> <sessions_dir> [<sessions_dir2> ...] <vault_dir>
    if len(argv) < 3:
        print("usage: build_vault.py <events_dir> <sessions_dir> [<more_sessions_dirs>...] <vault_dir>",
              file=sys.stderr)
        return 2
    events_dir = Path(argv[0])
    vault_dir = Path(argv[-1])
    sessions_dirs = [Path(a) for a in argv[1:-1]]
    r = build(events_dir, sessions_dirs, vault_dir)
    print(f"✅ Vault: {r['entities_written']} entities from "
          f"{r['events_read']} events / {r['sessions_loaded']} sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
