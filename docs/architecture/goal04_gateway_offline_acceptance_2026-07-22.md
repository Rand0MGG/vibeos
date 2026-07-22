# Goal 04B offline acceptance — 2026-07-22

Commit under test: working tree after Goal 04A commit `1cd96da`.

## Contract and isolation gates

The dedicated suite exercises Model Gateway v1 with synthetic D0 service facts:

```text
python -m pytest -q tests/test_model_gateway_goal04.py
18 passed
```

Covered gates include strict request/response parsing, deterministic unit/
argument/fact-digest/freshness/effect validation, 429, 5xx, timeout, outer and
inner invalid JSON, schema mismatch, total/token budget exhaustion,
cancellation, unknown delivery, locked keyring durable waiting and matching
unlock resume, leak canary, SecretRef-only route persistence, secret-tool stdin
handling, a real scrubbed semantic subprocess, and a separate real transport
subprocess that fails closed without a credential.

Repository-wide gates after disabling the legacy direct provider transport:

```text
ruff format --check src tests scripts migrations -> 191 files already formatted
ruff check src tests scripts migrations          -> passed
mypy                                             -> 0 issues in 59 source files
python scripts/architecture_guard.py             -> ok; 0 violations
python -m pytest -q                              -> 993 passed in 52.42s
```

## Environment observation

The FedoraLinux-44 WSL environment reported a session bus and XDG runtime
directory, but no `secret-tool`, no configured Gateway route and no
`OPENAI_API_KEY`/`DEEPSEEK_API_KEY` environment name. No credential value was
read or printed.

Consequently the controlled real-provider smoke is **not run**. This document
does not claim it passed. The production adapter and process protocol are
implemented and offline-accepted; a logged-in GNOME environment with a
user-owned SecretRef is still required for that external gate before the final
real-provider-dependent Goal 04 acceptance can be declared complete.
