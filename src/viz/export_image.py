"""Export brain map as a shareable image (SVG + PNG).

Produces a 1200×630 PNG optimized for LinkedIn/Twitter sharing.
Default: aggregated-only mode (no entity names, only region densities
+ hypothesis verdicts) — the "public tweet-ready" format from 07_BRAIN_VIZ.md.

Usage:
  python -m viz.export_image --input brain_state.json --output brain_map.png
  python -m viz.export_image --input brain_state.json --output brain_map.svg --format svg
  python -m viz.export_image --input brain_state.json --output brain_map.png --sanitize aggregated
"""
from __future__ import annotations
import json
import math
import time
from pathlib import Path
from typing import Any

# Canvas dimensions
W = 1200
H = 630

# Region definitions: anatomical positions with proper brain proportions
# Canvas: 1200×630, brain centered around (580, 310)
REGION_DEFS = {
    "prefrontal":        {"cx": 480, "cy": 220, "rx": 110, "ry": 90, "label": "Prefrontal"},
    "motor":             {"cx": 620, "cy": 170, "rx": 75, "ry": 60, "label": "Motor"},
    "hippocampus":       {"cx": 560, "cy": 380, "rx": 60, "ry": 50, "label": "Hippocampus"},
    "amygdala":          {"cx": 650, "cy": 320, "rx": 45, "ry": 45, "label": "Amygdala"},
    "brainstem":         {"cx": 480, "cy": 460, "rx": 55, "ry": 65, "label": "Brainstem"},
    "sensory":           {"cx": 370, "cy": 210, "rx": 70, "ry": 55, "label": "Sensory"},
    "anterior_cingulate":{"cx": 520, "cy": 290, "rx": 65, "ry": 60, "label": "Ant. Cingulate"},
    "corpus_callosum":   {"cx": 540, "cy": 250, "rx": 95, "ry": 28, "label": "Corpus Callosum"},
}

# Region colors (design spec palette)
REGION_COLORS = {
    "prefrontal":        {"r": 251, "g": 191, "b": 36},   # amber/gold
    "motor":             {"r": 251, "g": 113, "b": 133},  # coral/crimson
    "hippocampus":       {"r": 16, "g": 185, "b": 129},   # emerald/teal
    "amygdala":          {"r": 232, "g": 121, "b": 249},  # magenta/hot-pink
    "brainstem":         {"r": 34, "g": 211, "b": 238},   # cyan/sky
    "sensory":           {"r": 250, "g": 204, "b": 21},   # yellow/lime
    "anterior_cingulate":{"r": 251, "g": 146, "b": 60},   # orange/red
    "corpus_callosum":   {"r": 167, "g": 139, "b": 250},  # violet/indigo
}

# Hypothesis group colors
GROUP_COLORS = {
    "collab": "#8b5cf6",
    "cogload": "#06b6d4",
    "cross": "#f59e0b",
    "toolfit": "#10b981",
}

# Brain silhouette path — anatomically-accurate lateral view (left-facing)
# Used as clip mask for particle rendering
BRAIN_SILHOUETTE = """
M 300 480
C 280 460, 260 420, 250 370
C 240 320, 245 270, 260 230
C 275 190, 300 160, 340 140
C 380 120, 430 105, 490 100
C 550 95, 620 98, 680 115
C 740 132, 790 160, 820 200
C 850 240, 860 290, 850 340
C 840 390, 810 430, 770 455
C 730 480, 680 495, 620 500
C 560 505, 490 505, 430 498
C 370 491, 320 485, 300 480
Z
"""

# Inner brain folds (sulci) for visual texture
BRAIN_FOLDS = """
M 340 200 C 370 180, 400 170, 430 175 C 460 180, 490 190, 510 210
M 350 250 C 380 230, 420 220, 460 225 C 500 230, 540 240, 570 260
M 360 300 C 390 280, 430 270, 470 275 C 510 280, 550 290, 580 310
M 380 350 C 410 330, 450 320, 490 325 C 530 330, 560 340, 590 360
M 400 400 C 430 380, 470 370, 510 375 C 550 380, 580 390, 600 410
M 420 150 C 450 140, 490 145, 530 155 C 570 165, 600 180, 620 200
M 440 130 C 480 120, 520 125, 560 140 C 600 155, 630 175, 650 200
M 460 110 C 500 100, 540 105, 580 120 C 620 135, 650 155, 670 180
"""


def _hex_color(region_id: str, intensity: float) -> str:
    """Convert region color to hex with intensity-based alpha."""
    c = REGION_COLORS[region_id]
    # Shift brightness by intensity
    r = min(255, int(c["r"] * (0.4 + intensity * 0.6)))
    g = min(255, int(c["g"] * (0.4 + intensity * 0.6)))
    b = min(255, int(c["b"] * (0.4 + intensity * 0.6)))
    return f"rgb({r},{g},{b})"


