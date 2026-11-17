# Changelog

All notable changes to the stellarlib usage skill. The format follows
keep-a-changelog; one section per round, newest first.

## 2026-11-17

### Added

- Initial release, shipped inside the stellarlib package
  (`python -m stellarlib.skills` prints the skills root): a
  user-facing usage skill covering the library inventory and model
  strings, spectrum queries (exact / multilinear / voronoi nearest,
  MILES nearest-star-only), grid opening from files or a database,
  database building (`dry_run` first, `select=` rounds, `workers=`),
  integrity verification (`verify_database`, the signature cache and
  its migration rule), registration of held files, resampling, and the
  corner/materialize bulk access — plus a per-family reference
  (coverage, flux conventions, pitfalls) and an error-meaning table.
