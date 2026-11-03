# Contributing

Thanks for helping! shotforge is small on purpose: one rendering engine (`shotforge/engine.py`), a palette/rules module,
a copywriter (Claude backends), a CLI and an MCP server.

## Setup

```bash
git clone https://github.com/ibrahimatmaca/shotforge && cd shotforge
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # ~30 s (renders a few real images)
```

## Good first contributions

- **English CLI messages / `--help`** (currently Turkish): introduce a small message catalogue and keep Turkish as a locale.
- **New templates**: copy `shotforge/templates/bubble.json` (2D) or `neon3d.json` (3D). Required keys: `name`, `dimension` (`2d`/`3d`),
  `theme` (`dark`/`light`/`colorful`), `best_for`, `description`, `design_notes`, `palette`, `bg_mode`, `layouts`, `defaults`.
  2D templates must only use `flat`, `peek-flat`, `rotate-l/r`, `duo-flat`; 3D templates `tilt-right/left`, `peek`, `duo` (a test enforces this).
  Use original art direction and original code; do not copy third-party template artwork.
- Windows testing, more device frames, more locales in `--layout asc`.

## Guidelines

- Keep user-provided choices authoritative (colours, counts, texts, template) — Claude/rules only fill gaps.
- Add a test for new behaviour; the Claude backend is tested with a fake `claude` executable (see `tests/test_claude_backend.py`), the OpenAI backend with a fake client (`tests/test_openai_backend.py`).
- Do not add telemetry or network calls other than the user-configured AI backends.
