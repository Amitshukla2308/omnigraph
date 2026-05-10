"""6-lens synthesis prompts for `omnigraph reflect`.

Each lens is a short Qwen prompt that reads already-extracted per-session
objects (mental_moves / decisions / concerns / rules / drifts / mention_events)
and produces a focused ~150-200 word perspective on the session from that
viewpoint.

Composition into a single markdown file is the responsibility of
`src/reflect.py`. The lenses themselves are pure prompt templates.

Order (locked, matches Atelier VISION.md):
  1. Engineer
  2. Architect
  3. Strategist
  4. Economist
  5. Scientist
  6. Product
"""
from .six_lens_prompts import LENSES, lens_prompt, render_session_brief

__all__ = ["LENSES", "lens_prompt", "render_session_brief"]
