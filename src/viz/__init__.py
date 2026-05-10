"""OmniGraph brain viz — data pipeline + export.

Submodules:
  demo.py       — `omnigraph demo` CLI entry (pipeline → brain_state → image)
  export_image.py — headless screenshot of brain viz (PNG, 1200×630)
  hypotheses.py — 10 diagnostic hypothesis functions
  build_brain_state.py — assemble brain_state.json from vault + events
  sanitize.py   — sanitization (none / named_stripped / aggregated)
"""
