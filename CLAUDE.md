# CLAUDE.md — stellarlib

Agent guide for this repository. Human-facing documentation lives in the
README and the `docs/` tree; consult them before editing:

@README.md

## Project facts

- Science library: unified, provenance-first access to theoretical stellar
  spectral libraries (BOSZ 2024 first, TLUSTY second). See
  `docs/architecture.md` for the two-layer (native/unified) data model.
- All git-tracked content (code, comments, docs, commit messages) is written
  in English.
- Tests: `pytest` from the repo root; network-dependent tests are marked
  `network` and excluded from the default run via `-m "not network"` in CI
  (run them explicitly when needed).
- Test fixtures under `tests/data/` are trimmed excerpts of real upstream
  files (or documented format-equivalent synthetic files); each carries a
  `README.md` describing its derivation. Never hand-edit fixture data.

## Conventions

- Runtime dependencies stay minimal: numpy, astropy, scipy, requests.
- Prefer mature library implementations (numpy/astropy/scipy) over hand-rolled
  algorithms; self-written numerics are a defect, not a feature.
- No silent fallbacks: errors surface (fail clearly), and any data
  transformation must be recorded in provenance objects.
- No data clipping or zeroing: native spectra are preserved verbatim; quirks
  of the published files are recorded in notes, never "fixed" in code.
- The no-transform principle bounds the native layer: transformations the unified layer needs for interpolation (wavelength resampling; aligning the rorig native-sampling spectra onto a shared grid) are permitted as explicit, provenance-recorded derived operations that never rewrite or replace the native files.
- Public API surface is re-exported from `stellarlib/__init__.py`; keep the
  `__all__` list in sync.

## Git

- Autonomous-execution runs in this repository are authorized to stage
  and commit their own completed units (conventional commit messages,
  Co-Authored-By trailer), stated here by the user's own decision. Scope:
  the stellarlib working tree only — never pushes, never history
  rewrites, never other repositories or the database directory.
- If the harness permission layer still declines a commit under this
  authorization, follow its denial: leave the work in the working tree
  and surface the change groups in the run's final report.
