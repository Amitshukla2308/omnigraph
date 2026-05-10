# Contributing to OmniGraph

Thanks for considering a contribution. OmniGraph is a small, focused project and contributions of any size are welcome — bug reports, doc clarifications, new provider adapters, new compilers, performance fixes.

## Dev setup

Requires Python 3.10+.

```bash
git clone https://github.com/Amitshukla2308/omnigraph.git
cd omnigraph
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If `pip install -e .` complains about a missing `pyproject.toml`, run the CLI directly:

```bash
python src/omnigraph_cli.py status
```

(A proper `pyproject.toml` is on the v0.1 milestone.)

Optional: `cairosvg` for PNG export from the brain-map renderer:

```bash
pip install cairosvg
```

## Workflow

1. Open an issue first for non-trivial changes — describe what + why before writing code.
2. Fork → branch → PR. Branch names: `feat/...`, `fix/...`, `docs/...`, `refactor/...`.
3. Keep PRs focused. One concern per PR.
4. Conventional commit messages preferred (`feat: ...`, `fix: ...`, `docs: ...`).

## Personal-data sweep (REQUIRED before every PR)

OmniGraph reads founder-personal transcripts as **input**. The repo itself must ship **zero** personal data — no real names, no real project names, no real `brain_state.json`, no transcripts.

Before sending a PR:

```bash
# Substitute your own list of names/projects/paths to scrub:
grep -rIn "Your-Name\|Your-Project\|/home/your-username" --exclude-dir=.git --exclude-dir=__pycache__ .
# Must return zero hits.

# Sweep for bare secrets:
grep -rIn 'AKIA[A-Z0-9]\{16\}\|sk-ant-\|ghp_\|gho_\|ghu_\|ghs_\|ghr_' --exclude-dir=.git --exclude-dir=__pycache__ .
# Must return zero hits.

# Defensive filename check:
find . -iname "amit-*" -o -iname "*personal*" -o -iname "*_real*" -o -iname "vault" -type d -o -iname "pilot" -type d
# Must return zero hits.
```

The `.gitignore` is a second line of defense, not the first. Read your diff as a stranger would. If a snippet only makes sense with "the maintainer's specific project" in mind, generalize it.

## What we look for

- The source still parses: `python -m py_compile $(find src -name '*.py')` exits clean.
- The CLI still loads: `python src/omnigraph_cli.py status` runs without import errors.
- The 3-layer brain split (global / personal / project) is preserved — don't reintroduce a unified `brain.xml`.
- Schema changes ship with a migration path in `src/migrate.py` and a bump in `docs/SCHEMA.md`.
- No new bundled secrets, no real user data in fixtures, no hard-coded absolute paths under `/home/<user>` or `/Users/<user>` — use `Path.home()` or env-var overrides.

## Code of Conduct

Read [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md). It's enforced.

## License

By contributing, you agree your changes are licensed under Apache 2.0 (the project's license). No CLA required.
