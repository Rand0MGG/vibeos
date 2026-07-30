# Goal 04C system-service acceptance — 2026-07-22

Goal 04C is implemented on top of independent Goal 04A commit `1cd96da` and
Goal 04B commit `c6c88e3`. The rollback point for this slice is `c6c88e3`.
The files under `docs/goals/agent_native/` and the Goal 03 VM artifacts were
not modified.

## Delivered vertical slice

The only accepted goal is “诊断并恢复 VibeOS 测试用户服务，确认恢复完成”. A
different or materially ambiguous target enters durable clarification before
observation or model invocation. The fixed path is:

```text
GoalContract
  -> bounded systemd user D-Bus facts and fixed-unit journal
  -> persisted D0 context manifest
  -> Model Gateway v1 service_diagnosis
  -> persisted typed proposal
  -> EffectPolicy and existing ToolRegistry
  -> DurableActionExecutor proposal/receipt/evidence
  -> real-state reconciliation when outcome is unknown
  -> bounded independent re-observation
  -> TerminalOutcome
```

The provider accepts only `vibeos-goal04-fixture.service` and `start` or
`restart`. It uses systemd's user-manager D-Bus by default. The optional
fallback is a fixed absolute `/usr/bin/systemctl` or `/usr/bin/journalctl`
argv list; it has no shell, arbitrary unit, root, system bus, or arbitrary
path authority. The internal tool is not added to the 19-capability public
registry.

The fixture uses `Type=notify`. Before a task, the controller stops and resets
the fixed unit, writes a mode-0600 one-shot token, starts it once, and proves
that startup failed with exactly one synthetic log line. The unit can write
only its dedicated state directory while the rest of home remains read-only.
It is deliberately disabled from `default.target`; only the controller and the
Agent may start it. The controller is not called after task start.

## Crash and fault evidence

`tests/test_goal04_system_service.py` covers the golden path, ambiguity, unit
missing, permission denial, journal unavailable, stale facts, locked/unlocked
keyring, provider timeout, schema failure, ineffective recovery and concurrent
dispatch. One ineffective recovery produces one dispatch and an explicit
failed TerminalOutcome; there is no retry loop.

The suite also launches a separate Python worker and terminates it with
`os._exit(97)` at eleven persisted boundaries:

- before and after fact collection;
- after context-manifest commit;
- before and after the model call;
- after typed-proposal commit;
- before the external action;
- after dispatch-proposal commit;
- after the external action but before canonical receipt;
- before independent verification;
- while durably waiting for keyring unlock.

After the one-second test lease expires, a new process resumes the same SQLite
task. Ten cases reach `succeeded` with exactly one provider action and one
canonical receipt. A crash immediately after the dispatch proposal commit has
neither an action nor a receipt; because absence of health cannot prove that a
`restart` call was never initiated, recovery pauses as unknown instead of
redispatching. The post-action crash observes the file-backed real state and
reconciles to one receipt without repeating the action.

```text
python -m pytest -q tests/test_goal04_system_service.py -> 32 passed
python -m pytest -q tests/test_model_gateway_goal04.py  -> 18 passed
python -m pytest -q                                    -> 1063 passed in 103.00s
ruff format --check src tests scripts migrations       -> 200 files already formatted
ruff check src tests scripts                           -> passed
mypy                                                    -> 0 issues in 67 source files
python scripts/architecture_guard.py                    -> ok; 0 violations
```

The Gateway suite supplies the high-entropy leak canary coverage across strict
request/result serialization, subprocess stdout, semantic-worker environment,
transport argv, route persistence, TTY migration output and exceptions. The
04C task persists only D0 facts, a SecretRef URI and the redacted transport
receipt; it never persists a provider credential.

## FedoraLinux-44 WSL evidence

WSL reported a user session bus at `/run/user/1000/bus` and XDG runtime at
`/run/user/1000`; its user manager was `degraded`, and `secret-tool` was not
installed. This environment therefore proves the non-desktop durable kernel
and real systemd user D-Bus slice, not Secret Service or a real provider.

In one user-manager session, the real pre-task controller returned:

```json
{"prepared":true,"initial_start_failed":true,"active_state":"failed","synthetic_failure_lines":1,"controller_must_not_run_after_task_start":true}
```

The Agent then used the real systemd user D-Bus provider with the strict
offline model fixture. It produced one canonical receipt and independently
re-observed:

```json
{"status":"succeeded","receipt_count":1,"active_state":"active","sub_state":"running","healthy_log":true}
```

The TerminalOutcome recorded diagnosis `restart`, current state
`loaded/active/running` with a live PID, five evidence IDs, a positive
completion judgment and no unresolved risk. The bounded verifier waited for
the asynchronous systemd job to leave `activating/start`; it never redispatched
the action.

## Explicitly not accepted here

The controlled real-provider smoke, GNOME Keyring locked/unlocked behavior and
the complete Fedora GNOME VM fixture run are **not run**. The user requested
that VMware verification be skipped for this pass. An SSH attempt reached the
VM but had no accepted public key; no password was placed in argv, environment,
files or tool logs. These gates remain external follow-up evidence and are not
claimed as passing.

## 2026-07-30 review remediation

The non-external findings from the follow-up review were reproduced and fixed:

- secret status uses Secret Service metadata search and never resolves the
  plaintext value in the CLI process;
- model dispatch is persisted before provider I/O. Recovery pauses an
  unresolved dispatch instead of replaying it, while the stable request ID is
  propagated as idempotency and trace headers;
- the specialized Goal 04 resumer enforces `deadline_at`, and each provider
  budget is capped by the remaining task deadline;
- fresh 0006 upgrades write the canonical E0-E4 policy summary, and 0007
  corrects active databases that already contain the original mechanical
  mapping;
- the D-Bus, WSL and VM evidence scripts use schema v2, canonical task receipts,
  `EffectPolicy`, `effect_level` and `effect_policy`.

Verification after remediation:

```text
python -m pytest -q                                    -> 1072 passed in 113.13s
Goal04 real worker-process crash matrix                -> 12 passed
safe/offline collect_vm_evidence.py                    -> overall ok; 0 failed; 0 blocked
ruff check .                                           -> passed
ruff format --check .                                  -> 202 files already formatted
python -m mypy --strict                                -> 0 issues in 67 source files
python scripts/architecture_guard.py                    -> ok; 0 violations
```

The direct `verify_foundation_dbus.py` entry could not be rerun in this WSL
image because `dbus-run-session` is not installed; its v2 request/task-receipt
logic is covered by `tests/test_goal04_acceptance_scripts.py`. The controlled
real-provider smoke, GNOME Keyring locked/unlocked behavior and complete Fedora
GNOME VM fixture remain explicitly **not run**, so Goal 04 is still not claimed
as fully externally accepted.
