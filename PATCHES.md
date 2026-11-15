# kerihost patch queue

Branch `kerihost` = upstream WebOfTrust/keripy main @ b7cd5429be5e3e701eb8c5ee8925566b8162ebf5
plus the commits below, in order. Rules: one commit per patch, subject prefixed
`kerihost-patch:`, a test in tests/test_kerihost_patches.py that fails on plain
upstream, never edit MIGRATIONS. Drop a patch when upstream fixes it.
Design: ~/code/KERI/.internal/specs/2026-11-14-keripy-extension-package-design.md §4

| # | Commit subject | Site | Test | Upstream PR |
|---|---|---|---|---|
| P1 | kerihost-patch: P1 add wss to Schemes | src/keri/kering.py:374-375 | test_schemes_include_wss, test_wss_loc_scheme_accepted | not filed |
| P2 | kerihost-patch: P2 move kramTSGS to subkey ktsg. | src/keri/db/basing.py:1378; tests/db/test_basing.py:1919,2032 (ceiling 109->110) | test_kram_tsgs_does_not_alias_tsgs | not filed |

Note: ktsg. was also used by the pre-kerihost fork; it must not be used as a fork-era vault marker (spec §5.5).
