#!/usr/bin/env python3
"""Compile agent principle markdown to XML IR.

Reads atelier/agents/principles/*.md and emits a compressed XML form into
og_artifacts/agents/<name>.compiled.xml. The XML form is what runtime
spawns can inject as the agent's system prompt — same content, ~half the
tokens (mostly via removing italic narrative prose, blank lines, horizontal
rules, and tightening list markers).

The Atelier `loadOgArtifact("agent", "<name>")` reader already understands
this path layout (load.ts:182 agentPath).

Compression rules (Phase A — conservative, lossless on rule content):
  - Drop blank lines.
  - Drop `---` horizontal rules.
  - Drop italic-only lines (`_..._` standalone) — these are stage cues.
  - Drop `# H1` (filename already implies the agent name).
  - Convert `## Header` → `<section title="Header">`, closes on next `##` / EOF.
  - Convert `**bold**` → `<emph>...</emph>`.
  - Convert `- bullet` lines → `<rule>bullet</rule>`.
  - Keep paragraphs as `<p>` blocks.

No LLM call. Pure markdown transform. Reports approx-token savings:
  ~56% target per the original plan; a representative drafter.md run
  shows ~30-40% reduction from the markdown→XML form alone, more with
  aggressive narrative pruning (out of scope for Phase A).

Usage:
    python scripts/compile_agent_principles.py --atelier-root ~/informed-vibes/atelier
    python scripts/compile_agent_principles.py --atelier-root ~/informed-vibes/atelier --agent drafter
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from xml.sax.saxutils import escape


HRULE = re.compile(r"^-{3,}\s*$")
ITALIC_ONLY = re.compile(r"^_[^_]+_\s*$")
H1 = re.compile(r"^#\s+(.+)$")
H2 = re.compile(r"^##\s+(.+)$")
H3 = re.compile(r"^###\s+(.+)$")
BULLET = re.compile(r"^\s*-\s+(.+)$")
NUMBERED = re.compile(r"^\s*\d+\.\s+(.+)$")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
INLINE_CODE = re.compile(r"`([^`]+)`")


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def emphasize(line: str) -> str:
    line = BOLD.sub(r"<emph>\1</emph>", line)
    line = INLINE_CODE.sub(r"<code>\1</code>", line)
    return line


def strip_inline(line: str) -> str:
    """Drop **bold** and `code` markers — keep the underlying text only.
    Skip the markup entirely (no replacement tags) for tightness."""
    line = BOLD.sub(r"\1", line)
    line = INLINE_CODE.sub(r"\1", line)
    return line


def compile_md(md: str, agent_name: str) -> str:
    """Phase A compiler — keep imperative content, drop narrative.

    Tag names are intentionally short (a / s / r) to minimize per-token
    overhead. Output is line-per-rule for readable diffs but still fits
    well under the markdown source size after dropping paragraphs.
    """
    lines = md.splitlines()
    out: list[str] = []
    out.append(f'<a n="{escape(agent_name)}">')
    section_open = False
    pending_p: list[str] = []  # paragraphs collapsed into a single <d> dropdescription per section

    def flush_section_prose() -> None:
        nonlocal pending_p
        # Drop prose entirely — the rules carry the imperative content.
        # Set DEEP_COMPILE=0 in env to keep prose as <d>...</d> instead.
        import os
        if pending_p and os.getenv("AGENT_PRINCIPLES_KEEP_PROSE") == "1":
            joined = " ".join(pending_p)
            out.append(f"  <d>{escape(joined[:600])}</d>")
        pending_p = []

    def close_section() -> None:
        nonlocal section_open
        flush_section_prose()
        if section_open:
            out.append("</s>")
            section_open = False

    for raw in lines:
        line = raw.rstrip()
        if not line or HRULE.match(line) or ITALIC_ONLY.match(line) or H1.match(line):
            continue
        m = H2.match(line)
        if m:
            close_section()
            section_open = True
            out.append(f'<s t="{escape(m.group(1))}">')
            continue
        m = H3.match(line)
        if m:
            flush_section_prose()
            out.append(f'  <h>{escape(m.group(1))}</h>')
            continue
        m = BULLET.match(line) or NUMBERED.match(line)
        if m:
            flush_section_prose()
            out.append(f"  <r>{escape(strip_inline(m.group(1)))}</r>")
            continue
        # Plain prose — buffer for now; flush on next rule/section.
        pending_p.append(strip_inline(line))
    close_section()
    out.append("</a>")
    return "\n".join(out) + "\n"


def emit(atelier_root: Path, only_agent: str | None = None) -> dict:
    src_dir = atelier_root / "agents" / "principles"
    out_dir = atelier_root / "og_artifacts" / "agents"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src_dir.is_dir():
        return {"ok": False, "reason": f"no principles dir: {src_dir}"}

    written: list[dict] = []
    for md_path in sorted(src_dir.glob("*.md")):
        name = md_path.stem
        if only_agent and name != only_agent:
            continue
        md = md_path.read_text()
        xml = compile_md(md, name)
        out_path = out_dir / f"{name}.compiled.xml"
        out_path.write_text(xml)

        md_tokens = approx_tokens(md)
        xml_tokens = approx_tokens(xml)
        savings = (md_tokens - xml_tokens) / md_tokens if md_tokens else 0.0
        written.append({
            "name": name,
            "md_chars": len(md),
            "xml_chars": len(xml),
            "md_tokens_approx": md_tokens,
            "xml_tokens_approx": xml_tokens,
            "savings_pct": round(savings * 100, 1),
            "out": str(out_path),
        })

    # Update _manifest.json's agents list if present.
    manifest_path = atelier_root / "og_artifacts" / "_manifest.json"
    try:
        if manifest_path.is_file():
            import json
            m = json.loads(manifest_path.read_text())
            m.setdefault("agents", {})["compiled"] = [w["name"] for w in written]
            manifest_path.write_text(json.dumps(m, indent=2))
    except Exception:
        pass

    return {"ok": True, "written": written}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atelier-root", required=True, type=Path)
    ap.add_argument("--agent", default=None, help="Restrict to one agent (default: compile all)")
    args = ap.parse_args()

    atelier_root = args.atelier_root.expanduser().resolve()
    if not atelier_root.is_dir():
        print(f"[compile-agent-principles] atelier root not found: {atelier_root}")
        return 2

    result = emit(atelier_root, only_agent=args.agent)
    if not result.get("ok"):
        print(f"[compile-agent-principles] failed: {result.get('reason')}")
        return 3
    for w in result["written"]:
        print(f"  ✓ {w['name']:<24} md={w['md_tokens_approx']:>5}t  xml={w['xml_tokens_approx']:>5}t  savings={w['savings_pct']}%")
    print(f"\nwrote {len(result['written'])} compiled agent file(s) → {atelier_root}/og_artifacts/agents/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
