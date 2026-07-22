# Goal 04 migration and rollback

Last updated: 2026-07-22.

Revision `0006_effect_contract_v2` is an additive, JSON-aware migration from the frozen `0005_persist_dry_run_intent` head. It upgrades only nonterminal task families. Terminal v1 task, event, plan, receipt and evidence data is immutable and must be inspected through the explicit historical readers; it must never be resumed.

Before upgrade, stop all writers and preserve the artifact commit together with the SQLite database, WAL and SHM files. `CoreDatabase.upgrade()` creates and restores a same-directory pre-migration snapshot if Alembic fails. The supported rollback is the pair “pre-04A artifact plus pre-0006 database snapshot”. Do not point a pre-04A artifact at a v2 database or a Goal04 artifact at an unupgraded database.

After upgrade, verify:

- Alembic head is `0006_effect_contract_v2` and `CoreDatabase.health()` is ready;
- nonterminal task-family rows and JSON wrappers are v2;
- typed steps use E0-E4, observations use O0-O2, and no resumable v1 task remains;
- ambiguous unbound effect records are `paused` with a manual-disposition reason;
- terminal v1 payload bytes and schema markers are unchanged;
- `python scripts/architecture_guard.py`, Ruff, Mypy and the migration/canonical-result tests pass.

The migration downgrade intentionally does not synthesize live v1 data. Restore the paired snapshot instead.