def _build_svg(brain_state: dict[str, Any], sanitize: str = "aggregated") -> str:
    """Build the brain map as a high-quality SVG string."""
    lines: list[str] = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')

    # ── Defs: gradients and filters ──
    lines.append('  <defs>')

    # Background radial gradient (deep teal-slate)
    lines.append('    <radialGradient id="bgGrad" cx="50%" cy="45%" r="65%">')
    lines.append('      <stop offset="0%" stop-color="#0f2030"/>')
    lines.append('      <stop offset="60%" stop-color="#0a1824"/>')
    lines.append('      <stop offset="100%" stop-color="#050d14"/>')
    lines.append('    </radialGradient>')

    # Vignette
    lines.append('    <radialGradient id="vignette" cx="50%" cy="50%" r="70%">')
    lines.append('      <stop offset="35%" stop-color="#0a1824" stop-opacity="0"/>')
    lines.append('      <stop offset="100%" stop-color="#000000" stop-opacity="0.6"/>')
    lines.append('    </radialGradient>')

    # Glow filter for active regions
    lines.append('    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">')
    lines.append('      <feGaussianBlur stdDeviation="4" result="blur"/>')
    lines.append('      <feMerge>')
    lines.append('        <feMergeNode in="blur"/>')
    lines.append('        <feMergeNode in="SourceGraphic"/>')
    lines.append('      </feMerge>')
    lines.append('    </filter>')

    # Soft glow for particles
    lines.append('    <filter id="softGlow" x="-100%" y="-100%" width="300%" height="300%">')
    lines.append('      <feGaussianBlur stdDeviation="2" result="blur"/>')
    lines.append('      <feMerge>')
    lines.append('        <feMergeNode in="blur"/>')
    lines.append('        <feMergeNode in="SourceGraphic"/>')
    lines.append('      </feMerge>')
    lines.append('    </filter>')

    lines.append('  </defs>')

    # ── Clip path: brain silhouette ──
    lines.append(f'  <clipPath id="brainClip"><path d="{BRAIN_SILHOUETTE.strip()}"/></clipPath>')

    # ── Background ──
    lines.append(f'  <rect width="{W}" height="{H}" fill="url(#bgGrad)"/>')
    lines.append(f'  <rect width="{W}" height="{H}" fill="url(#vignette)"/>')

    # ── Brain silhouette fill ──
    lines.append(f'  <path d="{BRAIN_SILHOUETTE.strip()}" fill="#0d1f2e" stroke="#1a3a50" stroke-width="1.5" opacity="0.8"/>')

    # ── Region fills (clipped to brain shape) ──
    lines.append(f'  <g clip-path="url(#brainClip)">')
    for rid, pos in REGION_DEFS.items():
        intensity = 0.05  # default min
        for r in brain_state.get("regions", []):
            if r["id"] == rid:
                intensity = max(0.05, r.get("density", 0.05))
                break

        color = _hex_color(rid, intensity)
        rx = pos["rx"] * (0.4 + intensity * 0.6)
        ry = pos["ry"] * (0.4 + intensity * 0.6)

        # Outer glow (large, transparent)
        lines.append(
            f'  <ellipse cx="{pos["cx"]}" cy="{pos["cy"]}" '
            f'rx="{rx * 1.8}" ry="{ry * 1.8}" '
            f'fill="{color}" opacity="{0.02 * intensity:.3f}" filter="url(#glow)"/>'
        )

        # Mid glow
        lines.append(
            f'  <ellipse cx="{pos["cx"]}" cy="{pos["cy"]}" '
            f'rx="{rx * 1.3}" ry="{ry * 1.3}" '
            f'fill="{color}" opacity="{0.06 * intensity:.3f}" filter="url(#softGlow)"/>'
        )

        # Core ellipse
        lines.append(
            f'  <ellipse cx="{pos["cx"]}" cy="{pos["cy"]}" '
            f'rx="{rx}" ry="{ry}" '
            f'fill="{color}" opacity="{0.12 + intensity * 0.35:.3f}"/>'
        )

        # Inner highlight (lighter center)
        inner_r = max(10, rx * 0.4)
        inner_g = max(10, ry * 0.4)
        lines.append(
            f'  <ellipse cx="{pos["cx"]}" cy="{pos["cy"]}" '
            f'rx="{inner_r}" ry="{inner_g}" '
            f'fill="white" opacity="{0.03 + intensity * 0.08:.3f}"/>'
        )

    # ── Brain folds (sulci) — rendered as subtle lines ──
    lines.append(f'  <path d="{BRAIN_FOLDS.strip()}" fill="none" stroke="#1a3a50" stroke-width="1.0" opacity="0.3"/>')

    # ── Fiber connections (curved, weighted, clipped) ──
    fibers = brain_state.get("fibers", [])
    for fiber in fibers:
        from_r = fiber.get("from_region", "")
        to_r = fiber.get("to_region", "")
        weight = fiber.get("weight", 0.3)
        if from_r in REGION_DEFS and to_r in REGION_DEFS:
            fp = REGION_DEFS[from_r]
            tp = REGION_DEFS[to_r]
            opacity = max(0.03, min(0.35, weight * 0.6))
            # Curved path (quadratic bezier)
            mx = (fp["cx"] + tp["cx"]) / 2
            my = (fp["cy"] + tp["cy"]) / 2
            # Add slight curve
            dx = tp["cx"] - fp["cx"]
            dy = tp["cy"] - fp["cy"]
            cx = mx + dy * 0.15
            cy = my - dx * 0.15
            lines.append(
                f'  <path d="M {fp["cx"]} {fp["cy"]} Q {cx} {cy} {tp["cx"]} {tp["cy"]}" '
                f'stroke="#4a90d9" stroke-width="{0.5 + weight * 1.5}" '
                f'fill="none" opacity="{opacity:.3f}"/>'
            )

    # ── Particle clouds per region (clipped) ──
    import random
    rng = random.Random(42)  # deterministic
    for rid, pos in REGION_DEFS.items():
        intensity = 0.05
        for r in brain_state.get("regions", []):
            if r["id"] == rid:
                intensity = max(0.05, r.get("density", 0.05))
                break

        color = REGION_COLORS[rid]
        # Number of particles proportional to intensity
        n_particles = max(15, int(30 * intensity))
        for _ in range(n_particles):
            # Random point inside ellipse
            angle = rng.uniform(0, 2 * math.pi)
            dist = rng.uniform(0, 1)
            px = pos["cx"] + pos["rx"] * dist * math.cos(angle) * (0.3 + intensity * 0.7)
            py = pos["cy"] + pos["ry"] * dist * math.sin(angle) * (0.3 + intensity * 0.7)
            size = rng.uniform(1, 3) * (0.5 + intensity * 0.5)
            alpha = rng.uniform(0.1, 0.35) * intensity
            lines.append(
                f'  <circle cx="{px:.1f}" cy="{py:.1f}" r="{size:.1f}" '
                f'fill="rgb({color["r"]},{color["g"]},{color["b"]})" '
                f'opacity="{alpha:.3f}"/>'
            )

    lines.append('  </g>')  # end clip group

    # ── Brain outline (on top of everything) ──
    lines.append(f'  <path d="{BRAIN_SILHOUETTE.strip()}" fill="none" stroke="#2a5a7a" stroke-width="1.2" opacity="0.35"/>')

    # ── Title ──
    lines.append(
        f'  <text x="30" y="40" '
        f'font-family="system-ui, -apple-system, sans-serif" '
        f'font-size="13" fill="#94a3b8" letter-spacing="0.2em">'
        f'OMNIGRAPH · BRAIN MAP</text>'
    )

    # ── Subtitle ──
    session_count = brain_state.get("meta", {}).get("sessions", 0)
    entity_count = brain_state.get("meta", {}).get("entities", 0)
    lines.append(
        f'  <text x="30" y="58" '
        f'font-family="JetBrains Mono, ui-monospace, monospace" '
        f'font-size="10" fill="#4a6a8a">'
        f'{session_count} sessions · {entity_count} entities</text>'
    )

    # ── Hypothesis verdict cards (right side) ──
    active = []
    hypotheses = brain_state.get("hypotheses", {})
    for hid, hdata in hypotheses.items():
        if hdata.get("sufficient_data", False):
            active.append(hid)
    active = active[:3]  # max 3

    card_x = 820
    card_y = 30
    card_w = 350
    card_h = 44
    card_gap = 52

    for i, hid in enumerate(active):
        hdata = hypotheses.get(hid, {})
        label = hdata.get("label", hid)
        verdict = hdata.get("verdict", "Insufficient data")
        group = hdata.get("group", "collab")
        group_color = GROUP_COLORS.get(group, "#8b5cf6")
        cy = card_y + i * card_gap

        # Card background (glassmorphism)
        lines.append(
            f'  <rect x="{card_x}" y="{cy}" width="{card_w}" height="{card_h}" '
            f'rx="10" fill="#ffffff" fill-opacity="0.04" '
            f'stroke="#ffffff" stroke-opacity="0.08" stroke-width="0.5"/>'
        )

        # Group indicator line (left edge)
        lines.append(
            f'  <rect x="{card_x}" y="{cy + 8}" width="3" height="{card_h - 16}" '
            f'rx="1.5" fill="{group_color}" opacity="0.6"/>'
        )

        # Label
        lines.append(
            f'  <text x="{card_x + 14}" y="{cy + 19}" '
            f'font-family="system-ui, sans-serif" '
            f'font-size="10" fill="#94a3b8" font-weight="600">'
            f'{label}</text>'
        )

        # Verdict (truncated if too long)
        max_verdict_len = 55
        display_verdict = verdict if len(verdict) <= max_verdict_len else verdict[:max_verdict_len - 2] + ".."
        lines.append(
            f'  <text x="{card_x + 14}" y="{cy + 35}" '
            f'font-family="JetBrains Mono, ui-monospace, monospace" '
            f'font-size="9" fill="#cbd5e1">'
            f'{display_verdict}</text>'
        )

    # ── Metric highlight (bottom-left) ──
    # Show the most interesting metric hook
    hooks = []
    for hid in active:
        hdata = hypotheses.get(hid, {})
        verdict = hdata.get("verdict", "")
        if verdict and len(verdict) < 60:
            hooks.append(verdict)
    if hooks:
        # Show first hook as a highlighted metric
        lines.append(
            f'  <text x="30" y="{H - 35}" '
            f'font-family="JetBrains Mono, ui-monospace, monospace" '
            f'font-size="11" fill="#e2e8f0">'
            f'→ {hooks[0][:55]}</text>'
        )
        lines.append(
            f'  <text x="30" y="{H - 18}" '
            f'font-family="system-ui, sans-serif" '
            f'font-size="9" fill="#4a6a8a">'
            f'Powered by OmniGraph</text>'
        )
    else:
        lines.append(
            f'  <text x="{W - 30}" y="{H - 18}" '
            f'text-anchor="end" '
            f'font-family="system-ui, sans-serif" '
            f'font-size="9" fill="#334155">'
            f'Powered by OmniGraph</text>'
        )

    lines.append('</svg>')
    return "\n".join(lines)


