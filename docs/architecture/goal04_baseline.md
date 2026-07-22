# Goal 04 execution baseline

Recorded on 2026-07-22 before production implementation.

## Git and ownership

- Implementation branch: `codex/goal04-execution-foundation`
- Planning baseline commit: `efe22679faaae38227f2614c229ff5f958b6822f`
- Goal 03 merge on `origin/main`: `c9b7ca6a58d53c1b4127b7558373aceb6f607e72`
- Concurrent worktrees at baseline were the clean historical Goal 01 detached
  worktree and the clean `codex/goal03-reconciliation` worktree.
- The 13 files under `.codex_vm_artifacts` are tracked Goal 03 evidence.
- `docs/goals/agent_native` is an immutable input for this implementation. Its
  files must retain the SHA-256 digests recorded below.

## Protected plan digests

| File | SHA-256 |
| --- | --- |
| `00_alignment_and_code_baseline.md` | `4c7f720e7d2efe1bc56d3a0db58a36a12d1bd45810cdb16a148c31bf20b278f6` |
| `01_core_foundation_replacement.md` | `d2e346fcbb5ee93ff4837c659595ecb2f5feaae0fe75f022865719ebc94c86fd` |
| `02_durable_task_engine.md` | `ebdcf472337ad63fd532069e21d9cd9c8bcfe704fbdab32863f5eed2839a9a2b` |
| `03_reconcile_goal01_goal02.md` | `aeb0e2393f35b035888a6d9498142f3b497d05f0907dccc50b106c6d3328839e` |
| `04_core_execution_foundation_and_system_service_slice.md` | `055c478a52fce1e32bee0121a052142220df2fe5e7bc2aeeb21834802cf0a7fd` |
| `05_model_gateway_and_secret_broker.md` | `f70413a4fb822d7243d2270f59d5096f58a88f130747f8e68acdf844f868aaaa` |
| `06_unprivileged_tasks_and_installable_runtime.md` | `7487bf8ae11361405bfc9604a3e80b46fa47aa244894181eee46e4d18565b782` |
| `07_gnome_mixed_task_mvp.md` | `66abd11a10f9c0c7af7aff68edbf208dbcd46f9fded2c21076a731dfacb80a18` |
| `08_privileged_canary_and_rollback.md` | `648ee6b0350e422b8bfe45ab01849b91206a463a74228c2769b33a3e5d10db64` |
| `09_proactive_service_advisor.md` | `0b0ce33bc2062f67c9e02876e32d16c0cfac5ba796c0c305e3953314c58fce6b` |
| `10_runtime_release_lifecycle.md` | `45f5b09637323498fa5ad639fa512347080eea1bd6618aac50448424221e3840` |
| `11_readonly_extension_and_distro_gate.md` | `bdb51889d63d373c43f98485d384671162ddfab54bd2c90885eaa47330f79387` |
| `README.md` | `9db5cadada0ae27646d50a70fde67eb4223811fdd982be18d51f97c4806c995b` |

## Implementation baseline

- Alembic head: `0005_persist_dry_run_intent`.
- One production task path is composed through `DurableTaskEngine` and
  `SqliteTaskRepository`.
- The live capability, plan, review, and public transport contracts still use
  `risk_level` and effect `L0-L3`.
- Observation depth separately uses `L0-L2`.
- The Goal 01 foundation slice persists an inner action receipt/evidence while
  the durable executor persists the canonical outer result.
- Eight semantic modules make direct `request_json_object` calls.
- Provider credentials can be loaded from `.env` or ordinary environment
  variables, and the current session installer injects that environment into
  `vibed.service`.
- No governed systemd user-service fact/action provider exists.

## Phase gates

Goal 04 implementation proceeds only through the recorded sequence:

1. 04A: effect, observation, v2 live data, and canonical action-result
   convergence.
2. 04B: inheritable Gateway v1, SecretRef v1, and isolated provider transport.
3. 04C: systemd fixture, crash recovery, and real-environment evidence.

Each phase requires its own validation record, logical commit, and rollback
point. A later phase cannot be used to excuse an incomplete earlier gate.
