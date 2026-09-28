# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is `0.x`, a minor bump may
change behavior; the entry will say so.

## [Unreleased]

### Fixed
- `--streets` now covers the whole street: parcels come from the address list **and** the asset gazetteer (same
  city only), so buildings without address coordinates are no longer missed.
- `--streets` keeps only flats addressed on the requested streets. Parcels touching a street can hold large
  neighboring projects addressed elsewhere; those deals are left out and counted in a report note.

## [0.2.0] - 2026-09-28

### Added
- Reports are saved to `reports/<city>-<area>-<YYYY-MM-DD>.html` under the directory you run from. The folder is
  created when needed, and an existing report is never overwritten (`-2`, `-3` … is appended).
- `--slug` to set the file-name part (e.g. `tel-aviv-kochav-hatzafon`). Without it, the name is built from
  English names of the largest cities plus a transliteration of the area.
- Versioning: `--version` on `over_mcp.py` and `over_report.py`, `metadata.version` in `SKILL.md`, the generator
  version in every report's header, and this changelog.

### Changed
- **Breaking:** the default output path moved from `./nadlan-report-<area>-<YYYYMMDD>.html` to the `reports/` folder
  above. Pass `--out` to keep writing to a fixed path.

## [0.1.1] - 2026-09-28

### Fixed
- Network timeouts and connection failures are reported as a one-line error instead of a Python traceback.

### Added
- README: one-command install with the [skills CLI](https://github.com/vercel-labs/skills).

## [0.1.0] - 2026-09-28

### Added
- First public release of the `over-nadlan-deals` agent skill for the over.org.il MCP servers (deals, nadlan, data).
- `over_mcp.py`: a standard-library MCP client with Google sign-in (OAuth 2.1 + PKCE), automatic token
  refresh and thread-safe calls.
- `over_report.py`: resolves a neighborhood (Survey of Israel polygons or address-list labels), streets, parcels or a
  whole city to gush/helka, fetches the matching deals, adds streets and parcel geometry, and compares with the city.
- Interactive HTML report: a gush/helka parcel map with one dot per purchased apartment, cross-filtering charts,
  breakdown and deals tables, and CSV export.
- Room counts such as 4.5 and 5.5, which the deals API returns as empty, are restored from the register.
- Install guides for Claude Code, Claude Desktop, Codex, omp, pi, Hermes Agent and OpenClaw.

[Unreleased]: https://github.com/aviv4339/nadlan/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/aviv4339/nadlan/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/aviv4339/nadlan/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/aviv4339/nadlan/releases/tag/v0.1.0