def _svg_to_png(svg_bytes: bytes, output_path: Path) -> None:
    """Convert SVG to PNG using available tools.

    Tries in order:
    1. rsvg-convert (librsvg, fastest)
    2. cairosvg (Python package)
    3. Inkscape (CLI)
    4. Writes SVG with instructions to convert manually
    """
    # Try rsvg-convert
    import subprocess
    for cmd in [["rsvg-convert", "-w", "1200", "-h", "630", "-o", str(output_path)],
                ["inkscape", "--export-type=png", "--export-filename=" + str(output_path), "--export-width=1200", "--export-height=630"]]:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
            if output_path.exists() and output_path.stat().st_size > 1000:
                return
        except (subprocess.SubprocessError, FileNotFoundError):
            continue

    # Try cairosvg
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=svg_bytes, output_width=1200, output_height=630, write_to=str(output_path))
        return
    except ImportError:
        pass

    # Fallback: write SVG and print instructions
    output_path.write_bytes(svg_bytes)
    print(f"⚠️  No SVG→PNG converter available. SVG written to {output_path}")
    print("   Install one of: rsvg-convert, cairosvg, inkscape")
    print("   Or open the SVG in a browser and screenshot it.")


def export_brain_map(
    brain_state: dict[str, Any],
    output_path: str | Path,
    sanitize: str = "aggregated",
    format: str = "png",
) -> Path:
    """Export a brain map image from brain_state JSON.

    Args:
        brain_state: BrainState dict (from build_brain_state.py)
        output_path: Output file path (.svg or .png)
        sanitize: "none" | "named_stripped" | "aggregated"
        format: "svg" | "png" (auto-detected from extension if not specified)

    Returns:
        Path to the output file.
    """
    output_path = Path(output_path)

    # Auto-detect format from extension
    fmt = format or (".svg" if output_path.suffix == ".svg" else "png")

    svg = _build_svg(brain_state, sanitize)

    if fmt == "svg" or output_path.suffix == ".svg":
        output_path.write_text(svg, encoding="utf-8")
        print(f"✅ SVG brain map → {output_path}")
        return output_path

    # PNG: convert from SVG
    svg_bytes = svg.encode("utf-8")
    _svg_to_png(svg_bytes, output_path)
    print(f"✅ PNG brain map → {output_path}")
    return output_path


def export_from_file(
    input_path: str | Path,
    output_path: str | Path,
    sanitize: str = "aggregated",
    format: str = "png",
) -> Path:
    """Export brain map from a brain_state.json file.

    Convenience wrapper: reads JSON, exports image.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"brain_state.json not found: {input_path}")

    brain_state = json.loads(input_path.read_text())
    return export_brain_map(brain_state, output_path, sanitize, format)
