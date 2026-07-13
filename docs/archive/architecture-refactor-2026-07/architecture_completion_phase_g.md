# Architecture Completion - Phase G Static Quality and Final Verification

Captured: 2026-07-12

This document records the Phase G configuration and evidence status required by
[`architecture_completion_master_goal.md`](../../architecture_completion_master_goal.md).
The master contract itself remains unchanged.

## Static quality configuration

`pyproject.toml` now declares Ruff and mypy in the `dev` extra. Ruff is enabled
for undefined and unused-code diagnostics and has a repository-wide format
check. The D-Bus signature annotations are intentionally excluded from Ruff's
undefined-name diagnostic because those string annotations are consumed by the
D-Bus decorator rather than evaluated as Python names.

Mypy has a documented strict scope covering the refactored architecture:

```text
command service and task application boundary
GoalLoop and GoalLoop ports
planning service and loop snapshot codec
registered execution service and tool registry
review service, review resume, historical conversion, and ReviewStore
acceptance and recovery services
result projection and composition root
```

Imports outside this scope are skipped during the initial migration so that
unrelated historical modules cannot obscure type errors in the new task
architecture. The scope itself is checked with `--strict` and is expanded only
after it is green.

## CI configuration

[`.github/workflows/test.yml`](../../../.github/workflows/test.yml) now defines:

* `static-quality`: Ruff lint, Ruff format check, and scoped strict mypy;
* `deterministic-tests`: pytest, capability JSON, and offline dry-run smoke
  tests.

Both jobs use Python 3.11 and avoid model credentials, GUI sessions, D-Bus,
and real desktop adapters. No remote workflow result has been observed.

## Completed local verification

Run in the documented Fedora 44 WSL environment:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy --strict
python -m pytest -q
vibe capabilities --json
vibe ask "search web for hello" --json --offline --dry-run
```

All commands were executed in the documented Fedora 44 WSL environment as
user `rand0mg`, with
`/home/rand0mg/.venvs/vibeos/bin/activate` active:

```text
python -m ruff check src tests          -> All checks passed
python -m ruff format --check src tests -> 122 files already formatted
python -m mypy --strict                 -> Success: no issues found in 16 source files
python -m pytest -q                     -> 263 passed in 11.90s
vibe capabilities --json                -> exit 0; 19 registered capabilities
vibe ask "search web for hello" --json --offline --dry-run
                                         -> exit 0; status=dry_run
```

`vibe doctor --json` also completed with zero failures. Its warnings are
expected for WSL without a GNOME session or desktop services (GNOME Shell,
portal, daemon, desktop-app registry, and desktop action helpers). Those are
explicitly deferred to real GNOME Wayland verification and are not masked as
passing desktop integration.

The complete phase, architecture, execution, review-state, and deferred-GNOME
handoff is recorded in
[`architecture_completion_final_audit.md`](../../architecture_completion_final_audit.md).
